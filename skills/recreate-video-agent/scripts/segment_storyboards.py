#!/usr/bin/env python3
"""Compose generation-only 3x3 storyboards with segment-local timestamps."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


PANELS = 9
ANCHOR_METADATA_FIELDS = (
    "productPresent", "productVisibility", "productCount",
    "personPresent", "personExtent", "personCount",
    "creatorIds", "interactionState",
)


def executable(name: str, value: str | None = None, *, required: bool = True) -> str | None:
    found = value or shutil.which(name)
    local = Path.home() / ".local" / "bin" / name
    result = found or (str(local) if local.is_file() else "")
    if not result and required:
        raise ValueError(f"当前环境缺少 {name}。")
    return result or None


def run(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:] or "段内 Storyboard 处理失败。")
    return result.stdout


def dimensions(image: Path, ffmpeg: str, ffprobe: str | None) -> tuple[int, int]:
    if ffprobe:
        raw = run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(image)]).strip()
        width, height = raw.split("x", 1)
        return int(width), int(height)
    result = subprocess.run([ffmpeg, "-hide_banner", "-i", str(image)], text=True, capture_output=True, check=False)
    match = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", result.stderr)
    if not match:
        raise RuntimeError(f"无法读取画格尺寸：{image}")
    return int(match.group(1)), int(match.group(2))


def normalize_segment(raw: dict[str, Any]) -> dict[str, Any]:
    identifier = int(raw["segmentId"])
    start = float(raw["globalStart"])
    end = float(raw["globalEnd"])
    cells = raw.get("cells")
    if end <= start:
        raise ValueError(f"Segment {identifier} globalEnd 必须大于 globalStart。")
    if not isinstance(cells, list) or len(cells) != PANELS:
        raise ValueError(f"Segment {identifier} 必须且只能提供9个编辑后画格。")
    normalized: list[dict[str, Any]] = []
    previous = -1.0
    for index, item in enumerate(cells, 1):
        path = Path(str(item["file"])).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"Segment {identifier} 画格不存在或为空：{path}")
        timestamp = float(item["globalTimestamp"])
        if timestamp < start - .01 or timestamp > end + .01:
            raise ValueError(f"Segment {identifier} 画格 {index} 超出 {start:.2f}-{end:.2f}s。")
        if timestamp < previous:
            raise ValueError(f"Segment {identifier} 画格必须按全局时间升序排列。")
        previous = timestamp
        anchor = {
            "index": index,
            "file": str(path),
            "globalTimestamp": timestamp,
            "localTimestamp": round(timestamp - start, 3),
            "anchorRole": str(item.get("anchorRole", "context")),
            "eventType": str(item.get("eventType", "context")),
        }
        for field in ANCHOR_METADATA_FIELDS:
            if field == "creatorIds":
                anchor[field] = [str(value) for value in item.get(field, [])]
            elif field in item:
                anchor[field] = item[field]
        normalized.append(anchor)
    if abs(normalized[0]["globalTimestamp"] - start) > .05:
        raise ValueError(f"Segment {identifier} 第一格必须是段起点，误差不得超过0.05秒。")
    result = {
        "segmentId": identifier,
        "globalStart": start,
        "globalEnd": end,
        "duration": end - start,
        "replacementVerified": bool(raw.get("replacementVerified", False)),
        "replacement": raw.get("replacement") if isinstance(raw.get("replacement"), dict) else None,
        "labelHeight": int(raw.get("labelHeight", 38)),
        "cells": normalized,
    }
    return result


def render(segment: dict[str, Any], output: Path, ffmpeg: str, ffprobe: str | None) -> dict[str, Any]:
    cells = segment["cells"]
    width, height = dimensions(Path(cells[0]["file"]), ffmpeg, ffprobe)
    label_height = int(segment.get("labelHeight", 38))
    if not 1 <= label_height < height:
        raise ValueError("labelHeight必须小于画格高度。")
    frame_height = height - label_height
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ModuleNotFoundError:
        Image = ImageDraw = ImageFont = None
    if Image is not None:
        font_path = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
        font = ImageFont.truetype(str(font_path), 20) if font_path.is_file() else ImageFont.load_default()
        board = Image.new("RGB", (width * 3, height * 3), "black")
        for offset, cell in enumerate(cells):
            with Image.open(cell["file"]) as source:
                visual = source.convert("RGB").crop((0, 0, width, frame_height))
            if visual.size != (width, frame_height):
                visual = visual.resize((width, frame_height), Image.Resampling.LANCZOS)
            panel = Image.new("RGB", (width, height), "black")
            panel.paste(visual, (0, 0))
            draw = ImageDraw.Draw(panel)
            draw.text((8, frame_height + 7), f"{offset + 1:02d} · {float(cell['localTimestamp']):.2f}s", font=font, fill="white")
            draw.rectangle((0, 0, width - 1, height - 1), outline=(255, 255, 255), width=1)
            row, column = divmod(offset, 3)
            board.paste(panel, (column * width, row * height))
        output.parent.mkdir(parents=True, exist_ok=True)
        board.save(output)
    else:
        _render_with_ffmpeg(cells, output, ffmpeg, width, height, frame_height)
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError(f"未生成段内 Storyboard：{output}")
    result = {
        "storyboardId": int(segment["segmentId"]),
        "segmentId": int(segment["segmentId"]),
        "file": str(output.resolve()),
        "layout": "3x3",
        "globalStart": float(segment["globalStart"]),
        "globalEnd": float(segment["globalEnd"]),
        "localStart": 0.0,
        "localEnd": float(segment["duration"]),
        "replacementVerified": bool(segment.get("replacementVerified", False)),
        "labelHeight": label_height,
        "anchors": [
            {
                key: cell[key]
                for key in ("index", "globalTimestamp", "localTimestamp", "anchorRole", "eventType", *ANCHOR_METADATA_FIELDS)
                if key in cell
            }
            for cell in segment["cells"]
        ],
    }
    if isinstance(segment.get("replacement"), dict):
        result["replacement"] = segment["replacement"]
    return result


def _render_with_ffmpeg(cells: list[dict[str, Any]], output: Path, ffmpeg: str, width: int, height: int, frame_height: int) -> None:
    command = [ffmpeg, "-y"]
    for cell in cells:
        command.extend(["-i", cell["file"]])
    filters: list[str] = []
    labels: list[str] = []
    for offset, cell in enumerate(cells):
        label = f"c{offset}"
        local = float(cell["localTimestamp"])
        text = f"{offset + 1:02d} · {local:.2f}s"
        filters.append(
            f"[{offset}:v]crop={width}:{frame_height}:0:0,"
            f"scale={width}:{frame_height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{frame_height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"pad={width}:{height}:0:0:black,"
            f"drawtext=text='{text}':x=8:y={frame_height + 7}:fontsize=20:fontcolor=white,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.55:t=1[{label}]"
        )
        labels.append(f"[{label}]")
    layout = "|".join(f"{column * width}_{row * height}" for row in range(3) for column in range(3))
    filters.append("".join(labels) + f"xstack=inputs=9:layout={layout}[out]")
    output.parent.mkdir(parents=True, exist_ok=True)
    command.extend(["-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", "1", str(output)])
    run(command)


def build(plan: dict[str, Any], output_dir: Path, ffmpeg: str, ffprobe: str | None) -> dict[str, Any]:
    raw_segments = plan.get("segments") if isinstance(plan, dict) else None
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError("plan 缺少非空 segments。")
    segments = [normalize_segment(item) for item in raw_segments]
    segments.sort(key=lambda item: int(item["segmentId"]))
    cursor = 0.0
    boards: list[dict[str, Any]] = []
    for segment in segments:
        if abs(float(segment["globalStart"]) - cursor) > .01:
            raise ValueError("Segment 全局时间轴必须从0开始并连续、无重叠、无空档。")
        output = output_dir / f"segment-{int(segment['segmentId']):02d}-storyboard-3x3.png"
        boards.append(render(segment, output, ffmpeg, ffprobe))
        cursor = float(segment["globalEnd"])
    return {"schemaRevision": "5.1", "layout": "3x3", "boards": boards, "totalDuration": cursor}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    args = parser.parse_args()
    try:
        output_dir = Path(args.output_dir).expanduser().resolve()
        result = build(
            json.loads(Path(args.plan).expanduser().resolve().read_text(encoding="utf-8")),
            output_dir,
            str(executable("ffmpeg", args.ffmpeg, required_filter="drawtext")),
            executable("ffprobe", args.ffprobe, required=False),
        )
        metadata = output_dir / "segment-storyboard-metadata.json"
        metadata.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "metadata": str(metadata), **result}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
