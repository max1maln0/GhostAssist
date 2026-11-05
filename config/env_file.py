"""Atomically update selected dotenv keys while preserving unrelated text."""

import os
import re
import tempfile
from pathlib import Path

from dotenv.parser import parse_stream

from config.settings import ENV_FILE

INLINE_COMMENT = re.compile(
    r"""^[\s]*?(?:export[ \t]+)?(?:'[^']+'|[^=\s]+)[ \t]*=[ \t]*"""
    r"""(?:'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|[^'"\r\n]*?)"""
    r"""(?P<comment>[ \t]+#[^\r\n]*)[ \t]*$""",
    re.DOTALL,
)


def update_env(values: dict[str, str], path: Path = ENV_FILE):
    with tempfile.NamedTemporaryFile(
        dir=path.parent, mode="w", encoding="utf-8", newline="", delete=False
    ) as output:
        temp = Path(output.name)
        try:
            pending = dict(values)
            if path.exists():
                with path.open(encoding="utf-8-sig", newline="") as source:
                    for entry in parse_stream(source):
                        original = entry.original.string
                        if entry.key not in values:
                            output.write(original)
                            continue
                        leading = original[: len(original) - len(original.lstrip())]
                        content = original.rstrip("\r\n")
                        newline = original[len(content) :] or "\n"
                        match = INLINE_COMMENT.fullmatch(content)
                        comment = match["comment"] if match else ""
                        output.write(
                            leading + _assignment(entry.key, values[entry.key])
                        )
                        output.write(comment + newline)
                        pending.pop(entry.key, None)
            if pending:
                if output.tell():
                    output.write("\n")
                for key, value in pending.items():
                    output.write(_assignment(key, value) + "\n")
            output.close()
            os.replace(temp, path)
        finally:
            output.close()
            temp.unlink(missing_ok=True)


def _assignment(key, value):
    if "\n" in value or "\r" in value:
        raise ValueError("Настройка должна занимать одну строку.")
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"{key}='{escaped}'"
