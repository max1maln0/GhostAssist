"""Atomic updates for user-authored profile text."""

import os
import tempfile
from pathlib import Path

from config.settings import profile_path


def write_profile(name: str, text: str) -> None:
    path = profile_path(name)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as output:
            temp = Path(output.name)
            output.write(text)
        # Recheck the destination before replacing it, including symlink rejection.
        path = profile_path(name)
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
