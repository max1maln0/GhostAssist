import io
import mss
from PIL import Image
from google import genai
from google.genai import types
from config.settings import settings


class LLMWorker:
    def __init__(self, context_manager):
        self.ctx = context_manager
        # Инициализация клиента через валидированный секрет
        self.client = genai.Client(
            api_key=settings.gemini_api_key.get_secret_value()
        )

    def capture_screen_ram(self) -> bytes:
        """Снимает кадр экрана напрямую в память без сохранения на диск."""
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            raw_shot = sct.grab(monitor)
            
            img = Image.frombytes("RGB", raw_shot.size, raw_shot.bgra, "raw", "BGRX")
            img.thumbnail((settings.crop_width, settings.crop_height))
            
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75)
            jpeg_bytes = buf.getvalue()
            buf.close()
            return jpeg_bytes

    def analyze_screen_stream(self, on_chunk_callback):
        """Отправляет сжатый кадр в Gemini и передает ответ потоком в интерфейс."""
        try:
            image_bytes = self.capture_screen_ram()
            sys_prompt = self.ctx.get_system_prompt()
            
            image_part = types.Part.from_bytes(
                data=image_bytes, 
                mime_type="image/jpeg"
            )
            
            response_stream = self.client.models.generate_content_stream(
                model=settings.gemini_model,
                contents=[
                    image_part, 
                    "Проанализируй изображение и реши задачу согласно инструкции."
                ],
                config=types.GenerateContentConfig(
                    system_instruction=sys_prompt,
                    temperature=0.1,
                    max_output_tokens=300 if self.ctx.mode == "INTERVIEW" else 70,
                )
            )

            accumulated_text = ""
            for chunk in response_stream:
                if chunk.text:
                    accumulated_text += chunk.text
                    on_chunk_callback(accumulated_text)

        except Exception as e:
            err_msg = f"[Ошибка API: {type(e).__name__}] {e}"
            print(err_msg)
            on_chunk_callback(err_msg)