import ast
import io
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mock_provider import MockProvider
from PIL import Image

from config.settings import Settings
from core.llm_worker import LLMWorker
from core.orchestrator import TaskOrchestrator
from core.platform_contracts import ScreenCaptureError
from main import GhostAssistApp
from platform_adapters.windows.screen import WindowsScreenCapture


class PlatformTests(unittest.TestCase):
    def test_core_has_no_windows_or_capture_imports(self):
        forbidden = {
            "ctypes",
            "pynput",
            "pyaudiowpatch",
            "mss",
            "platform_adapters",
            "providers",
            "google",
            "openai",
            "tkinter",
            "customtkinter",
        }
        for path in Path("core").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                self.assertFalse(
                    forbidden.intersection(name.split(".")[0] for name in names),
                    f"{path}: {names}",
                )

    def test_windows_screen_supports_negative_coordinates_and_bounds(self):
        area = {"left": -80, "top": -40, "width": 20, "height": 20}
        backend = Mock()
        backend.monitors = [{}, {"left": 0, "top": 0, "width": 20, "height": 20}]
        backend.grab.return_value = SimpleNamespace(size=(20, 20), bgra=bytes(1600))
        context = Mock()
        context.__enter__ = Mock(return_value=backend)
        context.__exit__ = Mock(return_value=False)
        with patch("platform_adapters.windows.screen.mss.mss", return_value=context):
            result = WindowsScreenCapture(1280, 720).capture(area)
            backend.grab.assert_called_once_with(area)
            with Image.open(io.BytesIO(result)) as image:
                self.assertEqual((image.format, image.size), ("JPEG", (20, 20)))
            with self.assertRaises(ScreenCaptureError):
                WindowsScreenCapture(1280, 720).capture({**area, "width": 0})
            with self.assertRaises(ScreenCaptureError):
                WindowsScreenCapture(1280, 720).capture({**area, "width": 4000000})

    def test_injected_adapters_drive_fullscreen_without_windows_devices(self):
        settings = Settings(_env_file=None)
        screen = SimpleNamespace(capture=Mock(return_value=b"fake-jpeg"))
        audio = SimpleNamespace(record=Mock(), list_devices=Mock(return_value=[]))
        hotkeys = SimpleNamespace(
            create_keyboard=Mock(), create_mouse=Mock(), button_action=Mock()
        )
        provider = MockProvider(chunks=("Вопрос: Вопрос?\nОтвет: Ответ",))
        orchestrator = TaskOrchestrator()
        dependencies = SimpleNamespace(
            llm=LLMWorker(settings, provider=provider),
            screen=screen,
            audio=audio,
            hotkeys=hotkeys,
            orchestrator=orchestrator,
        )
        with patch("main.StealthHUD"):
            app = GhostAssistApp(settings, dependencies=dependencies)
        try:
            app.capture()
            app.future.result(timeout=2)
            app.poll()
            screen.capture.assert_called_once_with()
            self.assertEqual(app.sessions.snapshot("TEST")[1][0].answer, "Ответ")
            self.assertFalse(orchestrator.busy)
        finally:
            orchestrator.close(timeout=1)
            app.controller.close()
            app.controller._source.shutdown(wait=True)

    def test_fake_audio_honours_stop_and_cancel(self):
        stop, cancel = threading.Event(), threading.Event()

        class FakeAudio:
            def record(
                self, stop, cancel, seconds, max_bytes, device_name="", **options
            ):
                return (
                    b""
                    if cancel.is_set()
                    else (b"done" if stop.is_set() else b"recording")
                )

        audio = FakeAudio()
        self.assertEqual(audio.record(stop, cancel, 1, 10), b"recording")
        stop.set()
        self.assertEqual(audio.record(stop, cancel, 1, 10), b"done")
        cancel.set()
        self.assertEqual(audio.record(stop, cancel, 1, 10), b"")
