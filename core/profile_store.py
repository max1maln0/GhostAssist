"""Atomic updates for user-authored profile text."""

import os
import tempfile
from pathlib import Path

from config.settings import profile_path
from config.storage import protect_staging, replace_file


def write_profile(name: str, text: str, *, directory: Path | None = None) -> None:
    path = profile_path(name) if directory is None else profile_path(name, directory)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as output:
            temp = Path(output.name)
            protect_staging(path, temp)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        # Recheck the destination before replacing it, including symlink rejection.
        path = (
            profile_path(name) if directory is None else profile_path(name, directory)
        )
        replace_file(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
