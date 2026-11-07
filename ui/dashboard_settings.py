"""One transactional settings writer plus a non-persistent UI patch model."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from config.env_file import update_env
from config.migration import read_version
from config.settings import ENV_FILE, Settings, validate_hotkeys
from config.storage import configuration_lock
from providers.gemini_catalog import GEMINI_MODEL_CAPABILITIES
from providers.openai_catalog import OPENAI_MODEL_CAPABILITIES

CATALOGS = {"gemini": GEMINI_MODEL_CAPABILITIES, "openai": OPENAI_MODEL_CAPABILITIES}


class SettingsEditError(ValueError):
    pass


def compatible_models(provider, purpose):
    return tuple(
        name
        for name, caps in CATALOGS[provider].items()
        if (
            caps.audio_input is True
            and caps.transcription is True
            and "audio/wav" in (caps.mime_types or ())
            if purpose == "transcription"
            else caps.text_input is True
            and caps.image_input is True
            and caps.streaming is True
            and "image/jpeg" in (caps.mime_types or ())
        )
    )


def validate_bindings(settings):
    for purpose in ("generation", "transcription"):
        provider, model = settings.resolve_binding(purpose)
        if model not in compatible_models(provider, purpose):
            label = (
                "генерации текста и изображений"
                if purpose == "generation"
                else "транскрибации WAV"
            )
            raise SettingsEditError(
                f"Выберите известную совместимую модель для {label}."
            )


def check_environment(values):
    for key, value in values.items():
        if key in os.environ and os.environ[key] != value:
            raise SettingsEditError(
                f"Параметр {key} задан переменной окружения. Измените или удалите её перед сохранением."
            )


@dataclass
class SavedChange:
    settings: Settings = field(repr=False)
    listener: object = field(default=None, repr=False)


def cleanup_change(result):
    if result and result.listener:
        result.listener.stop()
        result.listener.join(timeout=0.5)


def persist_changes(
    current: Settings, values: dict[str, str], *, path: Path = ENV_FILE, prepare=None
):
    check_environment(values)
    prepared = None
    try:
        with configuration_lock(path, timeout=0.05):
            read_version(path)
            latest = Settings(_env_file=path)
            candidate = Settings(
                _env_file=path, **{key.lower(): value for key, value in values.items()}
            )
            # An independent field patch merges with the latest file; a stale
            # editor cannot silently overwrite a newer change of the same field.
            for key in values:
                field_name = key.lower()
                if getattr(latest, field_name) != getattr(
                    current, field_name
                ) and getattr(candidate, field_name) != getattr(latest, field_name):
                    raise SettingsEditError(
                        f"Параметр {key} изменён другим процессом. Отмените изменения и обновите форму."
                    )
            hotkeys = {
                key.lower(): value
                for key, value in values.items()
                if key.startswith("HOTKEY_")
            }
            validate_hotkeys(candidate, hotkeys)
            if any(key.startswith(("GENERATION_", "TRANSCRIPTION_")) for key in values):
                validate_bindings(candidate)
            if prepare and hotkeys:
                prepared = prepare(candidate, hotkeys)
            update_env(values, path)
        return SavedChange(candidate, prepared)
    except Exception:
        if prepared:
            prepared.stop()
            prepared.join(timeout=0.5)
        raise


class SettingsDraft:
    """A patch over Settings, never a second configuration store."""

    def __init__(self, settings):
        self.saved = settings
        self.changes = {}
        self.baseline = {}

    def display(self, key):
        name = key.lower()
        for purpose in ("generation", "transcription"):
            if name == purpose + "_provider":
                return self.saved.resolve_binding(purpose)[0]
            if name == purpose + "_model":
                return self.saved.resolve_binding(purpose)[1]
        value = getattr(self.saved, name)
        return str(value).lower() if isinstance(value, bool) else str(value)

    def stage(self, key, value):
        if value == self.baseline[key]:
            self.changes.pop(key, None)
        else:
            self.changes[key] = value

    def reset(self, settings=None):
        if settings is not None:
            self.saved = settings
        self.changes.clear()
