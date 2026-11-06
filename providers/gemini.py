"""Gemini adapter implementing the provider contracts."""

from core.contracts import ProviderCapabilities
from core.provider_contracts import (
    GenerationResult,
    ProviderErrorCode,
    TranscriptionResult,
)
from providers.gemini_client import GeminiClient, GenerationError


class GeminiProvider:
    capabilities = ProviderCapabilities(
        text=True, images=True, audio=True, streaming=True
    )

    def __init__(self, settings):
        self.client = GeminiClient(settings)

    def generate_response(self, request, publish, cancel):
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
        if not self.capabilities.audio:
            raise GenerationError(
                "Аудио не поддерживается провайдером.",
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
            )
        if cancel.is_set():
            raise GenerationError("Запрос отменён.", ProviderErrorCode.CANCELLED)
        result = self.client.transcribe(request.audio, cancel, budget=request.timeout)
        if cancel.is_set() or result is None:
            raise GenerationError("Запрос отменён.", ProviderErrorCode.CANCELLED)
        return TranscriptionResult(result)
