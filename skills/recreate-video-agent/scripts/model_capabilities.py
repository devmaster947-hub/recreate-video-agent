#!/usr/bin/env python3
"""Canonical video-model limits and deterministic minimum-Segment planning."""

from __future__ import annotations

from typing import Any


MODEL_CAPABILITIES: dict[str, dict[str, Any]] = {
    "seedance-2-mini": {
        "officialId": "seedance2.0mini_vip", "xiaoyunqueId": "seedance2.0mini_vip",
        "minDuration": 4, "maxDuration": 15,
        "resolutions": ["720p"], "maxImages": 9,
    },
    "seedance-2-fast": {
        "officialId": "seedance2.0fast_vip", "xiaoyunqueId": "seedance2.0fast_vip",
        "minDuration": 4, "maxDuration": 15,
        "resolutions": ["720p"], "maxImages": 9,
    },
    "seedance-2-fast-vip": {
        "officialId": "seedance2.0fast_vip", "minDuration": 4, "maxDuration": 15,
        "resolutions": ["720p"], "maxImages": 9,
    },
    "seedance-2": {
        "officialId": "seedance2.0_vip", "xiaoyunqueId": "seedance2.0_vip",
        "minDuration": 4, "maxDuration": 15,
        "resolutions": ["720p"], "maxImages": 9,
    },
    "seedance-2-vip": {
        "officialId": "seedance2.0_vip", "minDuration": 4, "maxDuration": 15,
        "resolutions": ["720p", "1080p", "4k"], "maxImages": 9,
    },
    "seedance-2-5": {
        "officialId": "seedance2.5", "xiaoyunqueId": "seedance2.5",
        "minDuration": 4, "maxDuration": 30,
        "resolutions": ["720p"], "maxImages": 9, "provisionalOfficial": True,
    },
    "minimax-h3": {
        "minDuration": 4, "maxDuration": 15,
        "resolutions": ["720p"], "maxImages": 9,
    },
    "grok-imagine-1-5-preview": {
        "minDuration": 1, "maxDuration": 15,
        "resolutions": ["720p"], "maxImages": 9,
    },
}

ALIASES = {
    "seedance 2 mini": "seedance-2-mini",
    "seedance 2 fast": "seedance-2-fast",
    "seedance 2 fast vip": "seedance-2-fast-vip",
    "seedance2fast vip": "seedance-2-fast-vip",
    "seedance2 fast vip": "seedance-2-fast-vip",
    "seedance 2": "seedance-2",
    "seedance 2 vip": "seedance-2-vip",
    "seedance 2.5": "seedance-2-5",
    "seedance 2 5": "seedance-2-5",
    "minimax h3": "minimax-h3",
    "grok imagine 1.5 preview": "grok-imagine-1-5-preview",
    "grok imagine 1 5 preview": "grok-imagine-1-5-preview",
}


def normalize_model(value: str) -> str:
    raw = str(value).strip()
    normalized = " ".join(raw.lower().replace("_", " ").replace("-", " ").split())
    return ALIASES.get(normalized, raw)


def capability(model: str) -> dict[str, Any] | None:
    return MODEL_CAPABILITIES.get(normalize_model(model))


def validate_generation(model: str, duration: int | float | str, resolution: str = "720p") -> dict[str, Any]:
    model_id = normalize_model(model)
    limits = capability(model_id)
    if limits is None:
        raise ValueError(f"不支持的视频模型：{model_id}。")
    numeric = float(duration)
    if not numeric.is_integer():
        raise ValueError(f"{model_id} Segment 时长必须是整数秒。")
    minimum, maximum = int(limits["minDuration"]), int(limits["maxDuration"])
    if not minimum <= int(numeric) <= maximum:
        raise ValueError(f"{model_id} Segment 时长必须是 {minimum}-{maximum} 秒。")
    if resolution not in limits["resolutions"]:
        allowed = "、".join(limits["resolutions"])
        raise ValueError(f"{model_id} 不支持 {resolution}；允许分辨率：{allowed}。")
    return {"model": model_id, "duration": int(numeric), "resolution": resolution, **limits}


def minimum_segment_count(model: str, total_duration: int | float) -> int:
    limits = capability(model)
    if limits is None:
        raise ValueError(f"不支持的视频模型：{normalize_model(model)}。")
    maximum = int(limits["maxDuration"])
    return max(1, (int(float(total_duration)) + maximum - 1) // maximum)


def plan_segment_windows(
    model: str,
    total_duration: int | float,
    candidate_cuts: list[int | float] | None = None,
    *,
    snap_tolerance: float = 1.5,
) -> list[dict[str, int]]:
    numeric = float(total_duration)
    if not numeric.is_integer():
        raise ValueError("目标总时长必须是整数秒；不得静默取整。")
    total = int(numeric)
    limits = capability(model)
    if limits is None:
        raise ValueError(f"不支持的视频模型：{normalize_model(model)}。")
    minimum, maximum = int(limits["minDuration"]), int(limits["maxDuration"])
    count = minimum_segment_count(model, total)
    if total < minimum * count:
        raise ValueError(f"{normalize_model(model)}无法用{count}个合法Segment覆盖{total}秒。")
    cuts = [float(value) for value in (candidate_cuts or []) if 0 < float(value) < total]
    boundaries = [0]
    previous = 0
    for index in range(1, count):
        remaining = count - index
        low = max(previous + minimum, total - remaining * maximum)
        high = min(previous + maximum, total - remaining * minimum)
        ideal = total * index / count
        nearby = [cut for cut in cuts if low <= round(cut) <= high and abs(cut - ideal) <= snap_tolerance]
        boundary = round(min(nearby, key=lambda cut: (abs(cut - ideal), cut))) if nearby else round(ideal)
        boundary = max(low, min(high, boundary))
        boundaries.append(int(boundary))
        previous = int(boundary)
    boundaries.append(total)
    return [
        {"segmentId": index, "globalStart": start, "globalEnd": end, "duration": end - start}
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1)
    ]
