from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    gemini_api_key: SecretStr = Field(..., alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-3.5-flash-lite", alias="GEMINI_MODEL")
    active_profile: str = Field("example_legend.md", alias="ACTIVE_PROFILE")
    crop_width: int = Field(1280, alias="CROP_WIDTH")
    crop_height: int = Field(720, alias="CROP_HEIGHT")

    # Настраиваемые горячие клавиши
    hotkey_screen: str = Field("<alt>+q", alias="HOTKEY_SCREEN")
    hotkey_audio: str = Field("<alt>+r", alias="HOTKEY_AUDIO")
    hotkey_mode: str = Field("<alt>+m", alias="HOTKEY_MODE")
    hotkey_hud: str = Field("<alt>+x", alias="HOTKEY_HUD")
    hotkey_snip: str = Field("<alt>+s", alias="HOTKEY_SNIP")


settings = Settings()