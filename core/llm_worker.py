import io
import time
import threading
import mss
import httpx
from PIL import Image
from google import genai
from google.genai import types
from config.settings import settings


class LLMWorker:
    def __init__(self, context_manager):
        self.ctx = context_manager
        
        # Инициализируем клиент официального SDK через защищенный SecretStr
        self.client = genai.Client(
            api_key=settings.gemini_api_key.get_secret_value()
        )
        
        # Пул моделей с автоматическим переключением при сбоях 503 / квотах
        self.models_pool = [
            settings.gemini_model,
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash"
        ]
        # Очищаем дубликаты с сохранением порядка
        self.models_pool = list(dict.fromkeys(self.models_pool))

        # Фоновый прогрев TLS-сессии при запуске
        self._warmup_connection()

    def _warmup_connection(self):
        """Прогревает сетевой сокет и TLS-сессию в фоне, срезая задержку первого запроса."""
        def _warm():
            try:
                self.client.models.generate_content(
                    model=settings.gemini_model,
                    contents="ping",
                    config=types.GenerateContentConfig(max_output_tokens=1)
                )
                print("[INIT] Сессия с Gemini API успешно прогрета.")
            except Exception as e:
                print(f"[INIT] Фоновый прогрев пропущен/ошибка: {e}")

        threading.Thread(target=_warm, daemon=True).start()

    def capture_screen_ram(self, bbox: dict = None) -> bytes:
        """Снимает либо указанную область (bbox), либо весь основной экран."""
        t0 = time.time()
        with mss.mss() as sct:
            # Если передана область — грабим ровно её, иначе полный монитор
            grab_area = bbox if bbox else sct.monitors[1]
            raw_shot = sct.grab(grab_area)
            
            img = Image.frombytes("RGB", raw_shot.size, raw_shot.bgra, "raw", "BGRX")
            
            # Для фрагмента ресайз не нужен, чтобы сохранить четкость шрифтов!
            if not bbox:
                img.thumbnail((settings.crop_width, settings.crop_height), Image.Resampling.BILINEAR)
            
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75, optimize=False)
            jpeg_bytes = buf.getvalue()
            buf.close()
            
        area_type = f"ROI {bbox['width']}x{bbox['height']}" if bbox else "FULL SCREEN"
        print(f"[PERF] Снимок ({area_type}): {(time.time() - t0) * 1000:.1f} мс, вес: {len(jpeg_bytes) // 1024} КБ")
        return jpeg_bytes

    def analyze_screen_stream(self, on_chunk_callback, bbox: dict = None):
        """Принимает опциональный bbox выделенной области."""
        t_start = time.time()
        image_bytes = self.capture_screen_ram(bbox=bbox)
        sys_prompt = self.ctx.get_system_prompt()
        
        image_part = types.Part.from_bytes(
            data=image_bytes, 
            mime_type="image/jpeg"
        )

        last_error = None
        for model_name in self.models_pool:
            for attempt in range(2):
                try:
                    # Жесткое ограничение токенов для мгновенного ответа
                    config = types.GenerateContentConfig(
                        system_instruction=sys_prompt,
                        temperature=0.0,
                        max_output_tokens=120 if self.ctx.mode == "TEST" else 350,
                    )

                    response_stream = self.client.models.generate_content_stream(
                        model=model_name,
                        contents=[
                            image_part, 
                            "Считай экран и дай ответ СТРОГО по шаблону 'Вопрос: ...\\nОтвет: ...'."
                        ],
                        config=config
                    )
                    accumulated = ""
                    first_chunk = True
                    for chunk in response_stream:
                        if chunk.text:
                            if first_chunk:
                                print(f"[PERF] Первый токен получен через: {time.time() - t_start:.2f} сек (модель: {model_name})")
                                first_chunk = False
                            accumulated += chunk.text
                            on_chunk_callback(accumulated)

                    print(f"[PERF] Полный ответ сформирован за: {time.time() - t_start:.2f} сек")
                    return  # Успешно отработано

                except Exception as e:
                    last_error = e
                    err_str = str(e)
                    # Если кратковременная ошибка перегрузки 503 — делаем секундную паузу
                    if "503" in err_str and attempt == 0:
                        time.sleep(1.0)
                        continue
                    print(f"[WARN] Модель {model_name} недоступна ({type(e).__name__}), проверяю следующую...")
                    break

        err_msg = f"[Ошибка API]: {last_error}"
        print(err_msg)
        on_chunk_callback(err_msg)