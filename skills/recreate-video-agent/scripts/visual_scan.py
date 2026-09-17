#!/usr/bin/env python3
"""Single-pass low-resolution visual scan for action/cut anchors.

The scanner intentionally avoids repeated random seeks. FFmpeg decodes the requested
window once and streams tiny grayscale frames to Python, where visual change and
frame-quality metrics are computed in memory.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any, Iterable

SCAN_WIDTH = 48
SCAN_HEIGHT = 27
DEFAULT_CHANGE_THRESHOLD = 0.07
DEFAULT_CUT_THRESHOLD = 0.14
VISUAL_SCAN_VERSION = "single-pass-gray-v1"


def adaptive_scan_fps(duration: float) -> float:
    """Keep short-video motion coverage dense while capping long-video sample count."""
    duration = float(duration)
    if duration <= 30:
        return 4.0
    if duration <= 60:
        return 3.0
    return 2.0


def frame_distance(first: bytes, second: bytes) -> float:
    if len(first) != len(second) or not first:
        raise ValueError("视觉扫描帧尺寸不一致。")
    return sum(abs(a - b) for a, b in zip(first, second)) / (len(first) * 255.0)


def frame_quality(frame: bytes, width: int = SCAN_WIDTH, height: int = SCAN_HEIGHT) -> float:
    """Cheap clarity/exposure score derived from the already-decoded grayscale sample."""
    if len(frame) != width * height:
        raise ValueError("视觉扫描帧长度与尺寸不匹配。")
    count = len(frame)
    total = sum(frame)
    mean = total / count
    square_mean = sum(value * value for value in frame) / count
    variance = max(0.0, square_mean - mean * mean)
    stddev = math.sqrt(variance)

    horizontal = 0
    vertical = 0
    horizontal_count = height * max(0, width - 1)
    vertical_count = max(0, height - 1) * width
    for y in range(height):
        row = y * width
        for x in range(width - 1):
            horizontal += abs(frame[row + x + 1] - frame[row + x])
    for y in range(height - 1):
        row = y * width
        below = row + width
        for x in range(width):
            vertical += abs(frame[below + x] - frame[row + x])
    edge_mean = (horizontal + vertical) / max(1, horizontal_count + vertical_count)

    contrast_score = min(1.0, stddev / 64.0)
    edge_score = min(1.0, edge_mean / 32.0)
    exposure_score = max(0.0, 1.0 - abs(mean - 128.0) / 128.0)
    return round(0.4 * contrast_score + 0.4 * edge_score + 0.2 * exposure_score, 4)


def _suppress_nearby(candidates: list[dict[str, Any]], min_gap: float) -> list[dict[str, Any]]:
    """Keep the strongest visual peak inside each local time neighborhood."""
    kept: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (-float(item["changeScore"]), float(item["timestamp"]))):
        timestamp = float(candidate["timestamp"])
        if any(abs(timestamp - float(item["timestamp"])) < min_gap for item in kept):
            continue
        kept.append(candidate)
    return sorted(kept, key=lambda item: float(item["timestamp"]))


def analyze_frames(
    frames: Iterable[bytes],
    *,
    fps: float,
    duration: float,
    width: int = SCAN_WIDTH,
    height: int = SCAN_HEIGHT,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    cut_threshold: float = DEFAULT_CUT_THRESHOLD,
) -> dict[str, Any]:
    """Convert a grayscale frame stream into compact scan metrics and visual peaks."""
    if fps <= 0:
        raise ValueError("visual scan fps 必须大于0。")
    samples: list[dict[str, Any]] = []
    history: list[bytes] = []
    lookback = max(1, round(fps))

    for index, frame in enumerate(frames):
        if len(frame) != width * height:
            raise ValueError("视觉扫描读取到不完整帧。")
        timestamp = min(float(duration), index / fps)
        previous_change = frame_distance(history[-1], frame) if history else 0.0
        state_change = frame_distance(history[-lookback], frame) if len(history) >= lookback else previous_change
        # Adjacent-frame change catches cuts/fast motion; 1s state drift catches
        # meaningful continuous actions that do not contain a real edit.
        change_score = max(previous_change, state_change * 0.72)
        samples.append({
            "timestamp": round(timestamp, 3),
            "changeScore": round(change_score, 4),
            "adjacentChange": round(previous_change, 4),
            "stateChange": round(state_change, 4),
            "qualityScore": frame_quality(frame, width, height),
        })
        history.append(frame)
        if len(history) > lookback + 1:
            history.pop(0)

    action_candidates = [
        {**sample, "eventType": "visual_change"}
        for sample in samples[1:]
        if float(sample["changeScore"]) >= change_threshold
    ]
    cut_candidates = [
        {**sample, "eventType": "cut"}
        for sample in samples[1:]
        if float(sample["adjacentChange"]) >= cut_threshold
    ]

    # Suppression prevents steady camera motion from consuming all 9 storyboard slots.
    actions = _suppress_nearby(action_candidates, max(0.45, 1.5 / fps))
    cuts = _suppress_nearby(cut_candidates, max(0.45, 1.5 / fps))
    cut_times = {round(float(item["timestamp"]), 3) for item in cuts}
    merged = []
    for item in actions:
        timestamp = round(float(item["timestamp"]), 3)
        if any(abs(timestamp - cut) < max(0.26, 0.8 / fps) for cut in cut_times):
            continue
        # The 1-second state comparison naturally remains high for a short time
        # after a hard cut. Do not mislabel that stale cross-scene difference as
        # a second action unless the adjacent frames are also changing.
        if any(
            0 < timestamp - cut <= 1.05
            and float(item.get("adjacentChange", 0)) < change_threshold * 0.7
            for cut in cut_times
        ):
            continue
        merged.append(item)
    merged.extend(cuts)
    merged.sort(key=lambda item: float(item["timestamp"]))

    return {
        "version": VISUAL_SCAN_VERSION,
        "fps": fps,
        "width": width,
        "height": height,
        "sampleCount": len(samples),
        "changeThreshold": change_threshold,
        "cutThreshold": cut_threshold,
        "samples": samples,
        "visualCandidates": merged,
        "candidateCuts": [round(float(item["timestamp"]), 3) for item in cuts],
    }


def scan_video(
    ffmpeg: str,
    video: Path,
    *,
    duration: float,
    fps: float | None = None,
    width: int = SCAN_WIDTH,
    height: int = SCAN_HEIGHT,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    cut_threshold: float = DEFAULT_CUT_THRESHOLD,
) -> dict[str, Any]:
    fps = float(fps or adaptive_scan_fps(duration))
    if fps <= 0 or not math.isfinite(fps):
        raise ValueError("visual scan fps 无效。")
    frame_size = width * height
    command = [
        ffmpeg,
        "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-t", f"{float(duration):.3f}",
        "-an",
        "-vf", f"fps={fps:g},scale={width}:{height}:flags=area,format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("无法启动视觉扫描。")

    frames: list[bytes] = []
    while True:
        parts: list[bytes] = []
        remaining = frame_size
        while remaining:
            part = process.stdout.read(remaining)
            if not part:
                break
            parts.append(part)
            remaining -= len(part)
        if not parts:
            break
        chunk = b"".join(parts)
        if len(chunk) != frame_size:
            process.kill()
            process.wait()
            raise RuntimeError("FFmpeg视觉扫描返回了不完整帧。")
        frames.append(chunk)
    stderr = process.stderr.read().decode("utf-8", errors="replace")
    returncode = process.wait()
    if returncode:
        raise RuntimeError(stderr[-2000:] or "FFmpeg视觉扫描失败。")
    if not frames:
        raise RuntimeError("视觉扫描没有返回任何帧。")
    return analyze_frames(
        frames,
        fps=fps,
        duration=duration,
        width=width,
        height=height,
        change_threshold=change_threshold,
        cut_threshold=cut_threshold,
    )
