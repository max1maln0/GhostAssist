"""Gemini adapter implementing the provider contracts."""

from core.contracts import ProviderCapabilities
from core.provider_contracts import (
    GenerationRequest,
    GenerationResult,
    ProviderErrorCode,
    TranscriptionResult,
)
from core.provider_routing import TRANSCRIPTION_INSTRUCTION, TRANSCRIPTION_PROMPT
from providers.gemini_catalog import validate_model
from providers.gemini_client import GeminiClient, GenerationError


class GeminiProvider:
    capabilities = ProviderCapabilities(
        text=True, images=True, audio=True, streaming=True
    )

    def __init__(self, settings, model=None):
        self.client = GeminiClient(settings, model=model)
        self.closed = False

    def close(self):
        # SDK clients are owned and closed inside each request/event loop.
        self.closed = True

    def _check_open(self):
        if self.closed:
            raise GenerationError("Провайдер закрыт.", ProviderErrorCode.CLOSED)

    def generate_response(self, request, publish, cancel):
        self._check_open()
        allowed = {
            "text/plain": self.capabilities.text,
            "image/jpeg": self.capabilities.images,
            "audio/wav": self.capabilities.audio,
        }
        if not allowed.get(request.mime_type, False) or not self.capabilities.streaming:
            raise GenerationError(
                "Формат не поддерживается провайдером.",
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
            )
        if cancel.is_set():
            raise GenerationError("Запрос отменён.", ProviderErrorCode.CANCELLED)
        validate_model(self.client.model_catalog, self.client.model, request)
        result = self.client.generate(
            request.data,
            request.mime_type,
            request.prompt,
            publish,
            cancel,
            budget=request.timeout,
            history=request.history,
            instruction=request.instruction,
        )
        if cancel.is_set() or result is None:
            raise GenerationError("Запрос отменён.", ProviderErrorCode.CANCELLED)
        return GenerationResult(result)

    def transcribe_audio(self, request, cancel):
        self._check_open()
        if not self.capabilities.audio:
            raise GenerationError(
                "Аудио не поддерживается провайдером.",
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
            )
        if cancel.is_set():
            raise GenerationError("Запрос отменён.", ProviderErrorCode.CANCELLED)
        validate_model(
            self.client.model_catalog,
            self.client.model,
            GenerationRequest(
                request.audio,
                "audio/wav",
                TRANSCRIPTION_PROMPT,
                instruction=TRANSCRIPTION_INSTRUCTION,
            ),
            transcription=True,
        )
        result = self.client.transcribe(request.audio, cancel, budget=request.timeout)
        if cancel.is_set() or result is None:
            raise GenerationError("Запрос отменён.", ProviderErrorCode.CANCELLED)
        return TranscriptionResult(result)
