#!/usr/bin/env python3
"""Inspect 4x4 storyboards, restore locked layout, and validate whole-board edits."""

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
    from scripts.creator_mapping import mapping_by_source, normalize_creator_replacement_map
except ModuleNotFoundError:
    from creator_mapping import mapping_by_source, normalize_creator_replacement_map


def executable(name: str, value: str | None, *, required: bool = True) -> str | None:
    if value:
        return value
    found = shutil.which(name)
    local = Path.home() / ".local" / "bin" / name
    result = found or (str(local) if local.is_file() else "")
    if not result and required:
        raise ValueError(f"当前环境缺少 {name}。")
    return result or None


def run(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:] or "画格处理失败。")
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


def split_board(ffmpeg: str, ffprobe: str | None, board: Path, output_dir: Path) -> dict[str, Any]:
    width, height = dimensions(ffprobe, board, ffmpeg)
    if width % 4 or height % 4:
        raise ValueError("Storyboard 宽高必须能被 4 整除。")
    cell_w, cell_h = width // 4, height // 4
    output_dir.mkdir(parents=True, exist_ok=True)
    cells: list[dict[str, Any]] = []
    for index in range(16):
        row, column = divmod(index, 4)
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
    width, height = dimensions(ffprobe, original, ffmpeg)
    if width % 4 or height % 4:
        raise ValueError("原Storyboard宽高必须能被4整除。")
    source_width, source_height = dimensions(ffprobe, source, ffmpeg)
    target_ratio, source_ratio = width / height, source_width / source_height
    if source_ratio > target_ratio:
        crop_width, crop_height = round(source_height * target_ratio), source_height
        offset_x, offset_y = round((source_width - crop_width) / 2), 0
    else:
        crop_width, crop_height = source_width, round(source_width / target_ratio)
        offset_x, offset_y = 0, round((source_height - crop_height) / 2)
    cell_w, cell_h = width // 4, height // 4
    if not 1 <= label_height < cell_h:
        raise ValueError("label-height必须小于单格高度。")
    filters = [f"[0:v]crop={crop_width}:{crop_height}:{offset_x}:{offset_y},scale={width}:{height}[base]"]
    previous = "base"
    for index in range(16):
        row, column = divmod(index, 4)
        x, y = column * cell_w, row * cell_h + cell_h - label_height
        label = f"label{index}"
        output_label = f"stage{index}"
        filters.append(f"[1:v]crop={cell_w}:{label_height}:{x}:{y}[{label}]")
        filters.append(f"[{previous}][{label}]overlay={x}:{y}[{output_label}]")
        previous = output_label
    filters.append(f"[{previous}]drawgrid=w={cell_w}:h={cell_h}:t=1:c=white@0.55[out]")
    output.parent.mkdir(parents=True, exist_ok=True)
    run([ffmpeg, "-y", "-i", str(source), "-i", str(original), "-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", "1", str(output)])
    return {"output": str(output), "width": width, "height": height, "labelHeight": label_height, "layoutRestoredFrom": str(original)}


