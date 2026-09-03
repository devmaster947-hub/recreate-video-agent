#!/usr/bin/env python3
"""Validate storyboard-driven video prompts before any paid submission."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

try:
    from scripts.model_capabilities import minimum_segment_count, validate_generation
except ModuleNotFoundError:
    from model_capabilities import minimum_segment_count, validate_generation


INTERVAL = re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*s?\s*[-–—]\s*(\d+(?:\.\d+)?)\s*s\s*\]")
FORBIDDEN_FIELDS = ("renderUnitId", "renderUnitIds")
REQUIRED_PROMPT_PHRASES = (
    "参考素材职责",
    "最终Segment故事板",
    "前0.2秒",
    "视觉和实体不变量",
    "禁止字幕",
    "生成单画面连续视频",
)
PRODUCT_REFERENCE_DUTY = "产品参考图仅负责锁定产品身份与外观"
PRODUCT_REFERENCE_CONSTRAINT = "产品身份与外观严格以产品参考图为准"
LEGACY_GENERIC_PROHIBITIONS = (
    "禁止新增人物、产品、配件或其他实体。",
    "禁止改变人物数量、产品数量和人物与产品的接触关系。",
    "禁止重新设计产品外观、颜色、材质、Logo、结构或配件。",
    "禁止虚构产品功能、功效、认证、参数或使用结果。",
    "新产品模式禁止出现原产品及其残留外观。",
    "禁止改变已经确定的真实切镜、Proof、CTA和关键产品状态。",
    "禁止分屏、画中画、拼贴画面以及不属于原视频的创意转场。",
)


def intervals(prompt: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in INTERVAL.findall(prompt)]


def validate_timeline(prompt: str, duration: int) -> None:
    spans = intervals(prompt)
    if not 3 <= len(spans) <= 5:
        raise ValueError("Prompt必须使用3-5个连续宏观时间阶段。")
    if abs(spans[0][0]) > .001:
        raise ValueError("Prompt时间轴必须从0秒开始。")
    cursor = 0.0
    for start, end in spans:
        if end <= start or abs(start - cursor) > .01:
            raise ValueError("Prompt时间轴必须连续、无重叠且无空档。")
        cursor = end
    if abs(cursor - duration) > .01:
        raise ValueError("Prompt最后一个时间必须等于Segment duration。")


def validate_segments(
    segments: list[dict[str, Any]], model: str, resolution: str = "720p",
    *, target_duration: int | None = None, available_storyboards: set[int] | None = None,
    available_creators: set[str] | None = None,
    storyboard_windows: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not segments:
        raise ValueError("videoPrompts.segments不能为空。")
    if storyboard_windows is None:
        raise ValueError("必须提供storyboards.generation时间窗。")
    seen: set[int] = set()
    global_cursor = 0.0
    total = 0
    for expected_id, segment in enumerate(segments, 1):
        required = ("segmentId", "title", "duration", "globalStart", "globalEnd", "storyboardIds", "creatorIds", "prompt")
        if any(key not in segment for key in required):
            raise ValueError(f"Segment字段不完整：{segment}")
        if any(field in segment for field in FORBIDDEN_FIELDS):
            raise ValueError("新流程禁止renderUnitId/renderUnitIds字段。")
        identifier = int(segment["segmentId"])
        if identifier != expected_id or identifier in seen:
            raise ValueError("Segment ID必须从1开始连续递增且不得重复。")
        seen.add(identifier)
        raw_duration = float(segment["duration"])
        if not raw_duration.is_integer():
            raise ValueError(f"Segment {identifier} duration必须是整数秒。")
        duration = int(raw_duration)
        validate_generation(model, duration, resolution)
        global_start, global_end = float(segment["globalStart"]), float(segment["globalEnd"])
        if abs(global_start - global_cursor) > .01:
            raise ValueError("Segment全局时间轴必须从0开始并连续、无重叠、无空档。")
        if abs(global_end - global_start - duration) > .01:
            raise ValueError(f"Segment {identifier}全局时间范围与duration不一致。")
        global_cursor = global_end
        total += duration

        boards_raw = segment["storyboardIds"]
        if not isinstance(boards_raw, list) or len(boards_raw) != 1:
            raise ValueError(f"Segment {identifier}必须且只能引用一张Storyboard。")
        board_id = int(boards_raw[0])
        if available_storyboards is not None and board_id not in available_storyboards:
            raise ValueError(f"Segment {identifier}引用了不存在的Storyboard。")
        window = storyboard_windows.get(board_id)
        if window is None:
            raise ValueError(f"Segment {identifier}缺少生成用Storyboard元数据。")
        if int(window.get("segmentId", -1)) != identifier:
            raise ValueError(f"Storyboard {board_id}不属于Segment {identifier}。")
        if abs(float(window.get("globalStart", -1)) - global_start) > .01 or abs(float(window.get("globalEnd", -1)) - global_end) > .01:
            raise ValueError(f"Storyboard {board_id}时间范围与Segment {identifier}不一致。")
        if abs(float(window.get("localStart", -1))) > .01 or abs(float(window.get("localEnd", -1)) - duration) > .01:
            raise ValueError(f"Storyboard {board_id}未使用0-duration段内时间。")

        creators_raw = segment["creatorIds"]
        if not isinstance(creators_raw, list):
            raise ValueError(f"Segment {identifier}.creatorIds必须是数组。")
        creators = {str(value) for value in creators_raw}
        if available_creators is not None and not creators <= available_creators:
            raise ValueError(f"Segment {identifier}引用了不存在的Creator。")

        prompt = str(segment["prompt"])
        if any(field in prompt for field in FORBIDDEN_FIELDS):
            raise ValueError(f"Segment {identifier} Prompt不得引用renderUnit。")
        if not prompt.lstrip().startswith("参考素材职责"):
            raise ValueError(f"Segment {identifier}必须以参考素材职责开始。")
        for phrase in REQUIRED_PROMPT_PHRASES:
            if phrase not in prompt:
                raise ValueError(f"Segment {identifier}缺少“{phrase}”约束。")
        legacy = next((phrase for phrase in LEGACY_GENERIC_PROHIBITIONS if phrase in prompt), None)
        if legacy is not None:
            raise ValueError(f"Segment {identifier}包含已废弃的通用禁止项：{legacy}")
        if PRODUCT_REFERENCE_DUTY in prompt and PRODUCT_REFERENCE_CONSTRAINT not in prompt:
            raise ValueError(f"Segment {identifier}已引用产品参考图，但缺少产品外观服从参考图的最小约束。")
        if creators and "达人参考图" not in prompt:
            raise ValueError(f"Segment {identifier}引用Creator时必须声明达人参考图职责。")
        validate_timeline(prompt, duration)

    expected = int(target_duration if target_duration is not None else total)
    if total != expected or abs(global_cursor - expected) > .01:
        raise ValueError(f"所有Segment总时长{total}s与目标时长{expected}s不一致。")
    minimum = minimum_segment_count(model, expected)
    if len(segments) != minimum:
        raise ValueError(f"当前模型和目标时长应使用最少{minimum}个Segment，实际为{len(segments)}。")
    return {"ok": True, "totalDuration": total, "segmentCount": len(segments)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompts", required=True)
    parser.add_argument("--storyboard-metadata", required=True)
    parser.add_argument("--model", default="seedance-2-fast")
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--target-duration", type=int)
    args = parser.parse_args()
    try:
        value = json.loads(Path(args.prompts).read_text(encoding="utf-8"))
        metadata = json.loads(Path(args.storyboard_metadata).read_text(encoding="utf-8"))
        boards = metadata.get("boards", [])
        windows = {int(item["storyboardId"]): item for item in boards}
        result = validate_segments(
            value["videoPrompts"]["segments"], args.model, args.resolution,
            target_duration=args.target_duration,
            available_storyboards=set(windows), storyboard_windows=windows,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
