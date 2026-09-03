"""Cross-platform FFmpeg discovery."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def find_ffmpeg() -> Path | None:
    """Return the FFmpeg executable found on PATH, without hard-coded paths."""
    candidates = ("ffmpeg.exe", "ffmpeg") if os.name == "nt" else ("ffmpeg", "ffmpeg.exe")
    for executable in candidates:
        resolved = shutil.which(executable)
        if resolved:
            return Path(resolved)
    return None


def require_ffmpeg() -> Path:
    executable = find_ffmpeg()
    if executable is None:
        raise RuntimeError("FFmpeg was not found on PATH. Install FFmpeg and try again.")
    return executable
