#!/usr/bin/env python3
"""Merge generated video segments into one final MP4 with ffmpeg.

This script is intentionally small and dependency-free. It uses ffmpeg concat
with stream copy first, then falls back to re-encoding if segment codecs are not
compatible.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def validate_inputs(paths: list[Path]) -> None:
    if not paths:
        raise SystemExit("No input videos supplied.")
    for path in paths:
        if not path.is_file():
            raise SystemExit(f"Input video not found: {path}")
        if path.stat().st_size <= 0:
            raise SystemExit(f"Input video is empty: {path}")


def ffmpeg_exists() -> None:
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found. Install ffmpeg or merge videos manually.")


def write_concat_file(paths: list[Path]) -> Path:
    temp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    with temp:
        for path in paths:
            escaped = str(path.resolve()).replace("'", "'\\''")
            temp.write(f"file '{escaped}'\n")
    return Path(temp.name)


def merge_copy(list_file: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return run([
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(output),
    ])


def merge_reencode(list_file: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return run([
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output),
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge video segments into final_video.mp4")
    parser.add_argument("--inputs", nargs="+", required=True, help="Input segment mp4 files in order")
    parser.add_argument("--output", default="final_video.mp4", help="Output mp4 path")
    args = parser.parse_args()

    ffmpeg_exists()
    inputs = [Path(p).expanduser() for p in args.inputs]
    output = Path(args.output).expanduser()
    validate_inputs(inputs)
    output.parent.mkdir(parents=True, exist_ok=True)

    list_file = write_concat_file(inputs)
    try:
        result = merge_copy(list_file, output)
        if result.returncode != 0 or not output.exists() or output.stat().st_size <= 0:
            result = merge_reencode(list_file, output)
        if result.returncode != 0:
            sys.stderr.write(result.stderr[-4000:])
            return result.returncode
        if not output.exists() or output.stat().st_size <= 0:
            sys.stderr.write("Merge failed: output file was not created or is empty.\n")
            return 1
        print(str(output.resolve()))
        return 0
    finally:
        try:
            list_file.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
