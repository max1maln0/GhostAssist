"""Explicit Windows composition; no service locator or DI framework."""

import ctypes
from dataclasses import dataclass

from core.llm_worker import LLMWorker
from core.orchestrator import TaskOrchestrator
from core.platform_contracts import AudioCapture, HotkeyBackend, ScreenCapture
from core.provider_contracts import AIProvider, TranscriptionProvider
from core.provider_registry import (
    ProviderBinding,
    ProviderRegistration,
    ProviderRegistry,
)
from core.provider_routing import BoundGeneration, BoundTranscription
from platform_adapters.windows.audio import WindowsAudioCapture
from platform_adapters.windows.hotkeys import WindowsHotkeyBackend
from platform_adapters.windows.screen import WindowsScreenCapture
from providers.gemini import GeminiProvider
from providers.gemini_catalog import GEMINI_MODEL_CAPABILITIES
from providers.openai_catalog import OPENAI_MODEL_CAPABILITIES
from providers.openai_provider import OpenAIProvider


@dataclass
class Dependencies:
    orchestrator: TaskOrchestrator
    provider: AIProvider
    transcription: TranscriptionProvider
    screen: ScreenCapture
    audio: AudioCapture
    hotkeys: HotkeyBackend
    llm: LLMWorker
    registry: ProviderRegistry

    def close(self):
        self.registry.close()


def build_dependencies(settings, *, registry=None, generation=None, transcription=None):
    if registry is None:
        registry = ProviderRegistry()
        registry.register(
            ProviderRegistration(
                "gemini",
                lambda model: GeminiProvider(settings, model=model),
                GeminiProvider.capabilities,
                GEMINI_MODEL_CAPABILITIES,
            )
        )
        registry.register(
            ProviderRegistration(
                "openai",
                lambda model: OpenAIProvider(settings, model),
                OpenAIProvider.capabilities,
                OPENAI_MODEL_CAPABILITIES,
            )
        )
    provider = BoundGeneration(
        registry, generation or ProviderBinding(*settings.resolve_binding("generation"))
    )
    transcriber = BoundTranscription(
        registry,
        transcription or ProviderBinding(*settings.resolve_binding("transcription")),
    )
    screen = WindowsScreenCapture(settings.crop_width, settings.crop_height)
    return Dependencies(
        orchestrator=TaskOrchestrator(),
        provider=provider,
        transcription=transcriber,
        screen=screen,
        audio=WindowsAudioCapture(),
        hotkeys=WindowsHotkeyBackend(),
        llm=LLMWorker(settings, provider=provider, transcription=transcriber),
        registry=registry,
    )


def configure_dpi():
    set_dpi_context = getattr(
        ctypes.windll.user32, "SetProcessDpiAwarenessContext", None
    )
    if set_dpi_context is None or not set_dpi_context(ctypes.c_void_p(-4)):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
