#!/usr/bin/env python3
"""Canonical video-model limits and deterministic minimum-Segment planning."""

from __future__ import annotations

from typing import Any


LOCAL_CLI_REQUIRED_MODEL_IDS = {
    "seedance-2-fast": "seedance2.0fast_vip",
    "seedance-2-mini": "seedance2.0mini_vip",
}


MODEL_CAPABILITIES: dict[str, dict[str, Any]] = {
    "seedance-2-mini": {
        "dreaminaId": LOCAL_CLI_REQUIRED_MODEL_IDS["seedance-2-mini"],
        "xiaoyunqueId": LOCAL_CLI_REQUIRED_MODEL_IDS["seedance-2-mini"],
        "minDuration": 4, "maxDuration": 15,
        "resolutions": ["720p"], "maxImages": 9,
    },
    "seedance-2-fast": {
        "dreaminaId": LOCAL_CLI_REQUIRED_MODEL_IDS["seedance-2-fast"],
        "xiaoyunqueId": LOCAL_CLI_REQUIRED_MODEL_IDS["seedance-2-fast"],
        "minDuration": 4, "maxDuration": 15,
        "resolutions": ["720p"], "maxImages": 9,
    },
    "seedance-2-fast-vip": {
        "dreaminaId": "seedance2.0fast_vip", "minDuration": 4, "maxDuration": 15,
        "resolutions": ["720p"], "maxImages": 9,
    },
    "seedance-2": {
        "dreaminaId": "seedance2.0_vip", "xiaoyunqueId": "seedance2.0_vip",
        "minDuration": 4, "maxDuration": 15,
        "resolutions": ["720p"], "maxImages": 9,
    },
    "seedance-2-vip": {
        "dreaminaId": "seedance2.0_vip", "minDuration": 4, "maxDuration": 15,
        "resolutions": ["720p", "1080p", "4k"], "maxImages": 9,
    },
    "seedance-2-5": {
        "dreaminaId": "seedance2.5", "xiaoyunqueId": "seedance2.5",
        "minDuration": 4, "maxDuration": 30,
        "resolutions": ["720p"], "maxImages": 9, "provisionalDreamina": True,
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
