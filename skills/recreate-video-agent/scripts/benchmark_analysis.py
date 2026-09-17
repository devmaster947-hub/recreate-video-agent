#!/usr/bin/env python3
"""Build a no-cost technical pre-analysis of a local benchmark video."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    from scripts.model_capabilities import capability, normalize_model, minimum_segment_count
    from scripts.visual_scan import (
        DEFAULT_CHANGE_THRESHOLD,
        DEFAULT_CUT_THRESHOLD,
        VISUAL_SCAN_VERSION,
        scan_video,
    )
except ModuleNotFoundError:
    from model_capabilities import capability, normalize_model, minimum_segment_count
    from visual_scan import (
        DEFAULT_CHANGE_THRESHOLD,
        DEFAULT_CUT_THRESHOLD,
        VISUAL_SCAN_VERSION,
        scan_video,
    )


PTS = re.compile(r"pts_time:([0-9.]+)")
DURATION = re.compile(r"Duration:\s*(\d+):(\d+):([0-9.]+)")
VIDEO_LINE = re.compile(r"Video:\s*([^,]+).*?,\s*(\d{2,5})x(\d{2,5})")
FPS = re.compile(r"([0-9.]+)\s*fps")
AUDIO_LINE = re.compile(r"Audio:\s*([^,]+).*?(\d{4,6}) Hz")
MAX_BENCHMARK_DURATION_SECONDS = 360.0


def analysis_cache_input(
    video: Path,
    model: str,
    duration_mode: str,
    target_duration: int | None,
    scene_threshold: float,
    visual_change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    visual_scan_fps: float | None = None,
) -> dict[str, Any]:
    stat = video.stat()
    return {
        "video": str(video),
        "size": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
        "model": normalize_model(model),
        "durationMode": duration_mode,
        "targetDuration": target_duration,
        "visualCutThreshold": scene_threshold,
        "visualChangeThreshold": visual_change_threshold,
        "visualScanFps": visual_scan_fps,
        "visualScanVersion": VISUAL_SCAN_VERSION,
    }


def analysis_fingerprint(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_cached_analysis(output: Path, cache_input: dict[str, Any]) -> dict[str, Any] | None:
    if not output.is_file():
        return None
    try:
        value = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    expected = analysis_fingerprint(cache_input)
    if value.get("analysisFingerprint") != expected or value.get("analysisInput") != cache_input:
        return None
    if not isinstance(value.get("media"), dict) or not isinstance(value.get("recommendedSegments"), list):
        return None
    return value


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def validate_benchmark_duration(duration: int | float) -> float:
    numeric = float(duration)
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError("无法读取有效的对标视频时长。")
    if numeric > MAX_BENCHMARK_DURATION_SECONDS:
        raise ValueError(
            f"对标视频时长{numeric:.3f}秒超过6分钟（360秒）上限，拒绝复刻；"
            "不能通过仅复刻开场或自定义较短时长绕过。"
        )
    return numeric


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


def analyze(
    ffmpeg: str,
    ffprobe: str | None,
    video: Path,
    threshold: float = DEFAULT_CUT_THRESHOLD,
    *,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    scan_duration: float | None = None,
    scan_fps: float | None = None,
    media: dict[str, Any] | None = None,
) -> dict[str, Any]:
    media = media or probe(ffprobe, video, ffmpeg)
    validate_benchmark_duration(media["duration"])
    window = min(float(scan_duration or media["duration"]), float(media["duration"]))
    scan = scan_video(
        ffmpeg,
        video,
        duration=window,
        fps=scan_fps,
        change_threshold=change_threshold,
        cut_threshold=threshold,
    )
    return {
        "media": media,
        "candidateCuts": scan["candidateCuts"],
        "visualCandidates": scan["visualCandidates"],
        "visualScan": {
            "version": scan["version"],
            "fps": scan["fps"],
            "width": scan["width"],
            "height": scan["height"],
            "sampleCount": scan["sampleCount"],
            "changeThreshold": scan["changeThreshold"],
            "cutThreshold": scan["cutThreshold"],
            "samples": scan["samples"],
        },
        "audio": {"present": media["hasAudio"]},
        "note": "单次低清视觉扫描同时提供切镜候选、连续动作变化和清晰帧评分；不使用大模型选帧。",
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
    source = validate_benchmark_duration(source_duration)
    minimum = int(limits["minDuration"])
    mode = str(duration_mode or "source").strip().lower()
    if mode not in {"source", "custom"}:
        raise ValueError("durationMode 必须是 source 或 custom。")
    if mode == "source":
        if requested_duration is not None:
            raise ValueError("source 模式不得提供自定义时长。")
        target = normalized_source_duration(source, minimum)
        requested = None
        source_label = "source_nearest_integer"
    else:
        if isinstance(requested_duration, bool) or not isinstance(requested_duration, int) or requested_duration <= 0:
            raise ValueError("custom 模式必须提供正整数秒 requestedDuration。")
        if requested_duration > source:
            raise ValueError(f"自定义复刻时长{requested_duration}秒超过原视频时长{source:.3f}秒。")
        target = requested_duration
        requested = requested_duration
        source_label = "custom"
    count = minimum_segment_count(model, target)
    if target < minimum * count:
        raise ValueError(f"{normalize_model(model)}无法用最少{count}个合法Segment覆盖{target}秒。")
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
    parser.add_argument("--output")
    parser.add_argument(
        "--duration-check-only", action="store_true",
        help="只读取并校验对标视频时长；用于展示启动确认单前的免费本地硬门槛。",
    )
    parser.add_argument("--visual-cut-threshold", type=float, default=DEFAULT_CUT_THRESHOLD)
    parser.add_argument("--visual-change-threshold", type=float, default=DEFAULT_CHANGE_THRESHOLD)
    parser.add_argument("--visual-scan-fps", type=float)
    parser.add_argument("--scene-threshold", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--model", default="seedance-2-fast")
    parser.add_argument("--duration-mode", choices=("source", "custom"))
    parser.add_argument("--target-duration", type=int)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    args = parser.parse_args()
    try:
        video = Path(args.video).expanduser().resolve()
        if not video.is_file() or video.stat().st_size <= 0:
            raise ValueError(f"对标视频不存在或为空：{video}")
        ffmpeg = str(resolve("ffmpeg", args.ffmpeg))
        ffprobe = resolve("ffprobe", args.ffprobe, required=False)
        if args.duration_check_only:
            media = probe(ffprobe, video, ffmpeg)
            duration = validate_benchmark_duration(media["duration"])
            print(json.dumps({"ok": True, "video": str(video), "duration": duration, "maximumDuration": MAX_BENCHMARK_DURATION_SECONDS}, ensure_ascii=False))
            return 0
        if not args.output:
            raise ValueError("完整分析必须提供 --output。")
        output = Path(args.output).expanduser().resolve()
        duration_mode = args.duration_mode or ("custom" if args.target_duration is not None else "source")
        effective_cut_threshold = (
            args.scene_threshold if args.scene_threshold is not None else args.visual_cut_threshold
        )
        cache_input = analysis_cache_input(
            video,
            args.model,
            duration_mode,
            args.target_duration,
            effective_cut_threshold,
            args.visual_change_threshold,
            args.visual_scan_fps,
        )
        cached = load_cached_analysis(output, cache_input)
        if cached is not None:
            print(json.dumps({"ok": True, "output": str(output), "reused": True, **cached}, ensure_ascii=False))
            return 0
        output.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        media = probe(ffprobe, video, ffmpeg)
        source_duration = float(media["duration"])
        duration = resolve_replication_duration(source_duration, args.model, duration_mode, args.target_duration)
        value = analyze(
            ffmpeg,
            ffprobe,
            video,
            effective_cut_threshold,
            change_threshold=args.visual_change_threshold,
            scan_duration=float(duration["targetDuration"]),
            scan_fps=args.visual_scan_fps,
            media=media,
        )
        value.update(duration)
        value["sourceFile"] = str(video)
        # 最终Segment/Storyboard anchors由服务端replicationPlan决定；本地不生成最终或候选分段。
        value["recommendedSegments"] = []
        value["analysisInput"] = cache_input
        value["analysisFingerprint"] = analysis_fingerprint(cache_input)
        value["stageTimings"] = {"technicalAnalysisSeconds": round(time.perf_counter() - started, 3)}
        output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(output), "reused": False, **value}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
