"""Reviewed exact model IDs; no prefix-based inference or network discovery."""

from types import MappingProxyType

from core.model_capabilities import ModelCapabilities
from core.provider_contracts import ProviderError, ProviderErrorCode

# Reviewed 2026-11-07, model-specific inputs:
# https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite
# https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite
# https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash
# https://ai.google.dev/gemini-api/docs/audio
# https://ai.google.dev/gemini-api/docs/image-understanding
# This is the supported application subset. The 20 MB inline request policy
# counts JSON/base64 and private text conservatively; it is not a token limit.
GEMINI_MODEL_CAPABILITIES = MappingProxyType(
    {
        model: ModelCapabilities(
            text_input=True,
            image_input=True,
            audio_input=True,
            transcription=True,
            streaming=True,
            mime_types=frozenset({"text/plain", "image/jpeg", "audio/wav"}),
            max_input_bytes=20_000_000,
            max_request_bytes=20_000_000,
            source=f"https://ai.google.dev/gemini-api/docs/models/{model}",
        )
        for model in (
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.6-flash",
        )
    }
)


def validate_model(catalog, model, request, *, transcription=False):
    capabilities = catalog.get(model)
    if capabilities is None:
        raise ProviderError(
            ProviderErrorCode.UNKNOWN_MODEL,
            "Возможности выбранной модели Gemini неизвестны. Выберите модель из проверенного каталога.",
        )
    capabilities.validate(request, transcription=transcription)
