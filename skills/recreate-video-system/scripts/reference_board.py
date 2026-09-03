#!/usr/bin/env python3
"""Compose multiple product identity images into one deterministic reference board."""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path


def render(images: list[Path], output: Path, ffmpeg: str | None = None) -> Path:
    if not images:
        raise ValueError("产品身份板至少需要一张图片。")
    executable = ffmpeg or shutil.which("ffmpeg")
    local = Path.home() / ".local" / "bin" / "ffmpeg"
    executable = executable or (str(local) if local.is_file() else "")
    if not executable:
        raise ValueError("当前环境缺少 ffmpeg。")
    columns = min(3, math.ceil(math.sqrt(len(images))))
    command = [executable, "-y"]
    for image in images:
        command.extend(["-i", str(image)])
    filters: list[str] = []
    labels: list[str] = []
    for index in range(len(images)):
        label = f"i{index}"
        filters.append(f"[{index}:v]scale=512:512:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2:white[{label}]")
        labels.append(f"[{label}]")
    total = len(images)
    layout = "|".join(f"{(index % columns) * 512}_{(index // columns) * 512}" for index in range(total))
    filters.append("".join(labels) + f"xstack=inputs={total}:layout={layout}[out]")
    output.parent.mkdir(parents=True, exist_ok=True)
    command.extend(["-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", "1", str(output)])
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode or not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError(result.stderr[-2000:] or "产品身份板生成失败。")
    return output
