from core.provider_contracts import (
    GenerationRequest,
    ProviderError,
    ProviderErrorCode,
    TranscriptionRequest,
)
from providers.gemini import GeminiProvider
from providers.gemini_client import GenerationError as GenerationError


class LLMWorker:
    def __init__(self, settings, provider=None):
        self.settings = settings
        self.provider = provider or GeminiProvider(settings)

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
    ):
        request = GenerationRequest(
            data, mime_type, prompt, tuple(history), instruction, budget
        )
        try:
            result = self.provider.generate_response(request, publish, cancel)
        except ProviderError as exc:
            if exc.code is ProviderErrorCode.CANCELLED:
                return None
            raise
        return None if result is None else result.text

    def transcribe(self, data, cancel, *, budget=None):
        try:
            result = self.provider.transcribe_audio(
                TranscriptionRequest(data, budget), cancel
            )
        except ProviderError as exc:
            if exc.code is ProviderErrorCode.CANCELLED:
                return None
            raise
        return None if result is None else result.text
