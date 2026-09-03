#!/usr/bin/env python3
"""Build a no-cost technical pre-analysis of a local benchmark video."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from scripts.model_capabilities import capability, normalize_model, plan_segment_windows
except ModuleNotFoundError:
    from model_capabilities import capability, normalize_model, plan_segment_windows


PTS = re.compile(r"pts_time:([0-9.]+)")
BLACK = re.compile(r"black_start:([0-9.]+).*?black_end:([0-9.]+)")
MEAN = re.compile(r"mean_volume:\s*(-?[0-9.]+) dB")
MAX = re.compile(r"max_volume:\s*(-?[0-9.]+) dB")
DURATION = re.compile(r"Duration:\s*(\d+):(\d+):([0-9.]+)")
VIDEO_LINE = re.compile(r"Video:\s*([^,]+).*?,\s*(\d{2,5})x(\d{2,5})")
FPS = re.compile(r"([0-9.]+)\s*fps")
AUDIO_LINE = re.compile(r"Audio:\s*([^,]+).*?(\d{4,6}) Hz")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def probe(ffprobe: str | None, video: Path, ffmpeg: str | None = None) -> dict[str, Any]:
    if not ffprobe:
        if not ffmpeg:
            raise RuntimeError("ffprobe不可用且未提供ffmpeg回退。")
        result = run([ffmpeg, "-hide_banner", "-i", str(video)])
        text = result.stderr
        duration_match = DURATION.search(text)
        video_match = VIDEO_LINE.search(text)
        audio_match = AUDIO_LINE.search(text)
        if not duration_match or not video_match:
            raise RuntimeError("无法从ffmpeg读取视频媒体信息。")
        hours, minutes, seconds = duration_match.groups()
        fps = FPS.search(text)
        return {
            "duration": int(hours) * 3600 + int(minutes) * 60 + float(seconds),
            "width": int(video_match.group(2)), "height": int(video_match.group(3)),
            "frameRate": fps.group(1) if fps else "", "videoCodec": video_match.group(1).strip(),
            "hasAudio": bool(audio_match), "audioCodec": audio_match.group(1).strip() if audio_match else "",
            "audioSampleRate": int(audio_match.group(2)) if audio_match else 0,
        }
    result = run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)])
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:] or "ffprobe failed")
    raw = json.loads(result.stdout)
    streams = raw.get("streams", [])
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), {})
    return {
        "duration": float(raw.get("format", {}).get("duration") or video_stream.get("duration") or 0),
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "frameRate": video_stream.get("avg_frame_rate", ""),
        "videoCodec": video_stream.get("codec_name", ""),
        "hasAudio": bool(audio_stream),
        "audioCodec": audio_stream.get("codec_name", ""),
        "audioSampleRate": int(audio_stream.get("sample_rate") or 0),
    }


def analyze(ffmpeg: str, ffprobe: str | None, video: Path, threshold: float = 0.30) -> dict[str, Any]:
    media = probe(ffprobe, video, ffmpeg)
    scene = run([
        ffmpeg, "-hide_banner", "-i", str(video), "-vf",
        f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-",
    ])
    cuts = sorted({round(float(value), 3) for value in PTS.findall(scene.stderr)})
    black = run([
        ffmpeg, "-hide_banner", "-i", str(video), "-vf",
        "blackdetect=d=0.08:pix_th=0.10", "-an", "-f", "null", "-",
    ])
    black_ranges = [
        {"start": float(start), "end": float(end)} for start, end in BLACK.findall(black.stderr)
    ]
    audio: dict[str, Any] = {"present": media["hasAudio"]}
    if media["hasAudio"]:
        volume = run([
            ffmpeg, "-hide_banner", "-i", str(video), "-vn", "-af", "volumedetect",
            "-f", "null", "-",
        ])
        mean = MEAN.search(volume.stderr)
        maximum = MAX.search(volume.stderr)
        audio.update({
            "meanVolumeDb": float(mean.group(1)) if mean else None,
            "maxVolumeDb": float(maximum.group(1)) if maximum else None,
        })
    return {
        "media": media,
        "candidateCuts": cuts,
        "blackRanges": black_ranges,
        "audio": audio,
        "sceneThreshold": threshold,
        "note": "候选切镜仅用于 Codex 复核，不得替代完整视频视觉判断。",
    }


def resolve(name: str, explicit: str | None, *, required: bool = True) -> str | None:
    if explicit:
        return explicit
    found = shutil.which(name)
    local = Path.home() / ".local" / "bin" / name
    value = found or (str(local) if local.is_file() else "")
    if not value and required:
        raise ValueError(f"当前环境缺少 {name}。")
    return value or None


def normalized_source_duration(duration: int | float, minimum_duration: int = 4) -> int:
    numeric = float(duration)
    if numeric < minimum_duration:
        raise ValueError(f"源视频总时长短于模型最短时长{minimum_duration}秒；不得静默延长。")
    return int(math.floor(numeric + 0.5))


def resolve_replication_duration(
    source_duration: int | float,
    model: str,
    duration_mode: str = "source",
    requested_duration: int | None = None,
) -> dict[str, Any]:
    limits = capability(model)
    if limits is None:
        raise ValueError(f"不支持的视频模型：{normalize_model(model)}。")
    source = float(source_duration)
    minimum = int(limits["minDuration"])
    mode = str(duration_mode or "source").strip().lower()
    if mode not in {"source", "opening_10", "custom"}:
        raise ValueError("durationMode 必须是 source、opening_10 或 custom。")
    if mode == "source":
        if requested_duration is not None:
            raise ValueError("source 模式不得提供自定义时长。")
        target = normalized_source_duration(source, minimum)
        requested = None
        source_label = "source_nearest_integer"
    elif mode == "opening_10":
        if requested_duration not in (None, 10):
            raise ValueError("opening_10 模式固定为开场10秒。")
        target = 10 if source >= 10 else normalized_source_duration(source, minimum)
        requested = 10
        source_label = "opening_10"
    else:
        if isinstance(requested_duration, bool) or not isinstance(requested_duration, int) or requested_duration <= 0:
            raise ValueError("custom 模式必须提供正整数秒 requestedDuration。")
        if requested_duration > source:
            raise ValueError(f"自定义复刻时长{requested_duration}秒超过原视频时长{source:.3f}秒。")
        target = requested_duration
        requested = requested_duration
        source_label = "custom"
    plan_segment_windows(model, target)
    return {
        "durationMode": mode,
        "requestedDuration": requested,
        "targetDuration": int(target),
        "targetDurationSource": source_label,
        "replicationWindow": {"start": 0, "end": int(target)},
        "durationAdjustmentSeconds": round(float(target) - source, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--scene-threshold", type=float, default=0.30)
    parser.add_argument("--model", default="seedance-2-fast")
    parser.add_argument("--duration-mode", choices=("source", "opening_10", "custom"))
    parser.add_argument("--target-duration", type=int)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    args = parser.parse_args()
    try:
        video = Path(args.video).expanduser().resolve()
        if not video.is_file() or video.stat().st_size <= 0:
            raise ValueError(f"对标视频不存在或为空：{video}")
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        value = analyze(str(resolve("ffmpeg", args.ffmpeg)), resolve("ffprobe", args.ffprobe, required=False), video, args.scene_threshold)
        source_duration = float(value["media"]["duration"])
        duration_mode = args.duration_mode or ("custom" if args.target_duration is not None else "source")
        duration = resolve_replication_duration(source_duration, args.model, duration_mode, args.target_duration)
        value.update(duration)
        value["recommendedSegments"] = plan_segment_windows(args.model, value["targetDuration"], value["candidateCuts"])
        output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(output), **value}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
