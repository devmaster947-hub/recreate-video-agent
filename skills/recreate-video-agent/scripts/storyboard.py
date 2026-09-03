#!/usr/bin/env python3
"""Extract real video frames and compose one fixed 4x4 board per Segment."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from scripts.model_capabilities import minimum_segment_count, validate_generation
except ModuleNotFoundError:
    from model_capabilities import minimum_segment_count, validate_generation


PANELS_PER_BOARD = 16
ALLOWED_ROLES = {"hard", "soft", "context"}
ALLOWED_EVENTS = {
    "first_frame", "continuity_start", "cut", "hook", "product_first",
    "action_start", "action_process", "action_end", "product_state", "before",
    "after", "proof", "cta", "context",
}
PRODUCT_VISIBILITY = {"none", "partial", "full"}
PERSON_EXTENT = {"none", "partial", "full"}


def normalize_presence(
    item: dict[str, Any],
    *,
    present_key: str,
    state_key: str,
    count_key: str,
    allowed_states: set[str],
    inferred_present: bool = False,
) -> tuple[bool, str, int]:
    """Normalize v5.11 presence metadata while accepting older anchor plans."""
    raw_present = item.get(present_key)
    present = inferred_present if raw_present is None else raw_present
    if not isinstance(present, bool):
        raise ValueError(f"anchor.{present_key} 必须是布尔值。")
    default_state = "full" if present else "none"
    state = str(item.get(state_key, default_state))
    if state not in allowed_states:
        raise ValueError(f"anchor.{state_key} 必须是 none、partial 或 full。")
    raw_count = item.get(count_key, 1 if present else 0)
    if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
        raise ValueError(f"anchor.{count_key} 必须是非负整数。")
    if not present:
        if state != "none" or raw_count != 0:
            raise ValueError(f"anchor.{present_key}=false 时 {state_key}=none 且 {count_key}=0。")
    elif state == "none" or raw_count < 1:
        raise ValueError(f"anchor.{present_key}=true 时必须有可见状态且 {count_key}≥1。")
    return present, state, raw_count


def normalize_anchor(value: Any, index: int) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {"timestamp": value}
    timestamp = item.get("timestamp", item.get("time"))
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or timestamp < 0:
        raise ValueError(f"无效时间戳：{timestamp!r}")
    role = str(item.get("anchorRole", "hard" if index == 0 else "soft"))
    event = str(item.get("eventType", "first_frame" if index == 0 else "context"))
    if role not in ALLOWED_ROLES:
        raise ValueError(f"无效 anchorRole：{role}")
    if event not in ALLOWED_EVENTS:
        raise ValueError(f"无效 eventType：{event}")
    creators = item.get("creatorIds", [])
    source_creators = item.get("sourceCreatorIds", [])
    if not isinstance(creators, list):
        raise ValueError("anchor.creatorIds 必须是数组。")
    if not isinstance(source_creators, list):
        raise ValueError("anchor.sourceCreatorIds 必须是数组。")
    product_present, product_visibility, product_count = normalize_presence(
        item,
        present_key="productPresent",
        state_key="productVisibility",
        count_key="productCount",
        allowed_states=PRODUCT_VISIBILITY,
    )
    person_present, person_extent, person_count = normalize_presence(
        item,
        present_key="personPresent",
        state_key="personExtent",
        count_key="personCount",
        allowed_states=PERSON_EXTENT,
        inferred_present=bool(creators or source_creators),
    )
    return {
        "timestamp": float(timestamp),
        "shotId": str(item.get("shotId", "")),
        "anchorRole": role,
        "eventType": event,
        "importance": int(item.get("importance", 5 if role == "hard" else 3)),
        "productPresent": product_present,
        "productVisibility": product_visibility,
        "productCount": product_count,
        "personPresent": person_present,
        "personExtent": person_extent,
        "personCount": person_count,
        "creatorIds": [str(identifier) for identifier in creators],
        "sourceCreatorIds": [str(identifier) for identifier in source_creators],
        "keptSourceCreatorIds": [str(identifier) for identifier in item.get("keptSourceCreatorIds", [])],
        "interactionState": str(item.get("interactionState", "")),
    }


def normalize_segment(raw: dict[str, Any], expected_id: int) -> dict[str, Any]:
    identifier = int(raw.get("segmentId", expected_id))
    if identifier != expected_id:
        raise ValueError("Segment ID 必须从1开始连续递增。")
    start, end = float(raw["globalStart"]), float(raw["globalEnd"])
    if end <= start:
        raise ValueError(f"Segment {identifier} globalEnd 必须大于 globalStart。")
    values = raw.get("anchors")
    if not isinstance(values, list) or len(values) != PANELS_PER_BOARD:
        raise ValueError(f"Segment {identifier} 必须且只能包含16个anchors。")
    anchors = [normalize_anchor(value, index) for index, value in enumerate(values)]
    timestamps = [item["timestamp"] for item in anchors]
    if timestamps != sorted(timestamps):
        raise ValueError(f"Segment {identifier} anchors必须按时间升序排列。")
    if any(value < start - .01 or value > end + .01 for value in timestamps):
        raise ValueError(f"Segment {identifier} anchor超出自身时间窗。")
    if abs(timestamps[0] - start) > .05:
        raise ValueError(f"Segment {identifier} 第一格必须是分段起点。")
    if anchors[0]["anchorRole"] != "hard":
        raise ValueError(f"Segment {identifier} 第一格必须标记为hard。")
    allowed_first = {"first_frame"} if identifier == 1 else {"cut", "continuity_start"}
    if anchors[0]["eventType"] not in allowed_first:
        raise ValueError(f"Segment {identifier} 第一格事件类型无效。")
    return {
        "segmentId": identifier,
        "globalStart": start,
        "globalEnd": end,
        "duration": end - start,
        "anchors": anchors,
    }


def parse_segment_plan(path: Path, *, media_duration: float | None = None) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("segments"), list):
        segments = [normalize_segment(item, index) for index, item in enumerate(raw["segments"], 1)]
    else:
        values = raw.get("anchors", raw.get("timestamps")) if isinstance(raw, dict) else raw
        if not isinstance(values, list) or not values or len(values) % PANELS_PER_BOARD:
            raise ValueError("输入必须含segments，或提供16的整数倍数量的anchors/timestamps。")
        normalized = [normalize_anchor(value, index) for index, value in enumerate(values)]
        segments = []
        for offset in range(0, len(normalized), PANELS_PER_BOARD):
            anchors = normalized[offset:offset + PANELS_PER_BOARD]
            identifier = offset // PANELS_PER_BOARD + 1
            start = anchors[0]["timestamp"]
            next_offset = offset + PANELS_PER_BOARD
            end = normalized[next_offset]["timestamp"] if next_offset < len(normalized) else float(media_duration or anchors[-1]["timestamp"])
            if end <= start:
                raise ValueError("旧版anchors无法推导有效Segment时间窗；请改用segments格式。")
            if identifier > 1 and anchors[0]["eventType"] == "first_frame":
                anchors[0]["eventType"] = "continuity_start"
            segments.append(normalize_segment({"segmentId": identifier, "globalStart": start, "globalEnd": end, "anchors": anchors}, identifier))
    if not segments:
        raise ValueError("segments不能为空。")
    cursor = 0.0
    for segment in segments:
        if abs(segment["globalStart"] - cursor) > .01:
            raise ValueError("Segment时间窗必须从0开始并连续、无重叠、无空档。")
        cursor = segment["globalEnd"]
    return segments


def parse_anchor_plan(path: Path) -> list[dict[str, Any]]:
    """Backward-compatible flattened view used by callers and tests."""
    return [anchor for segment in parse_segment_plan(path) for anchor in segment["anchors"]]


def parse_timestamps(path: Path) -> list[float]:
    return [item["timestamp"] for item in parse_anchor_plan(path)]


def validate_against_analysis(segments_or_anchors: list[dict[str, Any]], analysis: dict[str, Any]) -> None:
    if segments_or_anchors and "anchors" in segments_or_anchors[0]:
        anchors = [anchor for segment in segments_or_anchors for anchor in segment["anchors"]]
    else:
        anchors = segments_or_anchors
    cuts = [float(value) for value in analysis.get("candidateCuts", [])]
    for anchor in anchors:
        if anchor.get("eventType") == "cut" and cuts and min(abs(float(anchor["timestamp"]) - cut) for cut in cuts) > .25:
            raise ValueError(f"切镜锚点 {anchor['timestamp']:.2f}s 距候选切镜超过0.25秒。")
    duration = float(analysis.get("targetDuration") or analysis.get("media", {}).get("duration") or 0)
    if duration and float(anchors[-1]["timestamp"]) < duration * .9:
        raise ValueError("最后一个锚点早于复刻区间90%位置，可能遗漏末段Proof/CTA。")


def validate_model_windows(segments: list[dict[str, Any]], model: str) -> None:
    total = segments[-1]["globalEnd"]
    if not float(total).is_integer():
        raise ValueError("目标总时长必须是模型支持的整数秒；不得静默取整。")
    for segment in segments:
        validate_generation(model, segment["duration"])
    expected = minimum_segment_count(model, total)
    if len(segments) != expected:
        raise ValueError(f"当前模型与目标时长必须使用最少{expected}个Segment，实际为{len(segments)}。")


def run(command: list[str]) -> None:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError(completed.stderr[-2000:] or "视频帧处理失败。")


def render_board(ffmpeg: str, video: Path, anchors: list[dict[str, Any]], output: Path, cell_width: int, cell_height: int) -> None:
    command = [ffmpeg, "-y"]
    for anchor in anchors:
        command.extend(["-ss", f"{anchor['timestamp']:.3f}", "-i", str(video)])
    frame_height = max(1, cell_height - 38)
    filters: list[str] = []
    labels: list[str] = []
    for index, anchor in enumerate(anchors):
        label = f"v{index}"
        labels.append(f"[{label}]")
        text = f"{index + 1:02d} · {anchor['timestamp']:.2f}s".replace(":", r"\:").replace("'", r"\'")
        filters.append(
            f"[{index}:v]scale={cell_width}:{frame_height}:force_original_aspect_ratio=decrease,"
            f"pad={cell_width}:{frame_height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"pad={cell_width}:{cell_height}:0:0:black,"
            f"drawtext=text='{text}':x=8:y={frame_height + 7}:fontsize=20:fontcolor=white,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.55:t=1[{label}]"
        )
    layout = "|".join(f"{column * cell_width}_{row * cell_height}" for row in range(4) for column in range(4))
    filters.append("".join(labels) + f"xstack=inputs=16:layout={layout}[board]")
    output.parent.mkdir(parents=True, exist_ok=True)
    command.extend(["-filter_complex", ";".join(filters), "-map", "[board]", "-frames:v", "1", "-compression_level", "3", str(output)])
    run(command)
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError(f"未生成Storyboard：{output}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True)
    parser.add_argument("--timestamps-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cell-width", type=int, default=240)
    parser.add_argument("--cell-height", type=int, default=464)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--analysis-file")
    parser.add_argument("--model", default="seedance-2-fast")
    args = parser.parse_args()

    video = Path(args.video).expanduser().resolve()
    timestamp_file = Path(args.timestamps_file).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not video.is_file() or video.stat().st_size <= 0:
        raise SystemExit(f"对标视频不存在或为空：{video}")
    ffmpeg = args.ffmpeg if "/" in args.ffmpeg or "\\" in args.ffmpeg else shutil.which(args.ffmpeg)
    if not ffmpeg:
        local = Path.home() / ".local" / "bin" / "ffmpeg"
        ffmpeg = str(local) if local.is_file() else None
    if not ffmpeg:
        raise SystemExit("当前运行环境没有可用的视频帧处理能力。")
    try:
        analysis = json.loads(Path(args.analysis_file).read_text(encoding="utf-8")) if args.analysis_file else {}
        media_duration = float(analysis.get("targetDuration") or analysis.get("media", {}).get("duration") or 0) or None
        segments = parse_segment_plan(timestamp_file, media_duration=media_duration)
        validate_model_windows(segments, args.model)
        if analysis:
            validate_against_analysis(segments, analysis)
        boards: list[dict[str, Any]] = []
        for segment in segments:
            identifier = int(segment["segmentId"])
            output = output_dir / f"segment-{identifier:02d}-storyboard-4x4.png"
            render_board(ffmpeg, video, segment["anchors"], output, args.cell_width, args.cell_height)
            boards.append({
                "storyboardId": identifier,
                "segmentId": identifier,
                "file": str(output.resolve()),
                "layout": "4x4",
                "globalStart": segment["globalStart"],
                "globalEnd": segment["globalEnd"],
                "duration": segment["duration"],
                "anchors": segment["anchors"],
            })
        metadata = output_dir / "storyboard-metadata.json"
        metadata.write_text(json.dumps({"schemaRevision": "5.1", "layout": "4x4", "boards": boards, "totalDuration": segments[-1]["globalEnd"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "layout": "4x4", "outputs": [item["file"] for item in boards], "metadata": str(metadata), "boards": boards}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
