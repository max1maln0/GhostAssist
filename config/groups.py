"""Immutable logical views over the backwards-compatible flat dotenv schema."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Group(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)


class ApplicationConfig(Group):
    schema_version: int


class TaskConfig(Group):
    timeout: float


class AudioConfig(Group):
    device: str
    max_seconds: float
    max_mib: int
    vad_enabled: bool
    auto_send: bool
    min_speech_ms: int
    silence_ms: int
    segment_seconds: int
    sensitivity: int


class ScreenConfig(Group):
    width: int
    height: int


class InterfaceConfig(Group):
    close_to_tray: bool
    hotkeys: tuple[tuple[str, str], ...]


class MemoryConfig(Group):
    enabled: bool
    depth: int
    profile: str


class ProviderConfig(Group):
    identifier: Literal["gemini", "openai"] = "gemini"
    model: str
    api_key: SecretStr = Field(repr=False)
    request_timeout: float
    # Provider-specific fixed endpoint; custom endpoints are not configurable.


class BindingConfig(Group):
    identifier: Literal["gemini", "openai"]
    model: str


class ConfigurationGroups(Group):
    application: ApplicationConfig
    tasks: TaskConfig
    audio: AudioConfig
    screen: ScreenConfig
    interface: InterfaceConfig
    memory: MemoryConfig
    provider: ProviderConfig
    openai: ProviderConfig
    generation: BindingConfig
    transcription: BindingConfig
