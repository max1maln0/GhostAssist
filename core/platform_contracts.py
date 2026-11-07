"""Platform-independent interfaces used by the application composition root."""

from typing import Callable, Protocol


class ScreenCaptureError(RuntimeError):
    pass


class ScreenCapture(Protocol):
    def capture(self, bbox: dict | None = None) -> bytes: ...


class AudioCapture(Protocol):
    def list_devices(self) -> list[str]: ...

    def record(
        self,
        stop,
        cancel,
        seconds: float,
        max_bytes: int,
        device_name: str = "",
        **options,
    ) -> bytes: ...


class HotkeyBackend(Protocol):
    def create_keyboard(self, bindings: dict[str, Callable]): ...

    def create_mouse(self, on_click: Callable): ...

    def button_action(self, button) -> str | None: ...
