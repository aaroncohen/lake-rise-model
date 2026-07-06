"""Filesystem helpers shared across the persistence layers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: str | Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + ``os.replace``, so a crash mid-write never
    leaves a truncated file that would break the next read. Creates parent dirs. Atomic on POSIX."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)          # atomic on POSIX
    finally:
        Path(tmp).unlink(missing_ok=True)
