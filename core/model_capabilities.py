"""Explicit model evidence and conservative input policy, independent of SDKs."""

import json
from dataclasses import dataclass

from core.provider_contracts import GenerationRequest, ProviderError, ProviderErrorCode


@dataclass(frozen=True)
class ModelCapabilities:
    text_input: bool | None = None
    image_input: bool | None = None
    audio_input: bool | None = None
    transcription: bool | None = None
    streaming: bool | None = None
    mime_types: frozenset[str] | None = None
    max_input_bytes: int | None = None
    max_request_bytes: int | None = None
    source: str = "unknown"

    def __post_init__(self):
        if self.mime_types is not None:
            object.__setattr__(self, "mime_types", frozenset(self.mime_types))
        for limit in (self.max_input_bytes, self.max_request_bytes):
            if limit is not None and limit <= 0:
                raise ValueError("model limits must be positive")

    def _require(self, value):
        if value is None:
            raise ProviderError(
                ProviderErrorCode.UNKNOWN_CAPABILITY,
                "Возможности выбранной модели не подтверждены.",
            )
        if value is not True:
            raise ProviderError(
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                "Выбранная модель не поддерживает этот сценарий.",
            )

    def validate(self, request: GenerationRequest, *, transcription=False):
        # Generation includes text instructions/history and streams its answer.
        # A standalone ASR backend may support only audio and a final transcript.
        if not transcription:
            self._require(self.text_input)
            self._require(self.streaming)
        modality = request.mime_type.split("/", 1)[0]
        self._require(
            {
                "text": self.text_input,
                "image": self.image_input,
                "audio": self.audio_input,
            }.get(modality, False)
        )
        if transcription:
            self._require(self.transcription)
        if self.mime_types is None:
            self._require(None)
        if request.mime_type not in self.mime_types:
            raise ProviderError(
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                "Формат входных данных не поддерживается моделью.",
            )
        if (
            self.max_input_bytes is not None
            and len(request.data) > self.max_input_bytes
        ):
            raise ProviderError(
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                "Превышен допустимый размер входных данных модели.",
            )
        if self.max_request_bytes is not None:
            # Upper-bound policy for inline JSON/base64, not a token counter or
            # a claim about the model's context window. Include private text
            # without retaining or logging its serialized representation.
            text = {
                "prompt": request.prompt,
                "instruction": request.instruction,
                "history": [(i.question, i.answer) for i in request.history],
            }
            try:
                data_size = (
                    len(json.dumps(request.data.decode("utf-8"), ensure_ascii=True))
                    if modality == "text"
                    else 4 * ((len(request.data) + 2) // 3)
                )
            except UnicodeError:
                raise ProviderError(
                    ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                    "Некорректная кодировка текстового запроса.",
                ) from None
            size = (
                data_size
                + len(json.dumps(text, ensure_ascii=True))
                + 4096
                + 256 * len(request.history)
            )
            if size > self.max_request_bytes:
                raise ProviderError(
                    ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                    "Запрос с учётом контекста превышает допустимый размер модели.",
                )
