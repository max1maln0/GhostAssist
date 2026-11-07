"""Explicit Windows composition; no service locator or DI framework."""

import ctypes
from dataclasses import dataclass

from core.llm_worker import LLMWorker
from core.orchestrator import TaskOrchestrator
from core.platform_contracts import AudioCapture, HotkeyBackend, ScreenCapture
from core.provider_contracts import AIProvider, TranscriptionProvider
from platform_adapters.windows.audio import WindowsAudioCapture
from platform_adapters.windows.hotkeys import WindowsHotkeyBackend
from platform_adapters.windows.screen import WindowsScreenCapture
from providers.gemini import GeminiProvider


@dataclass
class Dependencies:
    screenshots: TaskOrchestrator
    provider: AIProvider
    transcription: TranscriptionProvider
    screen: ScreenCapture
    audio: AudioCapture
    hotkeys: HotkeyBackend
    llm: LLMWorker


def build_dependencies(settings):
    provider = GeminiProvider(settings)
    screen = WindowsScreenCapture(settings.crop_width, settings.crop_height)
    return Dependencies(
        screenshots=TaskOrchestrator(),
        provider=provider,
        transcription=provider,
        screen=screen,
        audio=WindowsAudioCapture(),
        hotkeys=WindowsHotkeyBackend(),
        llm=LLMWorker(settings, provider=provider),
    )


def configure_dpi():
    set_dpi_context = getattr(
        ctypes.windll.user32, "SetProcessDpiAwarenessContext", None
    )
    if set_dpi_context is None or not set_dpi_context(ctypes.c_void_p(-4)):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
