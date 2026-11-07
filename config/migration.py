"""Conservative, atomic dotenv schema upgrades without rewriting user values."""

from pathlib import Path

from dotenv.parser import parse_stream

SCHEMA_VERSION = 1


class ConfigurationError(ValueError):
    pass


def read_version(path: Path) -> int:
    if not path.exists():
        return 0
    versions = []
    try:
        with path.open(encoding="utf-8-sig") as source:
            for binding in parse_stream(source):
                if binding.error:
                    raise ConfigurationError("Некорректный синтаксис .env.")
                if binding.key == "SCHEMA_VERSION":
                    versions.append(binding.value)
    except UnicodeError:
        raise ConfigurationError("Файл .env должен иметь кодировку UTF-8.") from None
    if not versions:
        return 0
    if len(versions) != 1 or versions[0] != str(SCHEMA_VERSION):
        raise ConfigurationError("Неизвестная или неоднозначная версия схемы .env.")
    return SCHEMA_VERSION


def migrate_env(path: Path) -> bool:
    """Validate the complete persisted configuration before atomically upgrading."""
    version = read_version(path)
    if version == SCHEMA_VERSION or not path.exists():
        return False
    # Validate the file independently of overrides: environment must not conceal
    # malformed or future-version persisted data. No values are printed.
    from dotenv import dotenv_values

    from config.env_file import update_env
    from config.settings import Settings

    data = {
        key.lower(): value
        for key, value in dotenv_values(
            path, encoding="utf-8-sig", interpolate=True
        ).items()
        if value is not None
    }
    Settings(_env_file=None, **data)
    update_env({"SCHEMA_VERSION": str(SCHEMA_VERSION)}, path)
    return True
