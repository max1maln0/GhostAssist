"""MSS screen capture with bounded JPEG output."""

import io

import mss
from PIL import Image

from core.platform_contracts import ScreenCaptureError


class WindowsScreenCapture:
    def __init__(self, width, height):
        self.width, self.height = width, height

    def capture(self, bbox=None):
        with mss.mss() as capture:
            monitors = capture.monitors[1:]
            if bbox is None:
                if not monitors:
                    raise ScreenCaptureError("Нет доступных экранов.")
                area = next(
                    (
                        monitor
                        for monitor in monitors
                        if monitor["left"] == monitor["top"] == 0
                    ),
                    monitors[0],
                )
            else:
                area = bbox
            if (
                not isinstance(area, dict)
                or not all(
                    isinstance(area.get(key), int)
                    for key in ("left", "top", "width", "height")
                )
                or area["width"] <= 0
                or area["height"] <= 0
            ):
                raise ScreenCaptureError("Некорректная область экрана.")
            if area["width"] * area["height"] > 40_000_000:
                raise ScreenCaptureError("Выберите меньшую область экрана.")
            shot = capture.grab(area)
            image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if bbox is None:
            image.thumbnail((self.width, self.height))
        with io.BytesIO() as buffer:
            image.save(buffer, format="JPEG", quality=75)
            return buffer.getvalue()
