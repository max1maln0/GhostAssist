"""Independent generation/transcription bindings over the same lazy registry."""

from dataclasses import replace
from time import monotonic

from core.provider_contracts import GenerationRequest, ProviderError, ProviderErrorCode


def check_cancelled(cancel):
    if cancel.is_set():
        raise ProviderError(ProviderErrorCode.CANCELLED, "Запрос отменён.")


def remaining_request(request, started):
    if request.timeout is None:
        return request
    remaining = request.timeout - (monotonic() - started)
    if remaining <= 0:
        raise ProviderError(
            ProviderErrorCode.TIMEOUT, "Превышено общее время обработки."
        )
    return replace(request, timeout=remaining)


class BoundGeneration:
    def __init__(self, registry, binding):
        self.registry = registry
        self.binding = binding

    @property
    def capabilities(self):
        return self.registry.describe(self.binding.provider_id).capabilities

    def generate_response(self, request, publish, cancel):
        started = monotonic()
        check_cancelled(cancel)
        self.registry.validate(self.binding, request)
        remaining_request(request, started)
        with self.registry.acquire(self.binding) as provider:
            check_cancelled(cancel)

            def forward(text):
                check_cancelled(cancel)
                self.registry.describe(self.binding.provider_id)
                remaining_request(request, started)
                publish(text)

            try:
                result = provider.generate_response(
                    remaining_request(request, started), forward, cancel
                )
            except ProviderError:
                raise
            except Exception:  # noqa: BLE001 - provider trust boundary
                raise ProviderError(
                    ProviderErrorCode.PROVIDER_ERROR,
                    "Ошибка выполнения запроса провайдера.",
                ) from None
            check_cancelled(cancel)
            self.registry.describe(self.binding.provider_id)
            remaining_request(request, started)
            return result


class BoundTranscription:
    def __init__(self, registry, binding):
        self.registry = registry
        self.binding = binding

    def transcribe_audio(self, request, cancel):
        started = monotonic()
        check_cancelled(cancel)
        # Use the real transcription instruction for the input-size policy.
        validation = GenerationRequest(
            request.audio,
            "audio/wav",
            TRANSCRIPTION_PROMPT,
            instruction=TRANSCRIPTION_INSTRUCTION,
        )
        self.registry.validate(self.binding, validation, transcription=True)
        remaining_request(request, started)
        with self.registry.acquire(self.binding, transcription=True) as provider:
            check_cancelled(cancel)
            try:
                result = provider.transcribe_audio(
                    remaining_request(request, started), cancel
                )
            except ProviderError:
                raise
            except Exception:  # noqa: BLE001 - provider trust boundary
                raise ProviderError(
                    ProviderErrorCode.PROVIDER_ERROR, "Ошибка транскрибации провайдера."
                ) from None
            check_cancelled(cancel)
            self.registry.describe(self.binding.provider_id)
            remaining_request(request, started)
            return result


TRANSCRIPTION_PROMPT = (
    "Точно транскрибируй речь на исходном языке. Верни только произнесённый текст."
)
TRANSCRIPTION_INSTRUCTION = "Распознай речь. Сохрани язык и слова говорящего."
