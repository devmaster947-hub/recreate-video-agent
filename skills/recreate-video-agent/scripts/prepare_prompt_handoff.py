#!/usr/bin/env python3
"""Build and register local-agent prompt handoff Storyboards in one idempotent step."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import generation_manifest, reference_audit, entity_bindings
except ModuleNotFoundError:
    import generation_manifest
    import reference_audit
    import entity_bindings


ANCHOR_FIELDS = (
    "shotId",
    "anchorRole", "eventType", "productPresent", "productVisibility", "productCount",
    "personPresent", "personExtent", "personCount", "creatorIds", "interactionState",
    "semanticSource",
)
HANDOFF_POLICY_VERSION = "3"


def _json_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_digest(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().resolve().open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _segment_windows(manifest: Mapping[str, Any]) -> dict[int, tuple[float, float]]:
    analysis = manifest.get("benchmarkVideo", {}).get("analysis", {})
    segments = analysis.get("recommendedSegments", [])
    result: dict[int, tuple[float, float]] = {}
    for item in segments:
        identifier = int(item["segmentId"])
        result[identifier] = (float(item["globalStart"]), float(item["globalEnd"]))
    return result


def _selected_boards(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    container = manifest.get("storyboards", {})
    original = list(container.get("original") or [])
    edited = list(container.get("edited") or [])
    if not original:
        raise ValueError("必须先登记原始 Storyboard。")
    originals = {int(item["storyboardId"]): item for item in original}
    validation_mode = reference_audit.storyboard_validation_mode(dict(manifest))
    selected = edited or original
    if len(selected) != len(originals):
        raise ValueError("最终 Storyboard 必须与原始 Storyboard 数量一致。")
    for item in selected:
        identifier = int(item["storyboardId"])
        if identifier not in originals:
            raise ValueError(f"最终 Storyboard {identifier} 没有对应原始 Storyboard。")
        if reference_audit.current_or_legacy_at_least(str(manifest.get("skillVersion", "")), 6, 2):
            visible = {
                str(value)
                for anchor in item.get("anchors", []) if isinstance(anchor, dict)
                for value in anchor.get("creatorIds", [])
                if str(value).strip()
            }
            context = entity_bindings.segment_context(dict(manifest), {"segmentId": identifier})
            if context is not None:
                visible = set(context["creatorIds"])
            reference_audit.validate_identity_roster(
                dict(manifest), item, {"segmentId": identifier, "creatorIds": sorted(visible)}
            )
        replacement = item.get("replacement")
        if isinstance(replacement, dict) and replacement.get("applied"):
            reference_audit.validate_product_references(dict(manifest), item)
            expected_method = "whole-board-fast-v1" if validation_mode == "fast" else "whole-board-lock-merge"
            if reference_audit.current_or_legacy_at_least(str(manifest.get("skillVersion", "")), 6, 2) and replacement.get("method") != expected_method:
                raise ValueError(
                    f"Storyboard {identifier} {validation_mode}模式必须使用{expected_method}。"
                )
            normalized = generation_manifest.normalize_replacement_record(replacement)
            if not item.get("replacementVerified") or not normalized.get("replacementVerified"):
                raise ValueError(f"Storyboard {identifier} 尚未完成本地文件完整性登记。")
    return sorted(selected, key=lambda item: int(item.get("segmentId", item["storyboardId"])))


def build_plan(manifest: Mapping[str, Any], cell_roots: Mapping[int, Path]) -> dict[str, Any]:
    windows = _segment_windows(manifest)
    segments: list[dict[str, Any]] = []
    cursor = 0.0
    for board in _selected_boards(manifest):
        storyboard_id = int(board["storyboardId"])
        segment_id = int(board.get("segmentId", storyboard_id))
        if "globalStart" in board and "globalEnd" in board:
            start, end = float(board["globalStart"]), float(board["globalEnd"])
        elif segment_id in windows:
            start, end = windows[segment_id]
        else:
            raise ValueError(f"Storyboard {storyboard_id} 缺少可确定的 Segment 时间窗。")
        if abs(start - cursor) > .01 or end <= start:
            raise ValueError("Segment 时间窗必须从0开始连续、无重叠、无空档。")
        anchors = board.get("anchors")
        if not isinstance(anchors, list) or len(anchors) not in (9, 16):
            raise ValueError(f"Storyboard {storyboard_id} 必须包含9或16个 anchors。")
        cells: list[dict[str, Any]] = []
        for index, anchor in enumerate(anchors, 1):
            timestamp = anchor.get("globalTimestamp", anchor.get("timestamp"))
            if timestamp is None:
                raise ValueError(f"Storyboard {storyboard_id} 第{index}格缺少全局时间。")
            cell = {
                "file": str((cell_roots[segment_id] / f"cell-{index:02d}.png").resolve()),
                "globalTimestamp": float(timestamp),
            }
            for field in ANCHOR_FIELDS:
                if field in anchor:
                    cell[field] = anchor[field]
            cells.append(cell)
        segment = {
            "segmentId": segment_id,
            "globalStart": start,
            "globalEnd": end,
            "labelHeight": int(board.get("labelHeight", 38)),
            "replacementVerified": bool(board.get("replacementVerified", False)),
            "cells": cells,
        }
        if isinstance(board.get("replacement"), dict):
            segment["replacement"] = board["replacement"]
        segments.append(segment)
        cursor = end
    expected = manifest.get("userConfig", {}).get("duration")
    if expected is not None and abs(cursor - float(expected)) > .01:
        raise ValueError("最终 Storyboard 未覆盖完整复刻时长。")
    return {"segments": segments}


def input_fingerprint(manifest: Mapping[str, Any], boards: list[Mapping[str, Any]]) -> str:
    identity = {
        "schema": "replication-package-v1",
        "handoffPolicyVersion": HANDOFF_POLICY_VERSION,
        "duration": manifest.get("userConfig", {}).get("duration"),
        "sourceBlueprint": entity_bindings.load_blueprint(dict(manifest)),
        "replacementBindings": manifest.get("replacementBindings"),
        "boards": [
            {
                "storyboardId": int(board["storyboardId"]),
                "fileSha256": _file_digest(str(board["file"])),
                "anchors": board.get("anchors", []),
                "replacementVerified": bool(board.get("replacementVerified", False)),
                "replacement": board.get("replacement"),
            }
            for board in boards
        ],
    }
    return _json_digest(identity)


def _single_segment_direct_metadata(board: Mapping[str, Any], start: float, end: float) -> dict[str, Any]:
    """Reuse a final 3x3 board when global and segment-local time are identical."""
    anchors = board.get("anchors")
    if not isinstance(anchors, list) or len(anchors) not in (9, 16):
        raise ValueError("单段 Storyboard 必须包含9或16个 anchors。")
    normalized: list[dict[str, Any]] = []
    for index, anchor in enumerate(anchors, 1):
        timestamp = anchor.get("globalTimestamp", anchor.get("timestamp"))
        if timestamp is None:
            raise ValueError(f"单段 Storyboard 第{index}格缺少全局时间。")
        global_timestamp = float(timestamp)
        item = {
            "index": int(anchor.get("index", index)),
            "globalTimestamp": global_timestamp,
            "localTimestamp": global_timestamp - start,
        }
        for field in ANCHOR_FIELDS:
            if field in anchor:
                item[field] = anchor[field]
        normalized.append(item)
    result = {
        "storyboardId": int(board["storyboardId"]),
        "segmentId": int(board.get("segmentId", board["storyboardId"])),
        "file": str(Path(str(board["file"])).expanduser().resolve()),
        "layout": "4x4" if len(anchors) == 16 else "3x3",
        "globalStart": start,
        "globalEnd": end,
        "localStart": 0.0,
        "localEnd": end - start,
        "replacementVerified": bool(board.get("replacementVerified", False)),
        "labelHeight": int(board.get("labelHeight", 38)),
        "anchors": normalized,
    }
    if isinstance(board.get("replacement"), dict):
        result["replacement"] = board["replacement"]
    return {"schemaRevision": "5.1", "layout": "3x3", "boards": [result], "totalDuration": end - start}


def _all_segments_direct_metadata(manifest: Mapping[str, Any], boards: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Register every selected target Storyboard byte-for-byte without splitting or relabeling it."""
    windows = _segment_windows(manifest)
    results: list[dict[str, Any]] = []
    cursor = 0.0
    for board in boards:
        segment_id = int(board.get("segmentId", board["storyboardId"]))
        if "globalStart" in board and "globalEnd" in board:
            start, end = float(board["globalStart"]), float(board["globalEnd"])
        elif segment_id in windows:
            start, end = windows[segment_id]
        else:
            raise ValueError(f"Storyboard {board['storyboardId']} 缺少可确定的 Segment 时间窗。")
        if abs(start - cursor) > .01 or end <= start:
            raise ValueError("Segment 时间窗必须从0开始连续、无重叠、无空档。")
        direct = _single_segment_direct_metadata(board, start, end)["boards"][0]
        results.append(direct)
        cursor = end
    expected = manifest.get("userConfig", {}).get("duration")
    if expected is not None and abs(cursor - float(expected)) > .01:
        raise ValueError("最终 Storyboard 未覆盖完整复刻时长。")
    return {"schemaRevision": "5.4", "layout": "3x3", "boards": results, "totalDuration": cursor}


