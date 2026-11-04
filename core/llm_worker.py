import io
import mss
from PIL import Image
from google import genai
from google.genai import types
from config.settings import settings

class LLMWorker:
    def __init__(self, context_manager):
        self.ctx = context_manager
        # Передаем распечатанный секрет только внутрь инициализатора SDK
        self.client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())

    def capture_screen_ram(self) -> bytes:
        """Снимает скриншот напрямую в оперативную память (Zero-Disk Footprint)."""
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # Основной монитор
            raw_shot = sct.grab(monitor)
            img = Image.frombytes("RGB", raw_shot.size, raw_shot.bgra, "raw", "BGRX")
            img.thumbnail((settings.crop_width, settings.crop_height))
            
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75)
            jpeg_bytes = buf.getvalue()
            buf.close()
            return jpeg_bytes

    def analyze_screen_stream(self, on_chunk_callback):
        """Отправляет скриншот из RAM и стримит ответ по кускам в UI."""
        try:
            image_bytes = self.capture_screen_ram()
            sys_prompt = self.ctx.get_system_prompt()
            
            image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
            
            response_stream = self.client.models.generate_content_stream(
                model=settings.gemini_model,
                contents=[image_part, "Проанализируй экран и дай ответ по инструкции."],
                config=types.GenerateContentConfig(
                    system_instruction=sys_prompt,
                    temperature=0.1,
                    max_output_tokens=250 if self.ctx.mode == "INTERVIEW" else 60,
                )
            )
            
            full_text = ""
            for chunk in response_stream:
                if chunk.text:
                    full_text += chunk.text
                    on_chunk_callback(full_text)
                    
        except Exception as e:
            # Скрываем детали внутренних ошибок/ключей, выводим только тип сбоя
            on_chunk_callback(f"[Ошибка запроса: {type(e).__name__}]")