import asyncio
import io
import sys
import time
import wave
from contextlib import aclosing

import httpx
from google import genai
from google.genai import errors, types

from config.settings import GEMINI_MODELS
from core.provider_contracts import GenerationRequest, ProviderError, ProviderErrorCode
from core.provider_routing import TRANSCRIPTION_INSTRUCTION, TRANSCRIPTION_PROMPT
from providers.gemini_catalog import GEMINI_MODEL_CAPABILITIES, validate_model


class GenerationError(ProviderError):
    def __init__(self, message, code=ProviderErrorCode.PROVIDER_ERROR):
        super().__init__(code, message)


class GeminiClient:
    def __init__(self, settings, model=None):
        self.settings = settings
        self.configuration = settings.groups.provider
        self.model = model if model is not None else self.configuration.model
        self.models = tuple(dict.fromkeys((self.model, *GEMINI_MODELS)))
        self.model_catalog = GEMINI_MODEL_CAPABILITIES

    async def _generate(
        self,
        data,
        mime_type,
        prompt,
        publish,
        history,
        instruction,
        transcription=False,
    ):
        request = GenerationRequest(data, mime_type, prompt, history, instruction)
        candidates = []
        for model in self.models:
            try:
                validate_model(
                    self.model_catalog, model, request, transcription=transcription
                )
            except ProviderError:
                continue
            candidates.append(model)
        if not candidates:
            raise GenerationError(
                "Нет совместимых резервных моделей Gemini.",
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
            )
        options = types.HttpOptions(
            timeout=int(self.configuration.request_timeout * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        )
        client = genai.Client(
            vertexai=False,
            api_key=self.configuration.api_key.get_secret_value().strip(),
            http_options=options,
        )
        try:
            async with client.aio as api:
                last = "Модель не вернула ответ."
                last_code = ProviderErrorCode.PROVIDER_ERROR
                for index, model in enumerate(candidates):
                    print(f"[metrics] api_attempt={index + 1}", file=sys.stderr)
                    if index:
                        publish("")
                        await asyncio.sleep(0.4 * 2 ** (index - 1))
                    try:
                        return await asyncio.wait_for(
                            self._stream(
                                api,
                                model,
                                data,
                                mime_type,
                                prompt,
                                publish,
                                history,
                                instruction,
                            ),
                            self.configuration.request_timeout,
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
                                "Gemini отказал в доступе. Проверьте API-ключ.",
                                ProviderErrorCode.AUTH_FAILED,
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
                        last_code = {
                            404: ProviderErrorCode.MODEL_UNAVAILABLE,
                            429: ProviderErrorCode.RATE_LIMIT,
                            408: ProviderErrorCode.TIMEOUT,
                            504: ProviderErrorCode.TIMEOUT,
                        }.get(code, ProviderErrorCode.PROVIDER_ERROR)
                    except (asyncio.TimeoutError, httpx.TimeoutException):
                        last = "Истекло время ожидания Gemini."
                        last_code = ProviderErrorCode.TIMEOUT
                    except httpx.TransportError:
                        last = "Не удалось подключиться к Gemini."
                        last_code = ProviderErrorCode.NETWORK_ERROR
                    except GenerationError as exc:
                        last = str(exc)
                        last_code = exc.code
                raise GenerationError(last, last_code)
        finally:
            client.close()

    async def _stream(
        self, api, model, data, mime_type, prompt, publish, history, instruction
    ):
        if instruction is None:
            instruction = (
                "Ответь на вопрос из аудио."
                if mime_type == "audio/wav"
                else "Ответь на вопрос с изображения."
            )
        contents = []
        for item in history:
            contents.extend(
                [
                    types.Content(
                        role="user", parts=[types.Part.from_text(text=item.question)]
                    ),
                    types.Content(
                        role="model", parts=[types.Part.from_text(text=item.answer)]
                    ),
                ]
            )
        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=data.decode("utf-8"))
                    if mime_type == "text/plain"
                    else types.Part.from_bytes(data=data, mime_type=mime_type),
                    types.Part.from_text(text=instruction),
                ],
            )
        )
        started = time.monotonic()
        stream = await api.models.generate_content_stream(
            model=model,
            contents=contents,
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
                    if not answer:
                        print(
                            f"[metrics] first_token_ms={(time.monotonic() - started) * 1000:.0f}",
                            file=sys.stderr,
                        )
                    answer += chunk.text
                    publish(answer)
        if not answer.strip():
            raise GenerationError("Gemini вернул пустой ответ.")
        if finish != types.FinishReason.STOP:
            raise GenerationError("Gemini завершил генерацию без полного ответа.")
        return answer

    def generate(
        self,
        data,
        mime_type,
        prompt,
        publish,
        cancel,
        *,
        budget=None,
        history=(),
        instruction=None,
        transcription=False,
    ):
        async def run():
            if cancel.is_set():
                return None
            timeout = self.settings.task_timeout if budget is None else budget
            if timeout <= 0:
                raise GenerationError(
                    "Превышено общее время обработки.", ProviderErrorCode.TIMEOUT
                )
            task = asyncio.create_task(
                self._generate(
                    data,
                    mime_type,
                    prompt,
                    publish,
                    history,
                    instruction,
                    transcription,
                )
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            try:
                while not cancel.is_set():
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise GenerationError(
                            "Превышено общее время обработки Gemini.",
                            ProviderErrorCode.TIMEOUT,
                        )
                    done, _ = await asyncio.wait((task,), timeout=min(remaining, 0.05))
                    if done:
                        return task.result()
                return None
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        return asyncio.run(run())

    def transcribe(self, data, cancel, *, budget=None):
        try:
            with wave.open(io.BytesIO(data), "rb") as audio:
                if audio.getnframes() <= 0 or audio.getsampwidth() != 2:
                    raise ValueError("empty or unsupported audio")
        except (wave.Error, EOFError, ValueError):
            raise GenerationError("Некорректная или пустая аудиозапись.") from None
        return self.generate(
            data,
            "audio/wav",
            TRANSCRIPTION_PROMPT,
            lambda _: None,
            cancel,
            budget=budget,
            instruction=TRANSCRIPTION_INSTRUCTION,
            transcription=True,
        )
