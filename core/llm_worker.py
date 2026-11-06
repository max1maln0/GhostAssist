import io

import mss
from PIL import Image

from core.provider_contracts import (
    GenerationRequest,
    ProviderError,
    ProviderErrorCode,
    TranscriptionRequest,
)
from providers.gemini import GeminiProvider
from providers.gemini_client import GenerationError


class LLMWorker:
    def __init__(self, settings, provider=None):
        self.settings = settings
        self.provider = provider or GeminiProvider(settings)

    def capture_screen(self, bbox=None):
        with mss.mss() as capture:
            area = bbox or next(
                (
                    monitor
                    for monitor in capture.monitors[1:]
                    if monitor["left"] == monitor["top"] == 0
                ),
                capture.monitors[1],
            )
            if area["width"] * area["height"] > 40_000_000:
                raise GenerationError("Выберите меньшую область экрана.")
            shot = capture.grab(area)
            image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if bbox is None:
            image.thumbnail((self.settings.crop_width, self.settings.crop_height))
        with io.BytesIO() as buffer:
            image.save(buffer, format="JPEG", quality=75)
            return buffer.getvalue()

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
