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
    audio_device: str = ""
    vad_enabled: bool = False
    auto_send_transcript: bool = False
    vad_min_speech_ms: int = Field(300, ge=30, le=3000)
    vad_silence_ms: int = Field(900, ge=100, le=5000)
    vad_max_seconds: int = Field(30, ge=1, le=60)
    vad_sensitivity: int = Field(2, ge=0, le=3)
    memory_enabled: bool = True
    memory_depth: int = Field(6, ge=1, le=10)
    close_to_tray: bool = True
    hotkey_dashboard: str = "<ctrl>+<shift>+<f9>"
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


HOTKEY_LABELS = {
    "hotkey_dashboard": "Показать / скрыть Dashboard",
    "hotkey_screen": "Фуллскрин скрин",
    "hotkey_snip": "Снайпер (выделение)",
    "hotkey_audio": "Звук (Loopback)",
    "hotkey_mode": "TEST ↔ INTERVIEW",
    "hotkey_hud": "Скрыть / Показать HUD",
    "hotkey_cancel": "Отменить задачу",
    "hotkey_page_up": "Прокрутка HUD вверх",
    "hotkey_page_down": "Прокрутка HUD вниз",
    "hotkey_dock": "Сместить (Верх / Низ)",
}


class HotkeyValidationError(ValueError):
    pass


def format_hotkey(value):
    names = {
        "ctrl": "Ctrl",
        "shift": "Shift",
        "alt": "Alt",
        "cmd": "Win",
        "page_up": "Page Up",
        "page_down": "Page Down",
        "esc": "Esc",
        "enter": "Enter",
        "space": "Space",
        "down": "↓",
        "up": "↑",
        "left": "←",
        "right": "→",
        "home": "Home",
        "end": "End",
        "delete": "Delete",
        "tab": "Tab",
        "pause": "Pause",
        "scroll_lock": "Scroll Lock",
    }
    result = []
    for part in value.split("+"):
        key = part.strip().strip("<>").lower()
        result.append(names.get(key, key.upper()))
    return " + ".join(result)


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

    def parse(value):
        try:
            return frozenset(aliases.get(key, key) for key in HotKey.parse(value))
        except (ValueError, TypeError):
            raise HotkeyValidationError("Не удалось распознать клавишу.") from None

    blocked = [parse(combo) for combo in blocked_combinations]
    overrides = overrides or {}
    parsed = {}
    for name in HOTKEY_LABELS:
        value = overrides.get(name, getattr(settings, name))
        keys = parse(value)
        if any(combo <= keys for combo in blocked):
            raise HotkeyValidationError(
                f"Комбинация {format_hotkey(value)} зарезервирована Windows."
            )
        modifiers = {Key.alt, Key.ctrl, Key.shift, Key.cmd}
        if not keys - modifiers:
            raise HotkeyValidationError("Не удалось распознать клавишу.")
        if name in overrides and not keys & modifiers:
            key = value.strip().lower()
            safe = key in {"<pause>", "<scroll_lock>"} or key in {
                f"<f{i}>" for i in range(1, 13)
            }
            if not safe:
                raise HotkeyValidationError(
                    "Для этой комбинации необходим хотя бы один модификатор."
                )
        parsed[name] = keys

    # Identify the conflicting existing action even when it follows the edited field.
    for name in overrides:
        for other, keys in parsed.items():
            if other != name and parsed.get(name) == keys:
                raise HotkeyValidationError(
                    f"Комбинация {format_hotkey(overrides[name])} уже используется "
                    f"для действия «{HOTKEY_LABELS[other]}»."
                )
    seen = {}
    for name, keys in parsed.items():
        if keys in seen:
            raise HotkeyValidationError(
                f"Комбинация {format_hotkey(getattr(settings, name))} уже используется "
                f"для действия «{HOTKEY_LABELS[seen[keys]]}»."
            )
        seen[keys] = name
