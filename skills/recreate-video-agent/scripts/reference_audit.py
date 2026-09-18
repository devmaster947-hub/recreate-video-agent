#!/usr/bin/env python3
"""Audit storyboard/product/creator references before paid video generation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_MULTIVIEW_ANGLES = {"front", "back"}
SIDE_PROFILE_ANGLES = {"side_profile", "left_profile", "right_profile"}
try:
    from scripts import entity_bindings
except ModuleNotFoundError:
    import entity_bindings

CURRENT_SKILL_VERSION = "5.1"


def storyboard_validation_mode(manifest: dict[str, Any]) -> str:
    """New tasks opt into fast explicitly; missing legacy data remains strict."""
    value = str(manifest.get("userConfig", {}).get("storyboardValidationMode", "strict"))
    if value not in {"fast", "strict"}:
        raise ValueError("storyboardValidationMode 必须是 fast 或 strict。")
    return value


def segment_count(manifest: dict[str, Any]) -> int:
    prompts = manifest.get("videoPrompts", {}).get("segments", [])
    if isinstance(prompts, list) and prompts:
        return len(prompts)
    boards = manifest.get("storyboards", {}).get("generation", [])
    if isinstance(boards, list) and boards:
        return len({int(item.get("segmentId", item.get("storyboardId", -1))) for item in boards})
    planned = manifest.get("benchmarkVideo", {}).get("analysis", {}).get("recommendedSegments", [])
    return len(planned) if isinstance(planned, list) else 0


def cross_segment_creator_ids(manifest: dict[str, Any]) -> set[str]:
    appearances: dict[str, set[int]] = {}
    prompts = manifest.get("videoPrompts", {}).get("segments", [])
    if entity_bindings.load_blueprint(manifest) is not None:
        windows = manifest.get("benchmarkVideo", {}).get("analysis", {}).get("recommendedSegments", [])
        prompts = [dict(w, creatorIds=entity_bindings.segment_context(manifest, w)["creatorIds"]) for w in windows]
    if not isinstance(prompts, list) or not prompts:
        return set()
    for segment in prompts:
        if not isinstance(segment, dict):
            continue
        segment_id = int(segment.get("segmentId", -1))
        for value in segment.get("creatorIds", []):
            creator_id = str(value).strip()
            if creator_id:
                appearances.setdefault(creator_id, set()).add(segment_id)
    return {creator_id for creator_id, segment_ids in appearances.items() if len(segment_ids) > 1}


def validate_creator_references(manifest: dict[str, Any], visible_ids: set[str]) -> dict[str, Any]:
    """Enforce clean creator provenance for current and compatible legacy tasks."""
    entries = {
        str(item.get("creatorId")): item
        for item in manifest.get("creators", [])
        if isinstance(item, dict) and str(item.get("creatorId", "")).strip()
    }
    count = segment_count(manifest)
    multi_segment = count > 1
    submitted: list[str] = []
    for creator_id, item in entries.items():
        source = str(item.get("sourceType", "")).strip().lower()
        if source not in {"user_provided", "generated_multiview"}:
            raise ValueError(
                f"Creator {creator_id} 来源无效；禁止把原视频单帧登记或提交为达人参考图。"
            )
        path = Path(str(item.get("file", ""))).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"Creator {creator_id} 参考图不存在或为空：{path}")
        if source == "generated_multiview":
            if not multi_segment:
                raise ValueError("单Segment且用户未提供达人图时，不得生成或提交达人参考图。")
            views = {str(value).strip().lower() for value in item.get("views", [])}
            missing = sorted(REQUIRED_MULTIVIEW_ANGLES - views)
            if not SIDE_PROFILE_ANGLES.intersection(views):
                missing.append("side_profile")
            if missing or item.get("layout") != "multi-view":
                raise ValueError(f"Creator {creator_id} 多视图不完整，缺少：{missing}")
            if item.get("productFreeVerified") is not True:
                raise ValueError(f"Creator {creator_id} 多视图未完成无产品视觉检查。")
        if creator_id in visible_ids:
            submitted.append(creator_id)
    cross_segment = cross_segment_creator_ids(manifest)
    required_ids = visible_ids & cross_segment
    if multi_segment and not required_ids <= set(entries):
        missing = sorted(required_ids - set(entries))
        raise ValueError(f"多Segment任务必须为跨段人物提供用户达人图或生成的无产品多视图；缺少：{missing}")
    return {
        "segmentCount": count,
        "mode": "multi_segment_required" if multi_segment else "single_segment_optional",
        "creatorIds": sorted(submitted),
        "crossSegmentCreatorIds": sorted(required_ids),
    }


def version_at_least(value: str, major: int, minor: int) -> bool:
    try:
        parts = str(value).strip().split(".")
        return (int(parts[0]), int(parts[1])) >= (major, minor)
    except (ValueError, IndexError):
        return False


def current_or_legacy_at_least(value: str, major: int, minor: int) -> bool:
    """Treat the renumbered V4.3 release as current while preserving V6.x manifests."""
    normalized = str(value).strip()
    if normalized in {"", "unknown"}:
        # SkillHub 安装包的 SKILL.md 标题被改写，版本号无法识别；按当前代码能力处理。
        return True
    if normalized in {"5.0", CURRENT_SKILL_VERSION}:
        return True
    # SkillHub 发行包用独立包版本号（如 1.0.1），与技能内容版本不同步，一律按当前代码能力处理。
    if normalized.startswith("1.") and normalized.count(".") >= 2:
        return True
    return version_at_least(normalized, major, minor)


def validate_identity_roster(
    manifest: dict[str, Any], board: dict[str, Any], segment: dict[str, Any]
) -> set[str]:
    """Require every visible person to have a registered, segment-bound identity."""
    context = entity_bindings.segment_context(manifest, segment)
    known = {str(item.get("creatorId")) for item in manifest.get("creators", [])}
    visible: set[str] = set()
    anchors = board.get("anchors", [])
    if not isinstance(anchors, list) or not anchors:
        raise ValueError(f"Storyboard {board.get('storyboardId')} 缺少anchors，无法审计人物身份。")
    for position, anchor in enumerate(anchors, 1):
        if not isinstance(anchor, dict) or not isinstance(anchor.get("personPresent"), bool):
            raise ValueError(f"Storyboard {board.get('storyboardId')} 第{position}格缺少personPresent。")
        ids = anchor.get("creatorIds")
        if not isinstance(ids, list):
            raise ValueError(f"Storyboard {board.get('storyboardId')} 第{position}格creatorIds必须是数组。")
        normalized = {str(item).strip() for item in ids if str(item).strip()}
        count = anchor.get("personCount", 0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"Storyboard {board.get('storyboardId')} 第{position}格personCount无效。")
        if anchor["personPresent"] and (not normalized or (context is None and len(normalized) < max(1, count))):
            raise ValueError(
                f"Storyboard {board.get('storyboardId')} 第{position}格有{count or 1}人出镜，"
                "但未绑定全部creatorIds。"
            )
        if not anchor["personPresent"] and normalized:
            raise ValueError(f"Storyboard {board.get('storyboardId')} 第{position}格无人物却绑定creatorIds。")
        visible.update(normalized)
    if context is not None:
        expected = set(context["creatorIds"])
        if not visible <= expected:
            raise ValueError("anchor人物不属于本段完整镜头映射")
        selected = set(segment.get("creatorIds", []))
        if selected != expected:
            raise ValueError("Segment creatorIds必须覆盖全部镜头人物，不仅是九格人物")
        validate_creator_references(manifest, expected)
        return expected
    current_policy = current_or_legacy_at_least(str(manifest.get("skillVersion", "")), 6, 5)
    if current_policy:
        validate_creator_references(manifest, visible)
    elif not visible <= known:
        raise ValueError(f"Storyboard {board.get('storyboardId')} 引用了未登记的人物身份。")
    selected = {str(value) for value in segment.get("creatorIds", [])}
    if selected != visible:
        raise ValueError(
            f"Segment {segment.get('segmentId')} creatorIds必须与Storyboard实际出镜人物完全一致；"
            f"应为{sorted(visible)}，实际为{sorted(selected)}。"
        )
    return visible


def use_benchmark_product(manifest: dict[str, Any]) -> bool:
    product = manifest.get("product", {})
    mode = str(product.get("mode", "")).strip().lower() if isinstance(product, dict) else ""
    return bool(isinstance(product, dict) and (product.get("useBenchmarkProduct") is True or mode in {"benchmark", "use_benchmark", "original"}))


def product_presence(board: dict[str, Any], segment: dict[str, Any] | None = None) -> bool:
    anchors = board.get("anchors", [])
    auto_semantics = bool(anchors) and all(
        str(item.get("semanticSource", "observed")) == "unobserved"
        for item in anchors if isinstance(item, dict)
    )
    if auto_semantics:
        if isinstance(segment, dict) and isinstance(segment.get("productPresent"), bool):
            return bool(segment["productPresent"])
        replacement = board.get("replacement")
        return bool(isinstance(replacement, dict) and replacement.get("applied"))
    values = [item.get("productPresent") for item in anchors if isinstance(item, dict)]
    if not values or any(value is None for value in values):
        raise ValueError(f"Storyboard {board.get('storyboardId')} 缺少完整productPresent元数据。")
    return any(value is True for value in values)


def replacement_targets_product(board: dict[str, Any]) -> bool:
    replacement = board.get("replacement")
    if not isinstance(replacement, dict) or replacement.get("applied") is not True:
        return False
    if replacement.get("method") == "whole-board-fast-v1":
        return replacement.get("replaceProduct") is True
    map_file = str(replacement.get("mapFile", "")).strip()
    if not map_file:
        return False
    path = Path(map_file).expanduser().resolve()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Storyboard {board.get('storyboardId')} 的Replacement Map不可读取。") from exc
    return isinstance(value, dict) and value.get("replaceProduct") is True


def validate_product_references(manifest: dict[str, Any], board: dict[str, Any]) -> list[str]:
    """Fail closed when an edited board replaces the product but generation cannot send it."""
    if entity_bindings.load_blueprint(manifest) is not None:
        context = entity_bindings.segment_context(manifest, {"segmentId": int(board.get("segmentId", board.get("storyboardId")))})
        return context["productReferenceImages"]
    if not replacement_targets_product(board):
        return []
    if use_benchmark_product(manifest):
        raise ValueError(
            f"Storyboard {board.get('storyboardId')} 已替换产品，但Manifest仍标记沿用原产品；"
            "请先用 set-product-references 登记新产品图。"
        )
    product = manifest.get("product", {})
    values = product.get("productImages") if isinstance(product, dict) else None
    if not isinstance(values, list) or not values:
        raise ValueError("已选择新产品替换，但Manifest没有登记产品参考图。")
    files: list[str] = []
    for item in values:
        raw = item if isinstance(item, str) else item.get("file", item.get("filePath", "")) if isinstance(item, dict) else ""
        path = Path(str(raw)).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"产品参考图不存在或为空：{path}")
        if str(path) not in files:
            files.append(str(path))
    return files


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
    skill_version = str(manifest.get("skillVersion", "")).strip()
    requires_current_metadata = skill_version.startswith("5.13") or current_or_legacy_at_least(skill_version, 6, 2)
    protected_identity_gate = current_or_legacy_at_least(skill_version, 6, 2)
    validation_mode = storyboard_validation_mode(manifest)
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    for expected_id, segment in enumerate(prompts, 1):
        identifier = int(segment.get("segmentId", -1))
        selected = segment.get("storyboardIds")
        if identifier != expected_id or not isinstance(selected, list) or len(selected) != 1:
            raise ValueError("每个连续Segment必须且只能引用一张最终Storyboard。")
        board_id = int(selected[0])
        board = by_id.get(board_id)
        if board is None or int(board.get("segmentId", -1)) != identifier:
            raise ValueError(f"Segment {identifier}引用了不存在或不属于本段的Storyboard。")
        registered_product_files = validate_product_references(manifest, board)
        path = Path(str(board.get("file", ""))).expanduser()
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"Segment {identifier}最终Storyboard不存在或为空。")
        anchors = board.get("anchors", [])
        auto_semantics = bool(anchors) and all(
            str(anchor.get("semanticSource", "observed")) == "unobserved"
            for anchor in anchors if isinstance(anchor, dict)
        )
        inferred_product_presence = auto_semantics and not isinstance(segment.get("productPresent"), bool)
        context = entity_bindings.segment_context(manifest, segment)
        has_product = context["productPresent"] if context is not None else product_presence(board, segment)
        if context is not None and segment.get("productPresent") is not has_product:
            raise ValueError("productPresent与完整镜头不一致")
        if inferred_product_presence:
            warnings.append(
                f"Segment {identifier}缺少productPresent，已按Storyboard替换记录推断为{str(has_product).lower()}；该提示不阻断生成。"
            )
        selected_creators = {str(value) for value in segment.get("creatorIds", [])}
        if protected_identity_gate:
            selected_creators = validate_identity_roster(manifest, board, segment)
        current_creator_policy = current_or_legacy_at_least(skill_version, 6, 5)
        creator_reference_policy = validate_creator_references(manifest, selected_creators) if current_creator_policy else None
        if not current_creator_policy and not selected_creators <= creator_ids:
            raise ValueError(f"Segment {identifier}引用了不存在的Creator。")
        anchor_creators = {
            str(value)
            for anchor in board.get("anchors", []) if isinstance(anchor, dict)
            for value in anchor.get("creatorIds", [])
        }
        if context is None and not auto_semantics and not selected_creators <= anchor_creators:
            raise ValueError(f"Segment {identifier}达人引用与Storyboard metadata不一致。")
        product_replacement_required = bool(context["productReferenceImages"]) if context is not None else has_product and not benchmark_product
        # In V6.2 creatorIds also bind preserved source people. Their presence
        # alone no longer means the Storyboard replaced a creator.
        creator_replacement_required = bool(selected_creators) and not protected_identity_gate
        if current_creator_policy:
            registered_selected = {
                str(item.get("creatorId"))
                for item in manifest.get("creators", [])
                if isinstance(item, dict) and str(item.get("creatorId")) in selected_creators
            }
            creator_reference_required = bool(selected_creators) and (
                bool(selected_creators & cross_segment_creator_ids(manifest)) or bool(registered_selected)
            )
        else:
            creator_reference_required = creator_replacement_required
        if (product_replacement_required or creator_replacement_required) and not bool(board.get("replacementVerified", False)):
            raise ValueError(f"Segment {identifier}需要新产品/新达人身份，但Storyboard未标记replacementVerified文件完整性状态。")
        replacement = board.get("replacement")
        if isinstance(replacement, dict):
            expected_method = "whole-board-fast-v1" if validation_mode == "fast" else "whole-board-lock-merge"
            if protected_identity_gate and replacement.get("method") != expected_method:
                raise ValueError(
                    f"Segment {identifier} {validation_mode}模式只允许{expected_method}。"
                )
            if replacement.get("method") not in {"local-board-direct", "whole-board-lock-merge", "whole-board-user-review", "whole-board-fast-v1"}:
                raise ValueError(f"Segment {identifier} replacement.method 必须是受支持的完整故事板合成方式。")
            if requires_current_metadata or replacement.get("method") == "local-board-direct":
                try:
                    from scripts.generation_manifest import normalize_replacement_record
                except ModuleNotFoundError:
                    from generation_manifest import normalize_replacement_record
                replacement = normalize_replacement_record(replacement)
            if protected_identity_gate:
                if validation_mode == "strict" and not (
                    replacement.get("mergeMode") == "object-regions-v1"
                    and replacement.get("lockMergeSucceeded") is True
                    and replacement.get("frozenCellsRestored") is True
                    and replacement.get("protectedPixelsRestored") is True
                    and replacement.get("outsideMaskChangedPixels") == 0
                ):
                    raise ValueError(
                        f"Segment {identifier} 缺少可验证的区域锁定证据；"
                        "非目标人物与非编辑区域必须逐像素恢复。"
                    )
                if validation_mode == "fast" and not (
                    replacement.get("validationLevel") == "mechanical"
                    and replacement.get("artifactIntegrityVerified") is True
                    and replacement.get("visualReviewRequired") is False
                ):
                    raise ValueError(f"Segment {identifier} 缺少快速模式机械完整性证据。")
            if not bool(replacement.get("replacementVerified", False)):
                raise ValueError(f"Segment {identifier} replacement metadata 未完成文件完整性登记。")
        elif requires_current_metadata and (product_replacement_required or creator_replacement_required):
            raise ValueError(f"Segment {identifier} 当前任务缺少replacement metadata。")
        results.append({
            "segmentId": identifier,
            "storyboardId": board_id,
            "productPresent": has_product,
            "productReferenceRequired": product_replacement_required,
            "registeredProductImages": registered_product_files,
            "creatorReferenceRequired": creator_reference_required,
            "creatorIds": sorted(selected_creators),
            "creatorReferencePolicy": creator_reference_policy,
        })
    return {"passed": True, "warnings": warnings, "segments": results}


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
