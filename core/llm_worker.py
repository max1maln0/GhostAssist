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

    def capture_screen_ram(self) -> bytes:
        """Снимает кадр в RAM, быстро сжимает в JPEG без сохранения на диск."""
        t0 = time.time()
        with mss.mss() as sct:
            # monitors[1] — основной рабочий экран
            monitor = sct.monitors[1]
            raw_shot = sct.grab(monitor)
            
            img = Image.frombytes("RGB", raw_shot.size, raw_shot.bgra, "raw", "BGRX")
            
            # Быстрый билинейный ресайз экономит процессорные миллисекунды
            img.thumbnail((settings.crop_width, settings.crop_height), Image.Resampling.BILINEAR)
            
            buf = io.BytesIO()
            # quality=65 дает отличную читаемость текста при весе кадра ~40-60 КБ
            img.save(buf, format="JPEG", quality=65, optimize=False)
            jpeg_bytes = buf.getvalue()
            buf.close()
            
        print(f"[PERF] Захват и сжатие в RAM: {(time.time() - t0) * 1000:.1f} мс, размер: {len(jpeg_bytes) // 1024} КБ")
        return jpeg_bytes

    def analyze_screen_stream(self, on_chunk_callback):
        """Отправляет сжатый скриншот в Gemini и стримит ответ в оверлей."""
        t_start = time.time()
        image_bytes = self.capture_screen_ram()
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
                            "Вопрос на экране. Дай только букву правильного ответа и короткий факт."
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