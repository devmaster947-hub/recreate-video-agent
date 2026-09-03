#!/usr/bin/env python3
"""Normalize successful Segment videos and concatenate them in timeline order."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

try:
    from scripts import generation_manifest
except ModuleNotFoundError:
    import generation_manifest  # type: ignore[no-redef]


def ordered_segments(manifest: dict) -> list[tuple[int, Path]]:
    raw_segments = manifest.get("videos", manifest.get("segments", {}))
    if not raw_segments:
        raise ValueError("Manifest has no video segments")
    ordered: list[tuple[int, Path]] = []
    entries = (
        [(entry.get("segmentId"), entry) for entry in raw_segments]
        if isinstance(raw_segments, list)
        else list(raw_segments.items())
    )
    for raw_id, entry in entries:
        try:
            segment_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid Segment id: {raw_id}") from exc
        if entry.get("status") != "success":
            raise ValueError(f"Segment {segment_id} is not successful")
        output = entry.get("output_file")
        if not output:
            raise ValueError(f"Segment {segment_id} has no output_file")
        path = Path(output).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"Segment {segment_id} output is missing or empty: {path}")
        ordered.append((segment_id, path))
    ordered.sort(key=lambda item: item[0])
    return ordered


def build_filter(input_count: int) -> str:
    if input_count < 1:
        raise ValueError("At least one Segment is required")
    filters: list[str] = []
    concat_inputs: list[str] = []
    for index in range(input_count):
        filters.append(
            f"[{index}:v]"
            "scale=720:1280:force_original_aspect_ratio=decrease,"
            "pad=720:1280:(ow-iw)/2:(oh-ih)/2,"
            "setsar=1,fps=30,format=yuv420p,"
            f"setpts=PTS-STARTPTS[v{index}]"
        )
        filters.append(
            f"[{index}:a]aresample=48000,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS[a{index}]"
        )
        concat_inputs.append(f"[v{index}][a{index}]")
    filters.append(
        "".join(concat_inputs)
        + f"concat=n={input_count}:v=1:a=1[outv][outa]"
    )
    return ";".join(filters)


def render(inputs: list[Path], output: Path, ffmpeg: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp.mp4")
    if output in inputs or temporary in inputs:
        raise ValueError("Final output must not overwrite a Segment input")
    command = [ffmpeg, "-y"]
    for path in inputs:
        command.extend(["-i", str(path)])
    command.extend(
        [
            "-filter_complex",
            build_filter(len(inputs)),
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
    )
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concatenation failed: {result.stderr[-1000:]}")
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("ffmpeg produced an empty final video")
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()

    try:
        has_path_separator = "/" in args.ffmpeg or "\\" in args.ffmpeg
        ffmpeg = args.ffmpeg if has_path_separator else shutil.which(args.ffmpeg)
        if not ffmpeg and not has_path_separator:
            local_ffmpeg = Path.home() / ".local" / "bin" / args.ffmpeg
            if local_ffmpeg.is_file():
                ffmpeg = str(local_ffmpeg)
        if not ffmpeg:
            raise ValueError("FFmpeg is required for final concatenation")
        manifest_path = Path(args.manifest).expanduser().resolve()
        manifest = generation_manifest.load_manifest(manifest_path)
        ordered = ordered_segments(manifest)
        output = Path(args.output).expanduser().resolve()
        render([path for _, path in ordered], output, ffmpeg)
        generation_manifest.command_set_final(
            argparse.Namespace(
                manifest=str(manifest_path),
                output_file=str(output),
                segment_id=[segment_id for segment_id, _ in ordered],
            )
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "output": str(output),
                    "segment_order": [segment_id for segment_id, _ in ordered],
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
