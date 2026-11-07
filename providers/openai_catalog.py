"""Exact reviewed OpenAI IDs and the application-supported format subset."""

from types import MappingProxyType

from core.model_capabilities import ModelCapabilities

# Reviewed 2026-11-07. Metadata is not inferred from model-name prefixes.
# https://developers.openai.com/api/docs/models/gpt-5.6-terra
# https://developers.openai.com/api/docs/models/gpt-transcribe
# https://developers.openai.com/api/docs/guides/speech-to-text (25 MB files)
# Generation limits below are conservative application policy, not token limits.
OPENAI_MODEL_CAPABILITIES = MappingProxyType(
    {
        "gpt-5.6-terra": ModelCapabilities(
            text_input=True,
            image_input=True,
            audio_input=False,
            transcription=False,
            streaming=True,
            mime_types=frozenset({"text/plain", "image/jpeg"}),
            max_input_bytes=20_000_000,
            max_request_bytes=40_000_000,
            source="https://developers.openai.com/api/docs/models/gpt-5.6-terra",
        ),
        "gpt-transcribe": ModelCapabilities(
            text_input=False,
            image_input=False,
            audio_input=True,
            transcription=True,
            streaming=False,
            mime_types=frozenset({"audio/wav"}),
            max_input_bytes=25_000_000,
            max_request_bytes=None,
            source="https://developers.openai.com/api/docs/models/gpt-transcribe",
        ),
    }
)