def prepare(manifest_path: Path, output_dir: Path, ffmpeg: str | None = None, ffprobe: str | None = None) -> dict[str, Any]:
    started = time.monotonic()
    manifest = generation_manifest.load_manifest(manifest_path)
    boards = _selected_boards(manifest)
    fingerprint = input_fingerprint(manifest, boards)
    report_path = output_dir / "replication-package.json"
    if report_path.is_file():
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        generation = manifest.get("storyboards", {}).get("generation", [])
        files_valid = bool(generation) and all(Path(str(item.get("file", ""))).is_file() for item in generation)
        if previous.get("inputFingerprint") == fingerprint and files_valid:
            return {"ok": True, "reused": True, "package": str(report_path), "elapsedSeconds": round(time.monotonic() - started, 3)}

    # The selected target board is authoritative for every Segment. Keep local/global
    # timing in metadata only; never split, relabel, resize, or recompose pixels.
    metadata = _all_segments_direct_metadata(manifest, boards)
    metadata_path = output_dir / "segment-storyboard-metadata.json"
    _atomic_json(metadata_path, metadata)
    generation_manifest.set_generation_storyboards(manifest_path, str(metadata_path))
    report = {
        "schema": "replication-package-v1",
        "handoffPolicyVersion": HANDOFF_POLICY_VERSION,
        "inputFingerprint": fingerprint,
        "manifest": str(manifest_path),
        "generationMetadata": str(metadata_path),
        "segmentCount": len(boards),
        "entityContexts": [
            dict(segmentId=int(b.get("segmentId", b["storyboardId"])), **context)
            for b in boards
            for context in [entity_bindings.segment_context(manifest, {"segmentId": int(b.get("segmentId", b["storyboardId"]))})]
            if context is not None
        ],
        "directReuse": True,
        "pixelPolicy": "target-board-byte-for-byte",
        "timings": {
            "splitSeconds": 0.0,
            "renderAndRegisterSeconds": 0.0,
            "totalSeconds": round(time.monotonic() - started, 3),
        },
    }
    _atomic_json(report_path, report)
    return {"ok": True, "reused": False, "package": str(report_path), **report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    args = parser.parse_args()
    try:
        manifest_path = Path(args.manifest).expanduser().resolve()
        output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else manifest_path.parent / "storyboards" / "generation"
        print(json.dumps(prepare(manifest_path, output_dir, args.ffmpeg, args.ffprobe), ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
