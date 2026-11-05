import re
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
PROFILES = ROOT / "config" / "profiles"
GEMINI_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash")


def profile_path(name: str, directory: Path = PROFILES) -> Path:
    if not re.fullmatch(r"[\w][\w .-]*\.md", name, re.IGNORECASE):
        raise ValueError("Введите имя файла .md без пути.")
    reserved = re.fullmatch(
        r"CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]",
        name.split(".")[0].rstrip(),
        re.IGNORECASE,
    )
    if reserved:
        raise ValueError("Это имя зарезервировано Windows.")
    path = directory / name
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise ValueError("Профиль должен находиться в папке профилей.")
    return path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8-sig",
        extra="ignore",
        hide_input_in_errors=True,
    )

    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = Field(GEMINI_MODELS[0], min_length=1)
    active_profile: str = "example_legend.md"
    crop_width: int = Field(1280, ge=160, le=7680)
    crop_height: int = Field(720, ge=120, le=4320)
    request_timeout: float = Field(12, ge=0.1, le=60)
    task_timeout: float = Field(30, ge=0.1, le=120)
    audio_max_seconds: float = Field(60, ge=1, le=60)
    audio_max_mib: int = Field(12, ge=1, le=12)
    hotkey_screen: str = "<alt>+q"
    hotkey_snip: str = "<alt>+s"
    hotkey_audio: str = "<alt>+r"
    hotkey_mode: str = "<alt>+m"
    hotkey_hud: str = "<alt>+x"
    hotkey_dock: str = "<alt>+<down>"
    hotkey_cancel: str = "<ctrl>+<shift>+<f12>"
    hotkey_page_up: str = "<alt>+<page_up>"
    hotkey_page_down: str = "<alt>+<page_down>"

    @field_validator("active_profile")
    @classmethod
    def check_profile(cls, value):
        profile_path(value)
        return value

    @field_validator("hotkey_cancel")
    @classmethod
    def migrate_legacy_cancel(cls, value):
        if value.lower().replace(" ", "") == "<alt>+<esc>":
            return "<ctrl>+<shift>+<f12>"
        return value

    @property
    def has_api_key(self):
        key = self.gemini_api_key.get_secret_value().strip()
        return bool(key and key.isascii() and key != "AIzaSyYourActualApiKeyHere")


def validate_hotkeys(settings, overrides=None):
    from pynput.keyboard import HotKey, Key

    blocked_combinations = (
        "<alt>+<esc>",
        "<alt>+<tab>",
        "<alt>+<f4>",
        "<ctrl>+<shift>+<esc>",
        "<ctrl>+<alt>+<delete>",
        "<cmd>+d",
        "<cmd>+l",
        "<cmd>+<tab>",
        "<cmd>+r",
    )
    aliases = {
        Key.alt_l: Key.alt,
        Key.alt_r: Key.alt,
        Key.ctrl_l: Key.ctrl,
        Key.ctrl_r: Key.ctrl,
        Key.shift_l: Key.shift,
        Key.shift_r: Key.shift,
        Key.cmd_l: Key.cmd,
        Key.cmd_r: Key.cmd,
    }
    blocked = [
        frozenset(aliases.get(key, key) for key in HotKey.parse(combo))
        for combo in blocked_combinations
    ]
    seen = set()
    for name in Settings.model_fields:
        if not name.startswith("hotkey_"):
            continue
        value = (overrides or {}).get(name, getattr(settings, name))
        keys = frozenset(aliases.get(key, key) for key in HotKey.parse(value))
        modifiers = {Key.alt, Key.ctrl, Key.shift, Key.cmd}
        if any(combo <= keys for combo in blocked):
            raise ValueError("Системное сочетание Windows недоступно для назначения.")
        if not keys - modifiers or keys in seen:
            raise ValueError(
                "Хоткеи должны быть различными и включать обычную клавишу."
            )
        seen.add(keys)
