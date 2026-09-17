#!/usr/bin/env python3
"""Split 3x3 storyboards and deterministically restore locked layouts."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import re
import tempfile
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ImportError:  # pragma: no cover - ffmpeg fallback keeps the skill portable.
    Image = None


def executable(name: str, value: str | None, *, required: bool = True, required_filter: str | None = None) -> str | None:
    local = Path.home() / ".local" / "bin" / name
    found = shutil.which(name)
    fallbacks = [str(local) if local.is_file() else None, found] if required_filter else [found, str(local) if local.is_file() else None]
    candidates = [value, *fallbacks]
    checked: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in checked:
            continue
        checked.add(candidate)
        if required_filter:
            result = subprocess.run([candidate, "-hide_banner", "-filters"], text=True, capture_output=True, check=False)
            if result.returncode or not any(line.split()[1:2] == [required_filter] for line in result.stdout.splitlines()):
                continue
        return candidate
    if required:
        suffix = f"（需要 {required_filter} 滤镜）" if required_filter else ""
        raise ValueError(f"当前环境缺少 {name}{suffix}。")
    return None


def run(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:] or "画格处理失败。")
    return result.stdout


def run_bytes(command: list[str]) -> bytes:
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(message[-2000:] or "画格像素分析失败。")
    return result.stdout


def dimensions(ffprobe: str | None, image: Path, ffmpeg: str) -> tuple[int, int]:
    if ffprobe:
        raw = run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(image)]).strip()
        width, height = raw.split("x", 1)
        return int(width), int(height)
    result = subprocess.run([ffmpeg, "-hide_banner", "-i", str(image)], text=True, capture_output=True, check=False)
    match = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", result.stderr)
    if not match:
        raise RuntimeError("无法读取Storyboard尺寸。")
    return int(match.group(1)), int(match.group(2))


def grayscale_pixels(ffmpeg: str, image: Path, width: int, height: int) -> bytes:
    pixels = run_bytes([
        ffmpeg, "-v", "error", "-i", str(image), "-frames:v", "1",
        "-vf", "format=gray", "-pix_fmt", "gray", "-f", "rawvideo", "-",
    ])
    expected = width * height
    if len(pixels) != expected:
        raise RuntimeError(f"画格像素数量异常：期望{expected}，实际{len(pixels)}。")
    return pixels


def _axis_edge_score(pixels: bytes, width: int, height: int, position: int, axis: str) -> float:
    if axis == "x":
        step = max(1, height // 512)
        values = (
            abs(pixels[row * width + position] - pixels[row * width + position - 1])
            for row in range(0, height, step)
        )
        count = (height + step - 1) // step
    else:
        step = max(1, width // 512)
        current, previous = position * width, (position - 1) * width
        values = (
            abs(pixels[current + column] - pixels[previous + column])
            for column in range(0, width, step)
        )
        count = (width + step - 1) // step
    return sum(values) / max(1, count)


def _detect_axis_boundaries(
    pixels: bytes,
    width: int,
    height: int,
    axis: str,
) -> dict[str, Any]:
    length = width if axis == "x" else height
    step = length / 3
    radius = max(4, round(step * .12))
    boundaries = [0]
    scores: list[float] = []
    expected_values: list[int] = []
    for index in range(1, 3):
        expected = round(step * index)
        expected_values.append(expected)
        start, end = max(1, expected - radius), min(length - 1, expected + radius)
        candidates = [
            (_axis_edge_score(pixels, width, height, position, axis), position)
            for position in range(start, end + 1)
        ]
        score, position = max(candidates, key=lambda item: (item[0], -abs(item[1] - expected)))
        boundaries.append(position)
        scores.append(round(score, 3))
    boundaries.append(length)
    sizes = [boundaries[index + 1] - boundaries[index] for index in range(3)]
    minimum, maximum = step * .62, step * 1.38
    valid = (
        min(scores, default=0) >= 6
        and all(minimum <= size <= maximum for size in sizes)
        and boundaries == sorted(set(boundaries))
    )
    return {
        "boundaries": boundaries,
        "expected": [0, *expected_values, length],
        "scores": scores,
        "sizes": sizes,
        "valid": valid,
        "maxDriftPx": max(
            (abs(actual - expected) for actual, expected in zip(boundaries[1:-1], expected_values)),
            default=0,
        ),
    }


def detect_grid_boundaries(ffmpeg: str, ffprobe: str | None, image: Path) -> dict[str, Any]:
    width, height = dimensions(ffprobe, image, ffmpeg)
    pixels = grayscale_pixels(ffmpeg, image, width, height)
    columns = _detect_axis_boundaries(pixels, width, height, "x")
    rows = _detect_axis_boundaries(pixels, width, height, "y")
    return {
        "sourceWidth": width,
        "sourceHeight": height,
        "columns": columns,
        "rows": rows,
        "valid": bool(columns["valid"] and rows["valid"]),
        "pixels": pixels,
    }


def detect_label_start(
    pixels: bytes,
    width: int,
    left: int,
    right: int,
    top: int,
    bottom: int,
) -> int:
    """Find the AI-rendered dark label band near the bottom of one detected cell."""
    cell_height = bottom - top
    search_start = top + round(cell_height * .68)
    sample_step = max(1, (right - left) // 256)
    dark_rows: list[bool] = []
    for row in range(search_start, bottom):
        values = pixels[row * width + left:row * width + right:sample_step]
        if not values:
            dark_rows.append(False)
            continue
        dark_fraction = sum(1 for value in values if value < 58) / len(values)
        mean = sum(values) / len(values)
        dark_rows.append(dark_fraction >= .72 and mean < 72)
    # White timecode glyphs can split one black label band into several short
    # dark runs. Close those small interior gaps before locating the band.
    gap_limit = max(3, round(cell_height * .025))
    cursor = 0
    while cursor < len(dark_rows):
        if dark_rows[cursor]:
            cursor += 1
            continue
        gap_start = cursor
        while cursor < len(dark_rows) and not dark_rows[cursor]:
            cursor += 1
        if gap_start > 0 and cursor < len(dark_rows) and cursor - gap_start <= gap_limit:
            dark_rows[gap_start:cursor] = [True] * (cursor - gap_start)
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for offset, is_dark in enumerate(dark_rows):
        if is_dark and start is None:
            start = offset
        elif not is_dark and start is not None:
            runs.append((start, offset))
            start = None
    if start is not None:
        runs.append((start, len(dark_rows)))
    minimum_run = max(4, round(cell_height * .025))
    candidates = [
        (run_start, run_end)
        for run_start, run_end in runs
        # Generated boards often retain a gray outer margin below the label.
        # Accept the final broad dark band when it ends within the bottom 18%
        # of the detected cell instead of requiring it to touch the boundary.
        if run_end - run_start >= minimum_run and run_end >= len(dark_rows) - max(3, round(cell_height * .18))
    ]
    if not candidates:
        return bottom
    # Prefer the earliest qualifying band. A dark outer canvas margin can form
    # a second run after the real label; selecting the longest/later run would
    # leave the generated label in the visual area and duplicate it on restore.
    run_start, _ = min(candidates, key=lambda item: item[0])
    return search_start + run_start


def split_board(ffmpeg: str, ffprobe: str | None, board: Path, output_dir: Path) -> dict[str, Any]:
    if Image is not None:
        with Image.open(board) as source:
            width, height = source.size
            if width % 3 or height % 3:
                raise ValueError("Storyboard 宽高必须能被 3 整除。")
            cell_w, cell_h = width // 3, height // 3
            output_dir.mkdir(parents=True, exist_ok=True)
            cells: list[dict[str, Any]] = []
            image = source.convert("RGB")
            for index in range(9):
                row, column = divmod(index, 3)
                output = output_dir / f"cell-{index + 1:02d}.png"
                image.crop((column * cell_w, row * cell_h, (column + 1) * cell_w, (row + 1) * cell_h)).save(output)
                cells.append({"index": index + 1, "file": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()})
        manifest = {"source": str(board), "cellWidth": cell_w, "cellHeight": cell_h, "cells": cells}
        (output_dir / "cells.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest

    width, height = dimensions(ffprobe, board, ffmpeg)
    if width % 3 or height % 3:
        raise ValueError("Storyboard 宽高必须能被 3 整除。")
    cell_w, cell_h = width // 3, height // 3
    output_dir.mkdir(parents=True, exist_ok=True)
    cells: list[dict[str, Any]] = []
    for index in range(9):
        row, column = divmod(index, 3)
        output = output_dir / f"cell-{index + 1:02d}.png"
        run([ffmpeg, "-y", "-i", str(board), "-vf", f"crop={cell_w}:{cell_h}:{column * cell_w}:{row * cell_h}", "-frames:v", "1", str(output)])
        cells.append({"index": index + 1, "file": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()})
    manifest = {"source": str(board), "cellWidth": cell_w, "cellHeight": cell_h, "cells": cells}
    (output_dir / "cells.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def normalize_cell(
    ffmpeg: str,
    ffprobe: str | None,
    source: Path,
    output: Path,
    width: int,
    height: int,
    crop_x: float,
    crop_y: float,
) -> dict[str, Any]:
    if not 0 <= crop_x <= 1 or not 0 <= crop_y <= 1:
        raise ValueError("crop-x 和 crop-y 必须位于 0 到 1 之间。")
    source_width, source_height = dimensions(ffprobe, source, ffmpeg)
    target_ratio = width / height
    source_ratio = source_width / source_height
    if source_ratio > target_ratio:
        crop_width, crop_height = round(source_height * target_ratio), source_height
        offset_x, offset_y = round((source_width - crop_width) * crop_x), 0
    else:
        crop_width, crop_height = source_width, round(source_width / target_ratio)
        offset_x, offset_y = 0, round((source_height - crop_height) * crop_y)
    output.parent.mkdir(parents=True, exist_ok=True)
    run([
        ffmpeg, "-y", "-i", str(source),
        "-vf", f"crop={crop_width}:{crop_height}:{offset_x}:{offset_y},scale={width}:{height}",
        "-frames:v", "1", str(output),
    ])
    return {
        "output": str(output),
        "sourceWidth": source_width,
        "sourceHeight": source_height,
        "crop": {"x": offset_x, "y": offset_y, "width": crop_width, "height": crop_height},
        "targetWidth": width,
        "targetHeight": height,
    }


def restore_board_layout(
    ffmpeg: str,
    ffprobe: str | None,
    source: Path,
    original: Path,
    output: Path,
    label_height: int = 38,
) -> dict[str, Any]:
    """Restore a generated whole board without preserving AI-rendered labels.

    When the generated 3x3 grid can be detected, normalize every generated
    visual cell independently, discard its label strip, and append the exact
    label strip from the original board.  This prevents duplicated time bars
    while retaining the complete generated scene in every cell.
    """
    width, height = dimensions(ffprobe, original, ffmpeg)
    if width % 3 or height % 3:
        raise ValueError("原Storyboard宽高必须能被3整除。")
    source_width, source_height = dimensions(ffprobe, source, ffmpeg)
    target_ratio, source_ratio = width / height, source_width / source_height
    if source_ratio > target_ratio:
        crop_width, crop_height = round(source_height * target_ratio), source_height
        offset_x, offset_y = round((source_width - crop_width) / 2), 0
    else:
        crop_width, crop_height = source_width, round(source_width / target_ratio)
        offset_x, offset_y = 0, round((source_height - crop_height) / 2)
    cell_w, cell_h = width // 3, height // 3
    if not 1 <= label_height < cell_h:
        raise ValueError("label-height必须小于单格高度。")
    detection = detect_grid_boundaries(ffmpeg, ffprobe, source)
    if detection["valid"]:
        with tempfile.TemporaryDirectory(prefix="storyboard-layout-restore-") as temporary:
            root = Path(temporary)
            original_cells = root / "original-cells"
            split_board(ffmpeg, ffprobe, original, original_cells)
            restored_cells, adaptive = adaptive_edited_cells(
                ffmpeg,
                ffprobe,
                source,
                original_cells,
                root / "restored-cells",
                detection,
                list(range(1, 10)),
                cell_w,
                cell_h,
                label_height,
            )
            compose(ffmpeg, original_cells, output, restored_cells)
        return {
            "output": str(output),
            "width": width,
            "height": height,
            "labelHeight": label_height,
            "layoutRestoredFrom": str(original),
            "alignmentMode": "detected-grid-per-cell",
            "detectedGrid": {
                "valid": True,
                "columns": detection["columns"],
                "rows": detection["rows"],
            },
            **adaptive,
        }
    filters = [f"[0:v]crop={crop_width}:{crop_height}:{offset_x}:{offset_y},scale={width}:{height}[base]"]
    previous = "base"
    for index in range(9):
        row, column = divmod(index, 3)
        x, y = column * cell_w, row * cell_h + cell_h - label_height
        label = f"label{index}"
        output_label = f"stage{index}"
        filters.append(f"[1:v]crop={cell_w}:{label_height}:{x}:{y}[{label}]")
        filters.append(f"[{previous}][{label}]overlay={x}:{y}[{output_label}]")
        previous = output_label
    filters.append(f"[{previous}]drawgrid=w={cell_w}:h={cell_h}:t=1:c=white@0.55[out]")
    output.parent.mkdir(parents=True, exist_ok=True)
    run([ffmpeg, "-y", "-i", str(source), "-i", str(original), "-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", "1", str(output)])
    return {
        "output": str(output),
        "width": width,
        "height": height,
        "labelHeight": label_height,
        "layoutRestoredFrom": str(original),
        "alignmentMode": "whole-board-fallback",
        "detectedGrid": {"valid": False},
    }


def normalize_replacement_map(value: dict[str, Any]) -> dict[str, Any]:
    """Validate one v5.11 Segment replacement map and derive its cell groups."""
    if not isinstance(value, dict):
        raise ValueError("Replacement Map 必须是JSON对象。")
    cells = value.get("cells")
    if not isinstance(cells, list) or len(cells) != 9:
        raise ValueError("Replacement Map 必须且只能包含9格。")
    replace_product = bool(value.get("replaceProduct", False))
    replace_creator = bool(value.get("replaceCreator", False))
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    groups = {
        "productFullCells": [],
        "productPartialCells": [],
        "creatorFullCells": [],
        "creatorPartialCells": [],
        "editableCells": [],
        "frozenCells": [],
    }
    for raw in cells:
        if not isinstance(raw, dict):
            raise ValueError("Replacement Map cells 每项必须是对象。")
        index = int(raw.get("index", 0))
        if not 1 <= index <= 9 or index in seen:
            raise ValueError("Replacement Map 格号必须是唯一的1-9。")
        seen.add(index)
        cell: dict[str, Any] = {"index": index, "interactionState": str(raw.get("interactionState", ""))}
        if "regions" in raw:
            cell["regions"] = raw["regions"]
        editable = False
        for kind, enabled, full_key, partial_key in (
            ("product", replace_product, "productFullCells", "productPartialCells"),
            ("creator", replace_creator, "creatorFullCells", "creatorPartialCells"),
        ):
            source = raw.get(kind, {})
            if not isinstance(source, dict):
                raise ValueError(f"Replacement Map cell {index} {kind} 必须是对象。")
            state = str(source.get("state", "none"))
            count = source.get("count", 0)
            replace = bool(source.get("replace", False))
            if state not in {"none", "partial", "full"}:
                raise ValueError(f"Replacement Map cell {index} {kind}.state 无效。")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"Replacement Map cell {index} {kind}.count 必须是非负整数。")
            if state == "none" and (count != 0 or replace):
                raise ValueError(f"Replacement Map cell {index} {kind}=none 时 count=0 且 replace=false。")
            if state != "none" and count < 1:
                raise ValueError(f"Replacement Map cell {index} {kind}存在时 count 必须大于0。")
            if replace and not enabled:
                raise ValueError(f"Replacement Map cell {index} 要求替换{kind}，但Segment未开启该替换。")
            cell[kind] = {"state": state, "count": count, "replace": replace}
            if replace:
                editable = True
                groups[partial_key if state == "partial" else full_key].append(index)
        groups["editableCells" if editable else "frozenCells"].append(index)
        normalized.append(cell)
    if seen != set(range(1, 10)):
        raise ValueError("Replacement Map 必须正好覆盖格号01-9。")
    normalized.sort(key=lambda item: int(item["index"]))
    result = {
        "segmentId": int(value.get("segmentId", 0)),
        "replaceProduct": replace_product,
        "replaceCreator": replace_creator,
        "cells": normalized,
        **groups,
    }
    mode = value.get("mergeMode")
    if mode is not None:
        if mode == "whole-board-direct-v1":
            edit_rule = value.get("editRule")
            if not isinstance(edit_rule, str) or not edit_rule.strip():
                raise ValueError("完整板编辑必须提供明确的editRule。")
            result.update({
                "mergeMode": mode,
                "compositionMode": "whole-board-direct-v1",
                "labelHeight": int(value.get("labelHeight", 38)),
                "editRule": edit_rule,
            })
            return result
        if mode != "object-regions-v1":
            raise ValueError("未知mergeMode。")
        result.update({key: value[key] for key in ("mergeMode", "coordinateSpace", "labelHeight", "editRule", "compositionMode") if key in value})
        try:
            from scripts.storyboard_regions import validate_regions
        except ModuleNotFoundError:
            from storyboard_regions import validate_regions
        validate_regions(result)
    return result


def adaptive_edited_cells(
    ffmpeg: str,
    ffprobe: str | None,
    edited: Path,
    original_cells: Path,
    output_dir: Path,
    detection: dict[str, Any],
    editable_cells: list[int],
    cell_width: int,
    cell_height: int,
    label_height: int,
) -> tuple[dict[int, Path], dict[str, Any]]:
    """Crop the AI board by its detected grid and discard every AI-rendered label strip."""
    columns = detection["columns"]["boundaries"]
    rows = detection["rows"]["boundaries"]
    pixels = detection["pixels"]
    source_width = int(detection["sourceWidth"])
    output_dir.mkdir(parents=True, exist_ok=True)
    changes: dict[int, Path] = {}
    removed_labels: dict[str, int] = {}
    crops: dict[str, dict[str, int]] = {}
    visual_height = cell_height - label_height
    if visual_height < 1:
        raise ValueError("原Storyboard标签高度无效。")
    for index in editable_cells:
        row, column = divmod(index - 1, 3)
        left, right = int(columns[column]), int(columns[column + 1])
        top, bottom = int(rows[row]), int(rows[row + 1])
        # Drop detected separator pixels before fitting the visual content.
        if column > 0:
            left += 1
        if column < 3:
            right -= 1
        if row > 0:
            top += 1
        if row < 3:
            bottom -= 1
        if right - left < 8 or bottom - top < 8:
            raise ValueError(f"Storyboard第{index:02d}格检测区域过小。")
        label_start = detect_label_start(pixels, source_width, left, right, top, bottom)
        content_bottom = label_start
        if content_bottom - top < round((bottom - top) * .55):
            content_bottom = bottom
        removed_labels[str(index)] = max(0, bottom - content_bottom)
        crops[str(index)] = {
            "x": left,
            "y": top,
            "width": right - left,
            "height": content_bottom - top,
        }
        raw_cell = output_dir / f"cell-{index:02d}-raw.png"
        run([
            ffmpeg, "-y", "-i", str(edited),
            "-vf", f"crop={right - left}:{content_bottom - top}:{left}:{top}",
            "-frames:v", "1", str(raw_cell),
        ])
        visual = output_dir / f"cell-{index:02d}-visual.png"
        normalize_cell(ffmpeg, ffprobe, raw_cell, visual, cell_width, visual_height, .5, .5)
        final_cell = output_dir / f"cell-{index:02d}.png"
        original_cell = original_cells / f"cell-{index:02d}.png"
        run([
            ffmpeg, "-y", "-i", str(visual), "-i", str(original_cell),
            "-filter_complex",
            f"[1:v]crop={cell_width}:{label_height}:0:{visual_height}[label];"
            "[0:v][label]vstack=inputs=2[out]",
            "-map", "[out]", "-frames:v", "1", str(final_cell),
        ])
        changes[index] = final_cell
    return changes, {"aiLabelPixelsRemoved": removed_labels, "detectedCellCrops": crops}


def lock_merge(
    ffmpeg: str,
    ffprobe: str | None,
    original: Path,
    edited: Path,
    plan_path: Path,
    output: Path,
    label_height: int = 38,
) -> dict[str, Any]:
    """Deterministically use edited pixels only for mapped cells and restore frozen cells."""
    for role, path in (("原Storyboard", original), ("编辑后Storyboard", edited), ("Replacement Map", plan_path)):
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"{role}不存在或为空：{path}")
    plan = normalize_replacement_map(json.loads(plan_path.read_text(encoding="utf-8")))
    if plan.get("mergeMode") == "object-regions-v1":
        try:
            from scripts.storyboard_regions import merge
        except ModuleNotFoundError:
            from storyboard_regions import merge
        return merge(ffmpeg, ffprobe, original, edited, plan_path, output)
    width, height = dimensions(ffprobe, original, ffmpeg)
    if width % 3 or height % 3:
        raise ValueError("原Storyboard宽高必须能被3整除。")
    edited_width, edited_height = dimensions(ffprobe, edited, ffmpeg)
    detection = detect_grid_boundaries(ffmpeg, ffprobe, edited)
    grid_drift = (
        (edited_width, edited_height) != (width, height)
        or int(detection["columns"]["maxDriftPx"]) > 2
        or int(detection["rows"]["maxDriftPx"]) > 2
    )
    if grid_drift and not detection["valid"]:
        raise ValueError(
            "AI返回Storyboard尺寸或网格已漂移，且无法可靠识别3×3边界；"
            "已停止lock-merge，禁止输出错位分镜。"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    alignment_mode = "adaptive-grid" if grid_drift else "uniform-grid-fast-path"
    adaptive_metadata: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="storyboard-lock-merge-") as temporary:
        root = Path(temporary)
        original_cells = root / "original-cells"
        split_board(ffmpeg, ffprobe, original, original_cells)
        selected = root / "selected.png"
        if grid_drift:
            edited_changes, adaptive_metadata = adaptive_edited_cells(
                ffmpeg,
                ffprobe,
                edited,
                original_cells,
                root / "adaptive-cells",
                detection,
                plan["editableCells"],
                width // 3,
                height // 3,
                label_height,
            )
        else:
            edited_cells = root / "edited-cells"
            split_board(ffmpeg, ffprobe, edited, edited_cells)
            edited_changes = {
                index: edited_cells / f"cell-{index:02d}.png"
                for index in plan["editableCells"]
            }
        compose(ffmpeg, original_cells, selected, edited_changes)

        restored = root / "layout-restored.png"
        restore_board_layout(ffmpeg, ffprobe, selected, original, restored, label_height)
        restored_cells = root / "restored-cells"
        split_board(ffmpeg, ffprobe, restored, restored_cells)

        # Compose from original cells last: frozen cells therefore come only from Image 1.
        final_changes = {index: restored_cells / f"cell-{index:02d}.png" for index in plan["editableCells"]}
        compose(ffmpeg, original_cells, output, final_changes)

    final_width, final_height = dimensions(ffprobe, output, ffmpeg)
    if (final_width, final_height) != (width, height):
        raise RuntimeError("lock-merge输出尺寸与原Storyboard不一致。")
    direct_mode = plan.get("mergeMode") == "whole-board-direct-v1"
    metadata_path = output.with_suffix(".lock.json" if direct_mode else ".lock-merge.json")
    metadata = {
        "output": str(output),
        "metadata": str(metadata_path),
        "method": "whole-board-visual-review" if direct_mode else "whole-board-lock-merge",
        "segmentId": plan["segmentId"],
        "width": width,
        "height": height,
        "layout": "3x3",
        "cellCount": 9,
        "mapFile": str(plan_path),
        "mapValid": True,
        "lockMergeSucceeded": True,
        "frozenCellsRestored": True,
        "alignmentMode": alignment_mode,
        "gridDriftDetected": grid_drift,
        "editedSourceDimensions": {"width": edited_width, "height": edited_height},
        "detectedGrid": {
            "valid": detection["valid"],
            "columns": detection["columns"],
            "rows": detection["rows"],
        },
        "editableCells": plan["editableCells"],
        "frozenCells": plan["frozenCells"],
        "productFullCells": plan["productFullCells"],
        "productPartialCells": plan["productPartialCells"],
        "creatorFullCells": plan["creatorFullCells"],
        "creatorPartialCells": plan["creatorPartialCells"],
        **adaptive_metadata,
    }
    if direct_mode:
        metadata.update({
            "mergeMode": "whole-board-direct-v1",
            "compositionMode": "whole-board-direct-v1",
            "layoutRestored": True,
            "labelsRestored": True,
            "fullBoardVisualReviewRequired": True,
            "originalFile": str(original.resolve()),
            "originalSha256": hashlib.sha256(original.read_bytes()).hexdigest(),
            "editedSha256": hashlib.sha256(edited.read_bytes()).hexdigest(),
            "mapSha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            "finalSha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        })
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def fast_prepare(
    ffmpeg: str,
    ffprobe: str | None,
    original: Path,
    edited: Path,
    output: Path,
    label_height: int = 38,
) -> dict[str, Any]:
    """Mechanically normalize one AI storyboard without visual or region review."""
    for role, path in (("原Storyboard", original), ("候选Storyboard", edited)):
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"{role}不存在或为空：{path}")
    if output.exists():
        raise ValueError(f"输出已存在，请使用新文件名：{output}")
    width, height = dimensions(ffprobe, original, ffmpeg)
    if width % 3 or height % 3:
        raise ValueError("原Storyboard宽高必须能被3整除。")
    edited_width, edited_height = dimensions(ffprobe, edited, ffmpeg)
    try:
        from scripts.storyboard_regions import geometry, validate_canvas_aspect_ratio
    except ModuleNotFoundError:
        from storyboard_regions import geometry, validate_canvas_aspect_ratio
    canvas = geometry(width, height)
    drift = validate_canvas_aspect_ratio(
        edited_width, edited_height, canvas["width"], canvas["height"]
    )
    detection = detect_grid_boundaries(ffmpeg, ffprobe, edited)
    if not detection["valid"]:
        raise ValueError("候选Storyboard无法可靠识别3×3网格。")
    layout = restore_board_layout(
        ffmpeg, ffprobe, edited, original, output, label_height
    )
    final_width, final_height = dimensions(ffprobe, output, ffmpeg)
    if (final_width, final_height) != (width, height):
        raise RuntimeError("快速Storyboard输出尺寸与原板不一致。")
    metadata_path = output.with_suffix(".fast.json")
    metadata = {
        "method": "whole-board-fast-v1",
        "validationLevel": "mechanical",
        "visualReviewRequired": False,
        "layoutRestored": True,
        "labelsRestored": True,
        "canvasAspectRatioDrift": round(drift, 6),
        "maxCanvasAspectRatioDrift": 0.15,
        "originalFile": str(original.resolve()),
        "editedImageFile": str(edited.resolve()),
        "finalImageFile": str(output.resolve()),
        "originalSha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "editedSha256": hashlib.sha256(edited.read_bytes()).hexdigest(),
        "finalSha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "width": final_width,
        "height": final_height,
        "detectedGrid": {
            "valid": detection["valid"],
            "columns": detection["columns"],
            "rows": detection["rows"],
        },
        "layoutRestore": layout,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metadata["metadata"] = str(metadata_path.resolve())
    return metadata


def replacements(values: list[str]) -> dict[int, Path]:
    result: dict[int, Path] = {}
    for value in values:
        raw_index, raw_path = value.split("=", 1)
        index = int(raw_index)
        if not 1 <= index <= 9:
            raise ValueError(f"画格编号超出 1-9：{index}")
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"替换画格不存在或为空：{path}")
        result[index] = path
    return result


def compose(ffmpeg: str, cells_dir: Path, output: Path, changed: dict[int, Path], anchors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    metadata = json.loads((cells_dir / "cells.json").read_text(encoding="utf-8"))
    width, height = int(metadata["cellWidth"]), int(metadata["cellHeight"])
    inputs: list[Path] = []
    for index in range(1, 10):
        inputs.append(changed.get(index, cells_dir / f"cell-{index:02d}.png"))
    command = [ffmpeg, "-y"]
    for path in inputs:
        command.extend(["-i", str(path)])
    labels: list[str] = []
    filters: list[str] = []
    for index in range(9):
        label = f"c{index}"
        chain = f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        if index + 1 in changed and anchors and index < len(anchors):
            timestamp = float(anchors[index].get("timestamp", 0))
            chain += f",drawbox=x=0:y=0:w=130:h=46:color=black@0.75:t=fill,drawtext=text='{timestamp:.2f}s':x=8:y=8:fontsize=22:fontcolor=white"
        filters.append(chain + f"[{label}]")
        labels.append(f"[{label}]")
    layout = "|".join(f"{column * width}_{row * height}" for row in range(3) for column in range(3))
    filters.append("".join(labels) + f"xstack=inputs=9:layout={layout}[out]")
    output.parent.mkdir(parents=True, exist_ok=True)
    command.extend(["-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", "1", str(output)])
    run(command)
    return {"output": str(output), "changedCells": sorted(changed), "unchangedCells": [i for i in range(1, 10) if i not in changed]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    split = sub.add_parser("split")
    split.add_argument("--board", required=True)
    split.add_argument("--output-dir", required=True)
    compose_parser = sub.add_parser("compose")
    compose_parser.add_argument("--cells-dir", required=True)
    compose_parser.add_argument("--output", required=True)
    compose_parser.add_argument("--replacement", action="append", default=[])
    compose_parser.add_argument("--anchors-file")
    normalize = sub.add_parser("normalize")
    normalize.add_argument("--input", required=True)
    normalize.add_argument("--output", required=True)
    normalize.add_argument("--width", type=int, required=True)
    normalize.add_argument("--height", type=int, required=True)
    normalize.add_argument("--crop-x", type=float, default=0.5)
    normalize.add_argument("--crop-y", type=float, default=0.5)
    restore = sub.add_parser("restore-layout")
    restore.add_argument("--input", required=True)
    restore.add_argument("--original", required=True)
    restore.add_argument("--output", required=True)
    restore.add_argument("--label-height", type=int, default=38)
    lock = sub.add_parser("lock-merge")
    lock.add_argument("--original", required=True)
    lock.add_argument("--edited", required=True)
    lock.add_argument("--plan", required=True)
    lock.add_argument("--output", required=True)
    lock.add_argument("--label-height", type=int, default=38)
    fast = sub.add_parser("fast-prepare")
    fast.add_argument("--original", required=True)
    fast.add_argument("--edited", required=True)
    fast.add_argument("--output", required=True)
    fast.add_argument("--label-height", type=int, default=38)
    for item in (split, compose_parser, normalize, restore, lock, fast):
        item.add_argument("--ffmpeg")
        item.add_argument("--ffprobe")
    args = parser.parse_args()
    try:
        if args.command == "split":
            result = split_board(str(executable("ffmpeg", args.ffmpeg)), executable("ffprobe", args.ffprobe, required=False), Path(args.board).expanduser().resolve(), Path(args.output_dir).expanduser().resolve())
        elif args.command == "compose":
            anchors = None
            if args.anchors_file:
                raw = json.loads(Path(args.anchors_file).read_text(encoding="utf-8"))
                anchors = raw.get("anchors", raw) if isinstance(raw, dict) else raw
            result = compose(str(executable("ffmpeg", args.ffmpeg)), Path(args.cells_dir).expanduser().resolve(), Path(args.output).expanduser().resolve(), replacements(args.replacement), anchors)
        elif args.command == "normalize":
            if args.width <= 0 or args.height <= 0:
                raise ValueError("目标宽高必须为正数。")
            result = normalize_cell(
                str(executable("ffmpeg", args.ffmpeg)),
                executable("ffprobe", args.ffprobe, required=False),
                Path(args.input).expanduser().resolve(),
                Path(args.output).expanduser().resolve(),
                args.width,
                args.height,
                args.crop_x,
                args.crop_y,
            )
        elif args.command == "restore-layout":
            result = restore_board_layout(
                str(executable("ffmpeg", args.ffmpeg)),
                executable("ffprobe", args.ffprobe, required=False),
                Path(args.input).expanduser().resolve(),
                Path(args.original).expanduser().resolve(),
                Path(args.output).expanduser().resolve(),
                args.label_height,
            )
        elif args.command == "lock-merge":
            result = lock_merge(
                str(executable("ffmpeg", args.ffmpeg)),
                executable("ffprobe", args.ffprobe, required=False),
                Path(args.original).expanduser().resolve(),
                Path(args.edited).expanduser().resolve(),
                Path(args.plan).expanduser().resolve(),
                Path(args.output).expanduser().resolve(),
                args.label_height,
            )
        elif args.command == "fast-prepare":
            result = fast_prepare(
                str(executable("ffmpeg", args.ffmpeg)),
                executable("ffprobe", args.ffprobe, required=False),
                Path(args.original).expanduser().resolve(),
                Path(args.edited).expanduser().resolve(),
                Path(args.output).expanduser().resolve(),
                args.label_height,
            )
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