def normalize_replacement_map(value: dict[str, Any]) -> dict[str, Any]:
    """Validate one v5.11 Segment replacement map and derive its cell groups."""
    if not isinstance(value, dict):
        raise ValueError("Replacement Map 必须是JSON对象。")
    cells = value.get("cells")
    if not isinstance(cells, list) or len(cells) != 16:
        raise ValueError("Replacement Map 必须且只能包含16格。")
    replace_product = bool(value.get("replaceProduct", False))
    replace_creator = bool(value.get("replaceCreator", False))
    creator_mapping = []
    creator_mapping_by_source: dict[str, dict[str, Any]] = {}
    if replace_creator:
        creator_mapping = normalize_creator_replacement_map(value.get("creatorReplacementMap"))
        creator_mapping_by_source = mapping_by_source(creator_mapping)
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
        if not 1 <= index <= 16 or index in seen:
            raise ValueError("Replacement Map 格号必须是唯一的1-16。")
        seen.add(index)
        cell: dict[str, Any] = {"index": index, "interactionState": str(raw.get("interactionState", ""))}
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
            normalized_source: dict[str, Any] = {"state": state, "count": count, "replace": replace}
            if kind == "creator":
                source_ids = [str(item) for item in source.get("sourceCreatorIds", [])]
                if len(source_ids) != len(set(source_ids)):
                    raise ValueError(f"Replacement Map cell {index} sourceCreatorIds不得重复。")
                if len(source_ids) > count:
                    raise ValueError(f"Replacement Map cell {index}可识别人物数不得超过personCount。")
                if any(item not in creator_mapping_by_source for item in source_ids):
                    raise ValueError(f"Replacement Map cell {index}引用了未映射的sourceCreatorId。")
                replaced_sources = [item for item in source_ids if creator_mapping_by_source[item]["action"] == "replace"]
                kept_sources = [item for item in source_ids if creator_mapping_by_source[item]["action"] == "keep"]
                target_ids = [creator_mapping_by_source[item]["targetCreatorId"] for item in replaced_sources]
                if replace != bool(replaced_sources):
                    raise ValueError(f"Replacement Map cell {index} creator.replace与人物级映射不一致。")
                normalized_source.update({
                    "sourceCreatorIds": source_ids,
                    "replacedSourceCreatorIds": replaced_sources,
                    "keptSourceCreatorIds": kept_sources,
                    "targetCreatorIds": target_ids,
                })
            cell[kind] = normalized_source
            if replace:
                editable = True
                groups[partial_key if state == "partial" else full_key].append(index)
        groups["editableCells" if editable else "frozenCells"].append(index)
        normalized.append(cell)
    if seen != set(range(1, 17)):
        raise ValueError("Replacement Map 必须正好覆盖格号01-16。")
    normalized.sort(key=lambda item: int(item["index"]))
    return {
        "segmentId": int(value.get("segmentId", 0)),
        "replaceProduct": replace_product,
        "replaceCreator": replace_creator,
        "creatorReplacementMap": creator_mapping,
        "cells": normalized,
        **groups,
    }


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
    width, height = dimensions(ffprobe, original, ffmpeg)
    if width % 4 or height % 4:
        raise ValueError("原Storyboard宽高必须能被4整除。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="storyboard-lock-merge-") as temporary:
        root = Path(temporary)
        normalized_edited = root / "edited-normalized.png"
        normalize_cell(ffmpeg, ffprobe, edited, normalized_edited, width, height, .5, .5)
        original_cells = root / "original-cells"
        edited_cells = root / "edited-cells"
        split_board(ffmpeg, ffprobe, original, original_cells)
        split_board(ffmpeg, ffprobe, normalized_edited, edited_cells)

        selected = root / "selected.png"
        edited_changes = {index: edited_cells / f"cell-{index:02d}.png" for index in plan["editableCells"]}
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
    metadata_path = output.with_suffix(".lock-merge.json")
    metadata = {
        "output": str(output),
        "metadata": str(metadata_path),
        "method": "whole-board-lock-merge",
        "segmentId": plan["segmentId"],
        "width": width,
        "height": height,
        "layout": "4x4",
        "cellCount": 16,
        "mapFile": str(plan_path),
        "mapValid": True,
        "lockMergeSucceeded": True,
        "frozenCellsRestored": True,
        "editableCells": plan["editableCells"],
        "frozenCells": plan["frozenCells"],
        "productFullCells": plan["productFullCells"],
        "productPartialCells": plan["productPartialCells"],
        "creatorFullCells": plan["creatorFullCells"],
        "creatorPartialCells": plan["creatorPartialCells"],
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def replacements(values: list[str]) -> dict[int, Path]:
    result: dict[int, Path] = {}
    for value in values:
        raw_index, raw_path = value.split("=", 1)
        index = int(raw_index)
        if not 1 <= index <= 16:
            raise ValueError(f"画格编号超出 1-16：{index}")
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"替换画格不存在或为空：{path}")
        result[index] = path
    return result


def compose(ffmpeg: str, cells_dir: Path, output: Path, changed: dict[int, Path], anchors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    metadata = json.loads((cells_dir / "cells.json").read_text(encoding="utf-8"))
    width, height = int(metadata["cellWidth"]), int(metadata["cellHeight"])
    inputs: list[Path] = []
    for index in range(1, 17):
        inputs.append(changed.get(index, cells_dir / f"cell-{index:02d}.png"))
    command = [ffmpeg, "-y"]
    for path in inputs:
        command.extend(["-i", str(path)])
    labels: list[str] = []
    filters: list[str] = []
    for index in range(16):
        label = f"c{index}"
        chain = f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        if index + 1 in changed and anchors and index < len(anchors):
            timestamp = float(anchors[index].get("timestamp", 0))
            chain += f",drawbox=x=0:y=0:w=130:h=46:color=black@0.75:t=fill,drawtext=text='{timestamp:.2f}s':x=8:y=8:fontsize=22:fontcolor=white"
        filters.append(chain + f"[{label}]")
        labels.append(f"[{label}]")
    layout = "|".join(f"{column * width}_{row * height}" for row in range(4) for column in range(4))
    filters.append("".join(labels) + f"xstack=inputs=16:layout={layout}[out]")
    output.parent.mkdir(parents=True, exist_ok=True)
    command.extend(["-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", "1", str(output)])
    run(command)
    return {"output": str(output), "changedCells": sorted(changed), "unchangedCells": [i for i in range(1, 17) if i not in changed]}


def validate_plan(value: dict[str, Any]) -> dict[str, Any]:
    attempts = int(value.get("attempt", 1))
    maximum = int(value.get("maxImageEditAttempts", 1))
    if maximum not in {1, 2} or not 1 <= attempts <= maximum:
        raise ValueError(f"完整Storyboard编辑最多允许{maximum}次尝试。")
    failures: list[dict[str, Any]] = []
    for item in value.get("cells", []):
        index = int(item["index"])
        expected = bool(item.get("productPresent"))
        actual = bool(item.get("editedProductPresent"))
        reasons: list[str] = []
        if expected != actual:
            reasons.append("product_presence_mismatch")
        if item.get("personPresent") != item.get("editedPersonPresent"):
            reasons.append("person_presence_mismatch")
        if item.get("personExtent") and item.get("personExtent") != item.get("editedPersonExtent"):
            reasons.append("person_extent_changed")
        if item.get("productCount") is not None and item.get("productCount") != item.get("editedProductCount"):
            reasons.append("product_count_changed")
        if item.get("personCount") is not None and item.get("personCount") != item.get("editedPersonCount"):
            reasons.append("person_count_changed")
        for flag, reason in (
            ("layoutChanged", "layout_changed"),
            ("backgroundChanged", "background_changed"),
            ("contactChanged", "contact_changed"),
            ("oldProductResidual", "old_product_residual"),
            ("oldCreatorResidual", "old_creator_residual"),
        ):
            if bool(item.get(flag, False)):
                reasons.append(reason)
        if item.get("identityMatch") is False:
            reasons.append("identity_mismatch")
        if item.get("productIdentityMatch") is False:
            reasons.append("product_identity_mismatch")
        if item.get("creatorIdentityMatch") is False:
            reasons.append("creator_identity_mismatch")
        for expected_key, actual_key, reason in (
            ("expectedReplacedSourceCreatorIds", "replacedSourceCreatorIds", "creator_source_replacement_mismatch"),
            ("expectedKeptSourceCreatorIds", "keptSourceCreatorIds", "kept_creator_changed"),
            ("expectedTargetCreatorIds", "editedTargetCreatorIds", "creator_target_mismatch"),
        ):
            if expected_key in item:
                expected_ids = {str(identifier) for identifier in item.get(expected_key, [])}
                actual_ids = {str(identifier) for identifier in item.get(actual_key, [])}
                if expected_ids != actual_ids:
                    reasons.append(reason)
        if reasons:
            failures.append({"index": index, "reasons": reasons})
    return {"passed": not failures, "attempt": attempts, "maxImageEditAttempts": maximum, "failures": failures, "hardFailure": bool(failures)}


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
    validate = sub.add_parser("validate-plan")
    validate.add_argument("--plan", required=True)
    validate.add_argument("--output")
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
    for item in (split, compose_parser, normalize, restore, lock):
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
        else:
            result = validate_plan(json.loads(Path(args.plan).read_text(encoding="utf-8")))
            if args.output:
                output = Path(args.output).expanduser().resolve()
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
