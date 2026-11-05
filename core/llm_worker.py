import asyncio
import io
from contextlib import aclosing

import httpx
import mss
from google import genai
from google.genai import errors, types
from PIL import Image

from config.settings import GEMINI_MODELS


class GenerationError(RuntimeError):
    pass


class LLMWorker:
    def __init__(self, settings):
        self.settings = settings
        self.models = tuple(dict.fromkeys((settings.gemini_model, *GEMINI_MODELS)))

    def capture_screen(self, bbox=None):
        with mss.mss() as capture:
            area = bbox or next(
                (
                    monitor
                    for monitor in capture.monitors[1:]
                    if monitor["left"] == monitor["top"] == 0
                ),
                capture.monitors[1],
            )
            if area["width"] * area["height"] > 40_000_000:
                raise GenerationError("Выберите меньшую область экрана.")
            shot = capture.grab(area)
            image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if bbox is None:
            image.thumbnail((self.settings.crop_width, self.settings.crop_height))
        with io.BytesIO() as buffer:
            image.save(buffer, format="JPEG", quality=75)
            return buffer.getvalue()

    async def _generate(self, data, mime_type, prompt, publish):
        options = types.HttpOptions(
            timeout=int(self.settings.request_timeout * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        )
        client = genai.Client(
            vertexai=False,
            api_key=self.settings.gemini_api_key.get_secret_value().strip(),
            http_options=options,
        )
        try:
            async with client.aio as api:
                last = "Модель не вернула ответ."
                for index, model in enumerate(self.models):
                    if index:
                        publish("")
                        await asyncio.sleep(0.4 * 2 ** (index - 1))
                    try:
                        return await asyncio.wait_for(
                            self._stream(api, model, data, mime_type, prompt, publish),
                            self.settings.request_timeout,
                        )
                    except errors.APIError as exc:
                        code = exc.code
                        details = exc.details
                        info = (
                            details.get("error", {})
                            if isinstance(details, dict)
                            else {}
                        )
                        reasons = (
                            info.get("details") if isinstance(info, dict) else None
                        )
                        invalid_key = isinstance(reasons, list) and any(
                            isinstance(item, dict)
                            and item.get("reason") == "API_KEY_INVALID"
                            for item in reasons
                        )
                        if code in (401, 403) or invalid_key:
                            raise GenerationError(
                                "Gemini отказал в доступе. Проверьте API-ключ."
                            ) from None
                        if code not in (404, 408, 429) and not 500 <= code < 600:
                            raise GenerationError(
                                f"Gemini отклонил запрос (HTTP {code})."
                            ) from None
                        last = {
                            404: "Модель Gemini недоступна.",
                            429: "Достигнут лимит запросов Gemini.",
                            408: "Истекло время ожидания Gemini.",
                            504: "Истекло время ожидания Gemini.",
                        }.get(code, "Временный сбой Gemini.")
                    except (asyncio.TimeoutError, httpx.TimeoutException):
                        last = "Истекло время ожидания Gemini."
                    except httpx.TransportError:
                        last = "Не удалось подключиться к Gemini."
                    except GenerationError as exc:
                        last = str(exc)
                raise GenerationError(last)
        finally:
            client.close()

    async def _stream(self, api, model, data, mime_type, prompt, publish):
        instruction = (
            "Ответь на вопрос из аудио."
            if mime_type == "audio/wav"
            else "Ответь на вопрос с изображения."
        )
        stream = await api.models.generate_content_stream(
            model=model,
            contents=[
                types.Part.from_bytes(data=data, mime_type=mime_type),
                instruction,
            ],
            config=types.GenerateContentConfig(
                system_instruction=prompt,
                temperature=0.2,
                max_output_tokens=1024,
                thinking_config=types.ThinkingConfig(thinking_level="MINIMAL")
                if model in GEMINI_MODELS
                else None,
            ),
        )
        answer = ""
        finish = None
        async with aclosing(stream):
            async for chunk in stream:
                if chunk.candidates and chunk.candidates[0].finish_reason:
                    finish = chunk.candidates[0].finish_reason
                if chunk.text:
                    answer += chunk.text
                    publish(answer)
        if not answer.strip():
            raise GenerationError("Gemini вернул пустой ответ.")
        if finish != types.FinishReason.STOP:
            raise GenerationError("Gemini завершил генерацию без полного ответа.")
        return answer

    def generate(self, data, mime_type, prompt, publish, cancel, *, budget=None):
        async def run():
            if cancel.is_set():
                return None
            timeout = self.settings.task_timeout if budget is None else budget
            if timeout <= 0:
                raise GenerationError("Превышено общее время обработки.")
            task = asyncio.create_task(self._generate(data, mime_type, prompt, publish))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            try:
                while not cancel.is_set():
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise GenerationError("Превышено общее время обработки Gemini.")
                    done, _ = await asyncio.wait((task,), timeout=min(remaining, 0.05))
                    if done:
                        return task.result()
                return None
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        return asyncio.run(run())
