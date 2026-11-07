"""Responses and file transcription through the official async OpenAI SDK."""

import asyncio
import base64
import io
import logging
import wave

import httpx
import openai

from core.contracts import ProviderCapabilities
from core.provider_contracts import (
    GenerationRequest,
    GenerationResult,
    ProviderError,
    ProviderErrorCode,
    TranscriptionResult,
)
from core.provider_routing import TRANSCRIPTION_PROMPT
from providers.openai_catalog import OPENAI_MODEL_CAPABILITIES

# SDK verbose logging includes serialized private request bodies. Even an
# inherited OPENAI_LOG=debug must not enable content diagnostics in this app.
logging.getLogger("openai").setLevel(logging.CRITICAL + 1)


class _Failure(ProviderError):
    def __init__(self, code, message, retryable=False):
        super().__init__(code, message)
        self.retryable = retryable


def _status_error(status, code=None):
    if status in (401, 403) or code in ("invalid_api_key", "permission_denied"):
        return _Failure(
            ProviderErrorCode.AUTH_FAILED,
            "OpenAI отказал в доступе. Проверьте ключ и права.",
        )
    if status == 429 or code in ("rate_limit_exceeded", "insufficient_quota"):
        return _Failure(
            ProviderErrorCode.RATE_LIMIT,
            "Достигнут лимит или исчерпана квота OpenAI.",
            code not in ("insufficient_quota", "billing_hard_limit_reached"),
        )
    if status == 404 or code == "model_not_found":
        return _Failure(
            ProviderErrorCode.MODEL_UNAVAILABLE, "Выбранная модель OpenAI недоступна."
        )
    if status in (408, 504):
        return _Failure(
            ProviderErrorCode.TIMEOUT, "Истекло время ожидания OpenAI.", True
        )
    return _Failure(
        ProviderErrorCode.PROVIDER_ERROR,
        "OpenAI не смог выполнить запрос.",
        status == 409 or status >= 500 or code == "server_error",
    )


