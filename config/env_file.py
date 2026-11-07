"""Atomically update selected dotenv keys while preserving unrelated text."""

import os
import re
import tempfile
from pathlib import Path

from dotenv.parser import parse_stream

from config.settings import ENV_FILE
from config.storage import configuration_lock, protect_staging, replace_file

INLINE_COMMENT = re.compile(
    r"""^[\s]*?(?:export[ \t]+)?(?:'[^']+'|[^=\s]+)[ \t]*=[ \t]*"""
    r"""(?:'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|[^'"\r\n]*?)"""
    r"""(?P<comment>[ \t]+#[^\r\n]*)[ \t]*$""",
    re.DOTALL,
)


def update_env(values: dict[str, str], path: Path = ENV_FILE):
    with configuration_lock(path):
        _update_env(values, path)


def _update_env(values: dict[str, str], path: Path):
    from config.migration import SCHEMA_VERSION, read_version

    read_version(path)
    if "SCHEMA_VERSION" in values and values["SCHEMA_VERSION"] != str(SCHEMA_VERSION):
        raise ValueError("Неподдерживаемая версия схемы.")
    values = {"SCHEMA_VERSION": str(SCHEMA_VERSION), **values}
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        mode="w",
        encoding="utf-8",
        newline="",
        prefix=".ghostassist-env-",
        suffix=".tmp",
        delete=False,
    ) as output:
        temp = Path(output.name)
        try:
            protect_staging(path, temp)
            pending = dict(values)
            if path.exists():
                with path.open(encoding="utf-8-sig", newline="") as source:
                    for entry in parse_stream(source):
                        original = entry.original.string
                        key = (
                            "SCHEMA_VERSION"
                            if entry.key and entry.key.upper() == "SCHEMA_VERSION"
                            else entry.key
                        )
                        if key not in values:
                            output.write(original)
                            continue
                        leading = original[: len(original) - len(original.lstrip())]
                        content = original.rstrip("\r\n")
                        newline = original[len(content) :] or "\n"
                        match = INLINE_COMMENT.fullmatch(content)
                        comment = match["comment"] if match else ""
                        output.write(leading + _assignment(key, values[key]))
                        output.write(comment + newline)
                        pending.pop(key, None)
            if pending:
                if output.tell():
                    output.write("\n")
                for key, value in pending.items():
                    output.write(_assignment(key, value) + "\n")
            output.flush()
            os.fsync(output.fileno())
            output.close()
            replace_file(temp, path)
        finally:
            output.close()
            temp.unlink(missing_ok=True)


def _assignment(key, value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise ValueError("Некорректное имя параметра.")
    if "\n" in value or "\r" in value:
        raise ValueError("Настройка должна занимать одну строку.")
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"{key}='{escaped}'"
