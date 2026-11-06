"""Provider interfaces without SDK or platform dependencies."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

from core.contracts import ProviderCapabilities


class ProviderErrorCode(str, Enum):
    AUTH_FAILED = "auth_failed"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    MODEL_UNAVAILABLE = "model_unavailable"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    CANCELLED = "cancelled"
    PROVIDER_ERROR = "provider_error"


class ProviderError(RuntimeError):
    def __init__(self, code: ProviderErrorCode, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GenerationRequest:
    data: bytes = field(repr=False)
    mime_type: str
    prompt: str = field(repr=False)
    history: tuple = field(default=(), repr=False)
    instruction: str | None = field(default=None, repr=False)
    timeout: float | None = None


@dataclass(frozen=True)
class GenerationResult:
    text: str = field(repr=False)


@dataclass(frozen=True)
class TranscriptionRequest:
    audio: bytes = field(repr=False)
    timeout: float | None = None


@dataclass(frozen=True)
class TranscriptionResult:
    text: str = field(repr=False)


class Cancellation(Protocol):
    def is_set(self) -> bool: ...


class AIProvider(Protocol):
    capabilities: ProviderCapabilities

    def generate_response(
        self,
        request: GenerationRequest,
        publish: Callable[[str], None],
        cancel: Cancellation,
    ) -> GenerationResult: ...


class TranscriptionProvider(Protocol):
    def transcribe_audio(
        self, request: TranscriptionRequest, cancel: Cancellation
    ) -> TranscriptionResult: ...
