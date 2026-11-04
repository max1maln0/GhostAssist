from pathlib import Path
from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

class AppSettings(BaseSettings):
    gemini_api_key: SecretStr = Field(..., alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.5-flash", alias="GEMINI_MODEL")
    active_profile: str = Field("example_legend.md", alias="ACTIVE_PROFILE")
    
    # Максимальное разрешение сжатия в RAM для минимального пинга
    crop_width: int = Field(1280, alias="CROP_WIDTH")
    crop_height: int = Field(720, alias="CROP_HEIGHT")

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def profile_path(self) -> Path:
        path = BASE_DIR / "config" / "profiles" / self.active_profile
        if not path.exists():
            return BASE_DIR / "config" / "profiles" / "example_legend.md"
        return path

settings = AppSettings()