class OpenAIProvider:
    capabilities = ProviderCapabilities(
        text=True, images=True, audio=True, streaming=True
    )

    def __init__(self, settings, model):
        self.configuration = settings.groups.openai
        self.task_timeout = settings.task_timeout
        self.model = model
        self.closed = False

    def close(self):
        # Each operation owns its AsyncOpenAI/httpx clients and closes them on
        # their event loop before returning; the registry owns this wrapper.
        self.closed = True

    def _validate(self, request, cancel, *, transcription=False):
        if self.closed:
            raise ProviderError(ProviderErrorCode.CLOSED, "Провайдер закрыт.")
        if cancel.is_set():
            raise ProviderError(ProviderErrorCode.CANCELLED, "Запрос отменён.")
        model = OPENAI_MODEL_CAPABILITIES.get(self.model)
        if model is None:
            raise ProviderError(
                ProviderErrorCode.UNKNOWN_MODEL,
                "Возможности выбранной модели OpenAI неизвестны.",
            )
        model.validate(request, transcription=transcription)
        key = self.configuration.api_key.get_secret_value().strip()
        if not key or not key.isascii() or any(char.isspace() for char in key):
            raise ProviderError(
                ProviderErrorCode.AUTH_FAILED, "Укажите OPENAI_API_KEY в .env."
            )

    def generate_response(self, request, publish, cancel):
        self._validate(request, cancel)

        def forward(text):
            if cancel.is_set():
                raise ProviderError(ProviderErrorCode.CANCELLED, "Запрос отменён.")
            publish(text)

        text = self._run(
            lambda api: self._generate(api, request, forward),
            cancel,
            request.timeout,
            forward,
        )
        return GenerationResult(text)

    def transcribe_audio(self, request, cancel):
        self._validate(
            GenerationRequest(request.audio, "audio/wav", TRANSCRIPTION_PROMPT),
            cancel,
            transcription=True,
        )
        try:
            with wave.open(io.BytesIO(request.audio), "rb") as wav:
                size = wav.getnframes() * wav.getnchannels() * wav.getsampwidth()
                if (
                    not size
                    or wav.getsampwidth() != 2
                    or wav.getcomptype() != "NONE"
                    or wav.getframerate() <= 0
                    or len(wav.readframes(wav.getnframes())) != size
                ):
                    raise ValueError("unsupported WAV")
        except (wave.Error, EOFError, ValueError):
            raise ProviderError(
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                "Некорректная или пустая WAV-запись (требуется PCM 16-bit).",
            ) from None

        async def transcribe(api):
            result = await api.audio.transcriptions.create(
                model=self.model,
                file=("recording.wav", request.audio, "audio/wav"),
                response_format="json",
            )
            if not isinstance(result.text, str) or not result.text.strip():
                raise _Failure(
                    ProviderErrorCode.PROVIDER_ERROR,
                    "OpenAI вернул пустую транскрипцию.",
                )
            return result.text.strip()

        return TranscriptionResult(self._run(transcribe, cancel, request.timeout))

    async def _generate(self, api, request, publish):
        messages = []
        for item in request.history:
            messages.extend(
                (
                    {"role": "user", "content": item.question},
                    {"role": "assistant", "content": item.answer},
                )
            )
        if request.mime_type == "text/plain":
            content = [{"type": "input_text", "text": request.data.decode("utf-8")}]
        else:
            url = "data:image/jpeg;base64," + base64.b64encode(request.data).decode(
                "ascii"
            )
            content = [{"type": "input_image", "image_url": url, "detail": "auto"}]
        content.append(
            {
                "type": "input_text",
                "text": request.instruction or "Ответь на вопрос пользователя.",
            }
        )
        messages.append({"role": "user", "content": content})
        stream = await api.responses.create(
            model=self.model,
            instructions=request.prompt,
            input=messages,
            stream=True,
            store=False,
            max_output_tokens=2048,
            reasoning={"effort": "none"},
        )
        answer = ""
        async with stream:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    answer += event.delta
                    publish(answer)
                elif event.type == "response.completed":
                    text = event.response.output_text
                    if event.response.status != "completed" or not text.strip():
                        raise _Failure(
                            ProviderErrorCode.PROVIDER_ERROR,
                            "OpenAI не вернул полный текстовый ответ.",
                        )
                    if text != answer:
                        publish(text)
                    return text
                elif event.type in ("error", "response.failed"):
                    error = event if event.type == "error" else event.response.error
                    raise _status_error(0, getattr(error, "code", None))
                elif event.type == "response.incomplete":
                    raise _Failure(
                        ProviderErrorCode.PROVIDER_ERROR,
                        "OpenAI завершил генерацию неполным ответом.",
                    )
        raise _Failure(
            ProviderErrorCode.NETWORK_ERROR,
            "Поток OpenAI прерван до завершения ответа.",
            True,
        )

    def _run(self, operation, cancel, budget, publish=None):
        async def supervise():
            timeout = self.task_timeout if budget is None else budget
            if cancel.is_set():
                raise ProviderError(ProviderErrorCode.CANCELLED, "Запрос отменён.")
            if timeout <= 0:
                raise ProviderError(
                    ProviderErrorCode.TIMEOUT, "Превышено время обработки OpenAI."
                )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            task = asyncio.create_task(self._attempts(operation, deadline, publish))
            try:
                while not cancel.is_set():
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise ProviderError(
                            ProviderErrorCode.TIMEOUT,
                            "Превышено общее время обработки OpenAI.",
                        )
                    done, _ = await asyncio.wait((task,), timeout=min(0.05, remaining))
                    if done:
                        result = task.result()
                        if cancel.is_set():
                            break
                        if loop.time() >= deadline:
                            raise ProviderError(
                                ProviderErrorCode.TIMEOUT,
                                "Превышено общее время обработки OpenAI.",
                            )
                        return result
                raise ProviderError(ProviderErrorCode.CANCELLED, "Запрос отменён.")
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        try:
            return asyncio.run(supervise())
        except ProviderError:
            raise
        except Exception:  # noqa: BLE001 - sanitize all SDK/parsing details
            raise ProviderError(
                ProviderErrorCode.PROVIDER_ERROR, "Ошибка обработки запроса OpenAI."
            ) from None

    def _http_client(self, timeout):
        return httpx.AsyncClient(follow_redirects=False, timeout=timeout)

    async def _attempts(self, operation, deadline, publish):
        loop = asyncio.get_running_loop()
        for attempt in range(3):
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ProviderError(
                    ProviderErrorCode.TIMEOUT, "Превышено время обработки OpenAI."
                )
            timeout = min(self.configuration.request_timeout, remaining)
            try:
                http = self._http_client(timeout)
                try:
                    async with openai.AsyncOpenAI(
                        api_key=self.configuration.api_key.get_secret_value().strip(),
                        base_url="https://api.openai.com/v1",
                        max_retries=0,
                        default_headers={
                            "OpenAI-Organization": openai.omit,
                            "OpenAI-Project": openai.omit,
                        },
                        http_client=http,
                        timeout=timeout,
                    ) as api:
                        return await asyncio.wait_for(operation(api), timeout)
                finally:
                    if not http.is_closed:
                        await http.aclose()
            except _Failure as exc:
                failure = exc
            except (asyncio.TimeoutError, openai.APITimeoutError):
                failure = _Failure(
                    ProviderErrorCode.TIMEOUT, "Истекло время ожидания OpenAI.", True
                )
            except openai.APIConnectionError:
                failure = _Failure(
                    ProviderErrorCode.NETWORK_ERROR,
                    "Не удалось подключиться к OpenAI.",
                    True,
                )
            except openai.APIStatusError as exc:
                failure = _status_error(exc.status_code, exc.code)
            if not failure.retryable or attempt == 2:
                raise failure from None
            if publish is not None:
                publish("")
            await asyncio.sleep(min(0.25 * 2**attempt, max(0, deadline - loop.time())))
        raise AssertionError("unreachable")
