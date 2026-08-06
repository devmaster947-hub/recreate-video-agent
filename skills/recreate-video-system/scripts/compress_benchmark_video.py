#!/usr/bin/env python3
"""Compress oversized upload videos and images below the byte limit."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence


DEFAULT_MAX_BYTES = 20_000_000
IMAGE_SUFFIXES = {".avif", ".jpeg", ".jpg", ".png", ".webp"}
_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_BUDGET_FACTORS = (0.90, 0.78, 0.66, 0.54)


class CompressionError(RuntimeError):
    """Raised when a safe upload copy cannot be produced."""


def resolve_ffmpeg(value: str | os.PathLike[str] | None = None) -> Path:
    if value is not None:
        candidate = Path(value).expanduser().resolve()
    else:
        discovered = shutil.which("ffmpeg")
        candidate = (
            Path(discovered).resolve()
            if discovered
            else (Path.home() / ".local" / "bin" / "ffmpeg").resolve()
        )
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise CompressionError("上传素材超过 20 MB，但未找到可执行的 FFmpeg。")
    return candidate


def _run(
    command: Sequence[str],
    *,
    runner: Callable[..., Any] = subprocess.run,
    timeout: float = 3600.0,
) -> Any:
    try:
        return runner(
            list(command),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise CompressionError("对标视频压缩超时。") from None
    except OSError as exc:
        raise CompressionError(f"无法运行 FFmpeg：{exc}") from None


def probe_duration(
    source: Path,
    ffmpeg: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> float:
    result = _run(
        [str(ffmpeg), "-hide_banner", "-i", str(source)],
        runner=runner,
        timeout=60.0,
    )
    match = _DURATION_PATTERN.search(
        f"{getattr(result, 'stderr', '')}\n{getattr(result, 'stdout', '')}"
    )
    if match is None:
        raise CompressionError("无法读取对标视频时长。")
    hours, minutes, seconds = match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if duration <= 0:
        raise CompressionError("对标视频时长必须大于 0。")
    return duration


def _available_output(source: Path, output_dir: Path, *, suffix: str = ".mp4") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    requested = output_dir / f"{source.stem}-under-20mb{suffix}"
    candidate = requested
    version = 2
    while candidate.exists():
        candidate = requested.with_name(f"{requested.stem}-v{version}{requested.suffix}")
        version += 1
    return candidate


def _clear_passlog(prefix: Path) -> None:
    for path in prefix.parent.glob(prefix.name + "*"):
        if path.is_file():
            path.unlink(missing_ok=True)


def prepare_benchmark_video(
    file_path: str | os.PathLike[str],
    *,
    output_dir: str | os.PathLike[str] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    ffmpeg_path: str | os.PathLike[str] | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Return the original path or a verified, non-overwriting compressed copy."""
    source = Path(file_path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        raise CompressionError(f"对标视频不存在或为空：{source}")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise CompressionError("max_bytes 必须是正整数。")
    original_bytes = source.stat().st_size
    if original_bytes <= max_bytes:
        return {
            "filePath": str(source),
            "compressed": False,
            "originalBytes": original_bytes,
            "uploadBytes": original_bytes,
            "maxBytes": max_bytes,
        }

    ffmpeg = resolve_ffmpeg(ffmpeg_path)
    duration = probe_duration(source, ffmpeg, runner=runner)
    directory = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else Path(tempfile.gettempdir()) / "recreate-video-system" / "benchmark-uploads"
    )
    output = _available_output(source, directory)
    passlog = output.with_suffix("").with_name(output.stem + "-passlog")
    last_error = "FFmpeg 未生成有效文件。"

    try:
        for factor in _BUDGET_FACTORS:
            target_bits_per_second = max(96_000, int(max_bytes * factor * 8 / duration))
            audio_bits_per_second = min(96_000, max(32_000, target_bits_per_second // 8))
            video_bits_per_second = max(
                48_000, target_bits_per_second - audio_bits_per_second - 16_000
            )
            common = [
                str(ffmpeg), "-y", "-i", str(source),
                "-map", "0:v:0", "-c:v", "libx264", "-preset", "medium",
                "-b:v", str(video_bits_per_second), "-pix_fmt", "yuv420p",
                "-passlogfile", str(passlog),
            ]
            first = _run(
                [*common, "-pass", "1", "-an", "-f", "null", os.devnull],
                runner=runner,
            )
            if getattr(first, "returncode", 1) != 0:
                last_error = (getattr(first, "stderr", "") or "FFmpeg 第一遍编码失败。")[-1000:]
                continue
            second = _run(
                [
                    *common, "-pass", "2", "-map", "0:a?", "-c:a", "aac",
                    "-b:a", str(audio_bits_per_second), "-movflags", "+faststart",
                    str(output),
                ],
                runner=runner,
            )
            if getattr(second, "returncode", 1) != 0:
                last_error = (getattr(second, "stderr", "") or "FFmpeg 第二遍编码失败。")[-1000:]
                output.unlink(missing_ok=True)
                continue
            upload_bytes = output.stat().st_size if output.is_file() else 0
            if 0 < upload_bytes <= max_bytes:
                return {
                    "filePath": str(output),
                    "compressed": True,
                    "originalBytes": original_bytes,
                    "uploadBytes": upload_bytes,
                    "maxBytes": max_bytes,
                }
            last_error = f"压缩后仍为 {upload_bytes} 字节，超过 {max_bytes} 字节。"
            output.unlink(missing_ok=True)
    finally:
        _clear_passlog(passlog)

    output.unlink(missing_ok=True)
    raise CompressionError(f"无法将对标视频压缩到 {max_bytes} 字节以内：{last_error}")


def prepare_upload_image(
    file_path: str | os.PathLike[str],
    *,
    output_dir: str | os.PathLike[str] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    ffmpeg_path: str | os.PathLike[str] | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Return the original image or a verified JPEG copy below the upload limit."""
    source = Path(file_path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        raise CompressionError(f"上传图片不存在或为空：{source}")
    if source.suffix.lower() not in IMAGE_SUFFIXES:
        raise CompressionError(f"不支持压缩此图片格式：{source.suffix or '无扩展名'}")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise CompressionError("max_bytes 必须是正整数。")
    original_bytes = source.stat().st_size
    if original_bytes <= max_bytes:
        return {
            "filePath": str(source),
            "compressed": False,
            "originalBytes": original_bytes,
            "uploadBytes": original_bytes,
            "maxBytes": max_bytes,
        }

    ffmpeg = resolve_ffmpeg(ffmpeg_path)
    directory = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else Path(tempfile.gettempdir()) / "recreate-video-system" / "image-uploads"
    )
    output = _available_output(source, directory, suffix=".jpg")
    last_error = "FFmpeg 未生成有效文件。"
    attempts = ((4096, 3), (3072, 5), (2560, 7), (2048, 9), (1536, 12), (1024, 16))
    for max_edge, quality in attempts:
        result = _run(
            [
                str(ffmpeg), "-y", "-i", str(source), "-map", "0:v:0",
                "-frames:v", "1", "-vf",
                f"scale={max_edge}:{max_edge}:force_original_aspect_ratio=decrease",
                "-q:v", str(quality), str(output),
            ],
            runner=runner,
            timeout=600.0,
        )
        if getattr(result, "returncode", 1) != 0:
            last_error = (getattr(result, "stderr", "") or "FFmpeg 图片压缩失败。")[-1000:]
            output.unlink(missing_ok=True)
            continue
        upload_bytes = output.stat().st_size if output.is_file() else 0
        if 0 < upload_bytes <= max_bytes:
            return {
                "filePath": str(output),
                "compressed": True,
                "originalBytes": original_bytes,
                "uploadBytes": upload_bytes,
                "maxBytes": max_bytes,
            }
        last_error = f"压缩后仍为 {upload_bytes} 字节，超过 {max_bytes} 字节。"
        output.unlink(missing_ok=True)
    raise CompressionError(f"无法将上传图片压缩到 {max_bytes} 字节以内：{last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file_path")
    parser.add_argument("--output-dir")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--ffmpeg")
    args = parser.parse_args()
    try:
        result = prepare_benchmark_video(
            args.file_path,
            output_dir=args.output_dir,
            max_bytes=args.max_bytes,
            ffmpeg_path=args.ffmpeg,
        )
    except CompressionError as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
