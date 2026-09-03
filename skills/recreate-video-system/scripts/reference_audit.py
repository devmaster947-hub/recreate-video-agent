#!/usr/bin/env python3
"""Audit storyboard/product/creator references before paid video generation."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def use_benchmark_product(manifest: dict[str, Any]) -> bool:
    product = manifest.get("product", {})
    mode = str(product.get("mode", "")).strip().lower() if isinstance(product, dict) else ""
    return bool(isinstance(product, dict) and (product.get("useBenchmarkProduct") is True or mode in {"benchmark", "use_benchmark", "original"}))


def product_presence(board: dict[str, Any]) -> bool:
    anchors = board.get("anchors", [])
    values = [item.get("productPresent") for item in anchors if isinstance(item, dict)]
    if not values or any(value is None for value in values):
        raise ValueError(f"Storyboard {board.get('storyboardId')} 缺少完整productPresent元数据。")
    return any(value is True for value in values)


def audit(manifest: dict[str, Any], segments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    boards = manifest.get("storyboards", {}).get("generation", [])
    if not isinstance(boards, list) or not boards:
        raise ValueError("付费提交前必须有非空storyboards.generation。")
    by_id = {int(item["storyboardId"]): item for item in boards}
    prompts = segments if segments is not None else manifest.get("videoPrompts", {}).get("segments", [])
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("付费提交前必须有非空videoPrompts.segments。")
    creator_ids = {str(item.get("creatorId")) for item in manifest.get("creators", [])}
    benchmark_product = use_benchmark_product(manifest)
    version_match = re.match(r"^(\d+)\.(\d+)", str(manifest.get("skillVersion", "")).strip())
    version_tuple = (int(version_match.group(1)), int(version_match.group(2))) if version_match else (0, 0)
    requires_verified_metadata = version_tuple >= (5, 11)
    mapped_target_ids = {
        str(item.get("targetCreatorId"))
        for item in manifest.get("creatorReplacementMap", [])
        if isinstance(item, dict) and item.get("action") == "replace"
    }
    results: list[dict[str, Any]] = []
    for expected_id, segment in enumerate(prompts, 1):
        identifier = int(segment.get("segmentId", -1))
        selected = segment.get("storyboardIds")
        if identifier != expected_id or not isinstance(selected, list) or len(selected) != 1:
            raise ValueError("每个连续Segment必须且只能引用一张最终Storyboard。")
        board_id = int(selected[0])
        board = by_id.get(board_id)
        if board is None or int(board.get("segmentId", -1)) != identifier:
            raise ValueError(f"Segment {identifier}引用了不存在或不属于本段的Storyboard。")
        path = Path(str(board.get("file", ""))).expanduser()
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"Segment {identifier}最终Storyboard不存在或为空。")
        has_product = product_presence(board)
        selected_creators = {str(value) for value in segment.get("creatorIds", [])}
        if not selected_creators <= creator_ids:
            raise ValueError(f"Segment {identifier}引用了不存在的Creator。")
        if mapped_target_ids and not selected_creators <= mapped_target_ids:
            raise ValueError(f"Segment {identifier}引用了未映射给原片人物的Creator。")
        anchor_creators = {
            str(value)
            for anchor in board.get("anchors", []) if isinstance(anchor, dict)
            for value in anchor.get("creatorIds", [])
        }
        if not selected_creators <= anchor_creators:
            raise ValueError(f"Segment {identifier}达人引用与Storyboard metadata不一致。")
        product_replacement_required = has_product and not benchmark_product
        creator_replacement_required = bool(selected_creators)
        if (product_replacement_required or creator_replacement_required) and not bool(board.get("replacementVerified", False)):
            raise ValueError(f"Segment {identifier}需要新产品/新达人身份，但Storyboard未标记replacementVerified。")
        replacement = board.get("replacement")
        if isinstance(replacement, dict):
            if replacement.get("method") != "whole-board-lock-merge":
                raise ValueError(f"Segment {identifier} replacement.method 必须是 whole-board-lock-merge。")
            if requires_verified_metadata:
                try:
                    from scripts.generation_manifest import normalize_replacement_record
                except ModuleNotFoundError:
                    from generation_manifest import normalize_replacement_record
                replacement = normalize_replacement_record(replacement)
            if not bool(replacement.get("replacementVerified", False)):
                raise ValueError(f"Segment {identifier} replacement metadata 未完成全部验证。")
        elif requires_verified_metadata and (product_replacement_required or creator_replacement_required):
            raise ValueError(f"Segment {identifier}当前任务缺少replacement metadata。")
        results.append({
            "segmentId": identifier,
            "storyboardId": board_id,
            "productPresent": has_product,
            "productReferenceRequired": product_replacement_required,
            "creatorReferenceRequired": creator_replacement_required,
            "creatorIds": sorted(selected_creators),
        })
    return {"passed": True, "segments": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    path = Path(args.manifest).expanduser().resolve()
    result = audit(json.loads(path.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
