"""Deterministic offline provider for contract and integration tests."""

import time

from core.contracts import ProviderCapabilities
from core.provider_contracts import (
    GenerationResult,
    ProviderError,
    ProviderErrorCode,
    TranscriptionResult,
)


class MockProvider:
    capabilities = ProviderCapabilities(
        text=True, images=True, audio=True, streaming=True
    )

    def __init__(self, chunks=("answer",), delay=0.0, error=None):
        self.chunks = chunks
        self.delay = delay
        self.error = error

    def generate_response(self, request, publish, cancel):
        if request.mime_type not in ("text/plain", "image/jpeg", "audio/wav"):
            raise ProviderError(
                ProviderErrorCode.UNSUPPORTED_CAPABILITY, "Unsupported format"
            )
        started = time.monotonic()
        answer = ""
        for chunk in self.chunks:
            deadline = time.monotonic() + self.delay
            while time.monotonic() < deadline:
                if cancel.is_set():
                    raise ProviderError(ProviderErrorCode.CANCELLED, "Cancelled")
                if (
                    request.timeout is not None
                    and time.monotonic() - started >= request.timeout
                ):
                    raise ProviderError(ProviderErrorCode.TIMEOUT, "Timeout")
                time.sleep(min(0.005, max(0, deadline - time.monotonic())))
            if cancel.is_set():
                raise ProviderError(ProviderErrorCode.CANCELLED, "Cancelled")
            if self.error:
                raise ProviderError(self.error, "Provider failure")
            answer += chunk
            publish(answer)
        if not answer.strip():
            raise ProviderError(ProviderErrorCode.PROVIDER_ERROR, "Empty response")
        return GenerationResult(answer)

    def transcribe_audio(self, request, cancel):
        if cancel.is_set():
            raise ProviderError(ProviderErrorCode.CANCELLED, "Cancelled")
        if not request.audio:
            raise ProviderError(ProviderErrorCode.PROVIDER_ERROR, "Empty audio")
        if self.error:
            raise ProviderError(self.error, "Provider failure")
        return TranscriptionResult("transcript")
