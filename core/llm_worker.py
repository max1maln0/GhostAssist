from core.provider_contracts import (
    GenerationRequest,
    ProviderError,
    ProviderErrorCode,
    TranscriptionRequest,
)


class LLMWorker:
    def __init__(self, settings, provider, transcription=None):
        self.settings = settings
        self.provider = provider
        self.transcription = transcription if transcription is not None else provider

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
            data,
            mime_type,
            prompt,
            tuple(history),
            instruction,
            self.settings.task_timeout if budget is None else budget,
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
            result = self.transcription.transcribe_audio(
                TranscriptionRequest(
                    data, self.settings.task_timeout if budget is None else budget
                ),
                cancel,
            )
        except ProviderError as exc:
            if exc.code is ProviderErrorCode.CANCELLED:
                return None
            raise
        return None if result is None else result.text
