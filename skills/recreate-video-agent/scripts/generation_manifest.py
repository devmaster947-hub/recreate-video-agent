#!/usr/bin/env python3
"""Create and update the recreate-video-agent local manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import service_privacy
except ModuleNotFoundError:
    import service_privacy  # type: ignore[no-redef]

SKILL_ROOT = Path(__file__).resolve().parent.parent
TARGET_SKILL = "recreate-video-agent"
LEGACY_TARGET_SKILLS = {"recreate-video-system", "recreate-product-video", "recreate-product-video-v4"}
ACCEPTED_TARGET_SKILLS = {TARGET_SKILL, *LEGACY_TARGET_SKILLS}
PROPOSAL_CLASSIFICATIONS = {"skill_actionable", "task_specific", "provider_limited", "insufficient_evidence"}
PROPOSAL_STATUSES = {"proposal_required", "proposed", "no_change", "applied", "rolled_back", "stale"}
ALLOWED_OPTIMIZATION_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".json", ".txt"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: str | Path) -> str:
    value = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with value.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def skill_version(skill_root: str | Path | None = None) -> str:
    source = Path(skill_root or SKILL_ROOT).resolve() / "SKILL.md"
    text = source.read_text(encoding="utf-8")
    match = re.search(r"^#\s+recreate-video-agent\s+[vV]([^\s]+)", text, re.MULTILINE)
    if match:
        return match.group(1)
    # SkillHub 发行包会改写 SKILL.md 标题，此时回退到 frontmatter 的 version 字段。
    frontmatter = re.search(r"^version:\s*([^\s]+)\s*$", text, re.MULTILINE)
    return frontmatter.group(1) if frontmatter else "unknown"


def optimization_relative_path(value: str) -> Path:
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts or not raw.parts:
        raise ValueError(f"技能优化文件必须是技能内相对路径：{value}")
    if raw == Path("SKILL.md"):
        return raw
    if raw.parts[0] not in {"references", "scripts", "tests", "agents"}:
        raise ValueError(f"技能优化文件不在允许目录：{value}")
    if raw.suffix.lower() not in ALLOWED_OPTIMIZATION_SUFFIXES:
        raise ValueError(f"技能优化文件类型不允许：{value}")
    return raw


def skill_file_fingerprints(paths: list[str], skill_root: str | Path | None = None) -> dict[str, str]:
    root = Path(skill_root or SKILL_ROOT).resolve()
    result: dict[str, str] = {}
    for value in sorted(set(paths)):
        relative = optimization_relative_path(value)
        target = (root / relative).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"技能优化文件越出技能目录：{value}")
        result[str(relative)] = file_sha256(target) if target.is_file() else "missing"
    return result


def load_manifest(path: str | Path) -> dict[str, Any]:
    value = Path(path).expanduser().resolve()
    data = json.loads(value.read_text(encoding="utf-8"))
    if str(data.get("version")) != "4":
        raise ValueError("Unsupported manifest version")
    data.setdefault("schemaRevision", "4.0")
    archive = data.setdefault("migrationArchive", {})
    for field in ("shotContracts", "renderUnits"):
        legacy = data.pop(field, None)
        meaningful = bool(legacy.get("shotContracts")) if field == "shotContracts" and isinstance(legacy, dict) else bool(legacy)
        if meaningful and field not in archive:
            archive[field] = legacy
    revision = str(data.get("schemaRevision", "4.0"))
    migrating_50 = revision.startswith("5.0")
    if migrating_50 or revision.startswith("5.1") or revision.startswith("5.2"):
        data["schemaRevision"] = "5.3"
    data.setdefault("benchmarkVideo", {})
    product = data.setdefault("product", {})
    if isinstance(product, dict) and not product.get("productImages") and not product.get("images") and "useBenchmarkProduct" not in product:
        product["useBenchmarkProduct"] = True
        product.setdefault("productImages", [])
    storyboards = data.setdefault("storyboards", {"original": [], "edited": [], "generation": []})
    storyboards.setdefault("original", [])
    storyboards.setdefault("edited", [])
    storyboards.setdefault("generation", [])
    if migrating_50 and not storyboards["generation"]:
        status = str(data.get("workflowStatus", "initialized"))
        if status in {"step3_complete", "step4_complete", "step5_review_pending", "complete"}:
            if storyboards["edited"]:
                data["workflowStatus"] = "step2_complete"
            elif storyboards["original"]:
                data["workflowStatus"] = "step1_complete"
            else:
                data["workflowStatus"] = "initialized"
    config = data.setdefault("userConfig", {})
    config.setdefault("videoModel", "seedance-2-fast")
    config.setdefault("durationMode", "source")
    config.setdefault("requestedDuration", None)
    config.setdefault("customRequirement", "")
    config.setdefault("targetCountry", "跟原视频一致")
    config.setdefault("targetLanguage", "跟原视频一致")
    config.setdefault("resolution", "720p")
    config.setdefault("qualityProfile", "fast")
    config.setdefault("fidelityMode", "high_fidelity")
    config.setdefault("peopleMode", "recreate")
    config.setdefault("imageProvider", "agent_local")
    config.setdefault("videoProvider", "auto")
    # Existing manifests predate the fast path and must keep their original
    # strict semantics. Newly initialized manifests explicitly store "fast".
    config.setdefault("storyboardValidationMode", "strict")
    if config["storyboardValidationMode"] not in {"fast", "strict"}:
        raise ValueError("storyboardValidationMode 必须是 fast 或 strict。")
    data.setdefault("providerCapabilities", {})
    data.setdefault("qualityReports", [])
    data.setdefault("skillOptimizations", [])
    data.setdefault("videoGeneration", {"status": "not_started", "generated": False})
    if data.get("workflowStatus") == "step5_review_pending" and isinstance(data.get("finalVideo"), dict) and data["finalVideo"].get("file"):
        data["workflowStatus"] = "complete"
    sanitized = service_privacy.sanitize_payload(data)
    blueprint = sanitized.get("videoBlueprint")
    if isinstance(blueprint, dict) and blueprint.get("rawFile"):
        service_privacy.sanitize_json_file(blueprint["rawFile"], root=value.parent)
    if sanitized != data:
        save_manifest(value, sanitized)
    data = sanitized
    return data


def save_manifest(path: str | Path, data: dict[str, Any]) -> Path:
    value = Path(path).expanduser().resolve()
    sanitized = service_privacy.sanitize_payload(data)
    data.clear()
    data.update(sanitized)
    data["updatedAt"] = now()
    value.parent.mkdir(parents=True, exist_ok=True)
    temporary = value.with_suffix(value.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(value)
    return value


def require_file(value: str, role: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"{role}不存在或为空：{path}")
    return str(path)


def normalize_replacement_record(value: dict[str, Any]) -> dict[str, Any]:
    """Compute replacementVerified from artifact integrity, without visual review."""
    if not isinstance(value, dict):
        raise ValueError("replacement 必须是对象。")
    record = dict(value)
    attempts = int(record.get("generationAttempts", 0))
    maximum = int(record.get("maxImageEditAttempts", 1))
    if maximum not in {1, 2} or attempts < 0 or attempts > maximum:
        raise ValueError("replacement.generationAttempts 超过 maxImageEditAttempts。")

    def artifact(key: str) -> tuple[str, dict[str, Any] | None]:
        raw = str(record.get(key, "")).strip()
        if not raw:
            return "", None
        path = Path(raw).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            return str(path), None
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return str(path), None
        return str(path), parsed if isinstance(parsed, dict) else None

    edited_image = Path(str(record.get("editedImageFile", ""))).expanduser()
    image_file_valid = bool(str(record.get("editedImageFile", "")).strip()) and edited_image.is_file() and edited_image.stat().st_size > 0
    map_file, map_value = artifact("mapFile")
    lock_file, lock_value = artifact("lockMergeFile")
    normalized_map = None
    if map_value is not None:
        try:
            try:
                from scripts.storyboard_cells import normalize_replacement_map
            except ModuleNotFoundError:
                from storyboard_cells import normalize_replacement_map
            normalized_map = normalize_replacement_map(map_value)
        except (ValueError, KeyError, TypeError):
            normalized_map = None
    mapped_editable = normalized_map.get("editableCells", []) if normalized_map else []
    mapped_frozen = normalized_map.get("frozenCells", []) if normalized_map else []
    requested_editable = sorted({int(item) for item in record.get("editableCells", [])})
    requested_frozen = sorted({int(item) for item in record.get("frozenCells", [])})
    if record.get("method") == "whole-board-fast-v1":
        evidence_file, evidence = artifact("fastPrepareFile")
        final_image = Path(str(record.get("finalImageFile", ""))).expanduser().resolve()
        final_image_valid = final_image.is_file() and final_image.stat().st_size > 0
        evidence_matches = bool(
            evidence
            and evidence.get("method") == "whole-board-fast-v1"
            and evidence.get("validationLevel") == "mechanical"
            and evidence.get("layoutRestored") is True
            and evidence.get("labelsRestored") is True
        )
        for path_value, digest_key in (
            (record.get("editedImageFile"), "editedSha256"),
            (record.get("finalImageFile"), "finalSha256"),
            ((evidence or {}).get("originalFile"), "originalSha256"),
        ):
            artifact_path = Path(str(path_value or ""))
            evidence_matches = bool(
                evidence_matches and artifact_path.is_file()
                and file_sha256(artifact_path) == (evidence or {}).get(digest_key)
            )
        conditions = {
            "imageEditSucceeded": bool(record.get("imageEditSucceeded", False)) and attempts == 1 and maximum == 1 and image_file_valid,
            "layoutRestored": evidence_matches,
            "artifactIntegrityVerified": evidence_matches and final_image_valid,
        }
        verified = bool(record.get("applied", False)) and record.get("validationLevel") == "mechanical" and all(conditions.values())
        if bool(record.get("replacementVerified", False)) and not verified:
            missing = [key for key, passed in conditions.items() if not passed]
            raise ValueError("replacementVerified=true 与快速模式机械验证状态不一致：" + "、".join(missing or ["applied/validationLevel"]))
        record.update(conditions)
        record.update({
            "generationAttempts": attempts,
            "maxImageEditAttempts": maximum,
            "editedImageFile": str(edited_image.resolve()) if image_file_valid else str(record.get("editedImageFile", "")),
            "finalImageFile": str(final_image) if final_image_valid else str(record.get("finalImageFile", "")),
            "fastPrepareFile": evidence_file,
            "validationLevel": "mechanical",
            "visualReviewRequired": False,
            "fullBoardVisualReviewRequired": False,
            "replacementVerified": verified,
        })
        record.pop("visualReviewPassed", None)
        return record
    if record.get("method") == "local-board-direct":
        final_image = Path(str(record.get("finalImageFile", ""))).expanduser().resolve()
        edited_resolved = edited_image.resolve() if image_file_valid else None
        final_image_valid = final_image.is_file() and final_image.stat().st_size > 0
        expected_sha = str(record.get("localOutputSha256", "")).strip()
        source_unmodified = bool(
            record.get("sourceImageUnmodified") is True
            and image_file_valid
            and final_image_valid
            and edited_resolved == final_image
            and expected_sha
            and file_sha256(final_image) == expected_sha
        )
        conditions = {
            "imageEditSucceeded": bool(record.get("imageEditSucceeded", False)) and attempts >= 1 and image_file_valid,
            "mapValid": bool(record.get("mapValid", False)) and normalized_map is not None and requested_editable == mapped_editable and requested_frozen == mapped_frozen,
            "sourceImageUnmodified": source_unmodified,
        }
        verified = bool(record.get("applied", False)) and all(conditions.values())
        if bool(record.get("replacementVerified", False)) and not verified:
            missing = [key for key, passed in conditions.items() if not passed]
            raise ValueError("replacementVerified=true 与本地目标板验证状态不一致：" + "、".join(missing or ["applied/method"]))
        record.update(conditions)
        record["generationAttempts"] = attempts
        record["maxImageEditAttempts"] = maximum
        record["editedImageFile"] = str(final_image) if final_image_valid else str(record.get("editedImageFile", ""))
        record["finalImageFile"] = str(final_image) if final_image_valid else str(record.get("finalImageFile", ""))
        record["mapFile"] = map_file
        record["editableCells"] = requested_editable
        record["frozenCells"] = requested_frozen
        record["fullBoardVisualReviewRequired"] = False
        record["visualReviewRequired"] = False
        record.pop("visualReviewPassed", None)
        record["replacementVerified"] = verified
        return record
    if record.get("method") == "whole-board-visual-review":
        evidence_matches = bool(
            lock_value
            and lock_value.get("method") == "whole-board-visual-review"
            and lock_value.get("mergeMode") == "whole-board-direct-v1"
            and lock_value.get("layoutRestored") is True
            and lock_value.get("labelsRestored") is True
            and lock_value.get("fullBoardVisualReviewRequired") is True
            and lock_value.get("editableCells") == mapped_editable
            and lock_value.get("frozenCells") == mapped_frozen
            and normalized_map
            and normalized_map.get("mergeMode") == "whole-board-direct-v1"
        )
        for path_value, digest_key in (
            (record.get("editedImageFile"), "editedSha256"),
            (record.get("mapFile"), "mapSha256"),
            (record.get("finalImageFile"), "finalSha256"),
            ((lock_value or {}).get("originalFile"), "originalSha256"),
        ):
            artifact_path = Path(str(path_value or ""))
            evidence_matches = bool(
                evidence_matches and artifact_path.is_file()
                and file_sha256(artifact_path) == (lock_value or {}).get(digest_key)
            )
        final_image = Path(str(record.get("finalImageFile", ""))).expanduser()
        final_image_valid = bool(str(record.get("finalImageFile", "")).strip()) and final_image.is_file() and final_image.stat().st_size > 0
        review = record.get("visualReview") if isinstance(record.get("visualReview"), dict) else {}
        visual_review_passed = bool(
            review.get("targetCorrect") is True
            and review.get("protectedCorrect") is True
            and final_image_valid
            and review.get("imageSha256") == file_sha256(final_image)
            and review.get("mapSha256") == file_sha256(Path(map_file))
        )
        conditions = {
            "imageEditSucceeded": bool(record.get("imageEditSucceeded", False)) and attempts >= 1 and image_file_valid,
            "mapValid": bool(record.get("mapValid", False)) and normalized_map is not None and requested_editable == mapped_editable and requested_frozen == mapped_frozen,
            "layoutRestored": bool(record.get("layoutRestored", False)) and evidence_matches,
            "fullBoardVisualReviewRequired": True,
            "visualReviewPassed": visual_review_passed,
        }
        verified = bool(record.get("applied", False)) and all(conditions.values())
        if bool(record.get("replacementVerified", False)) and not verified:
            missing = [key for key, passed in conditions.items() if not passed]
            raise ValueError("replacementVerified=true 与实际验证状态不一致：" + "、".join(missing or ["applied/method"]))
        record.update(conditions)
        record["generationAttempts"] = attempts
        record["maxImageEditAttempts"] = maximum
        record["editedImageFile"] = str(edited_image.resolve()) if image_file_valid else str(record.get("editedImageFile", ""))
        record["mapFile"] = map_file
        record["lockMergeFile"] = lock_file
        record["finalImageFile"] = str(final_image.resolve()) if final_image_valid else str(record.get("finalImageFile", ""))
        record["editableCells"] = requested_editable
        record["frozenCells"] = requested_frozen
        record["replacementVerified"] = verified
        return record
    if record.get("method") == "whole-board-user-review":
        evidence_matches = bool(
            lock_value
            and lock_value.get("method") == "whole-board-user-review"
            and lock_value.get("mapValid") is True
            and lock_value.get("layoutRestored") is True
            and lock_value.get("labelsRestored") is True
            and lock_value.get("fullBoardUserReviewRequired") is True
            and lock_value.get("editableCells") == mapped_editable
            and lock_value.get("frozenCells") == mapped_frozen
            and normalized_map
            and normalized_map.get("mergeMode") == "object-regions-v1"
        )
        for path_value, digest_key in (
            (record.get("editedImageFile"), "editedSha256"),
            (record.get("mapFile"), "mapSha256"),
            (record.get("finalImageFile"), "finalSha256"),
            ((lock_value or {}).get("originalFile"), "originalSha256"),
        ):
            artifact_path = Path(str(path_value or ""))
            evidence_matches = bool(
                evidence_matches and artifact_path.is_file()
                and file_sha256(artifact_path) == (lock_value or {}).get(digest_key)
            )
        final_image = Path(str(record.get("finalImageFile", ""))).expanduser()
        final_image_valid = bool(str(record.get("finalImageFile", "")).strip()) and final_image.is_file() and final_image.stat().st_size > 0
        confirmed_digest = str(record.get("confirmedImageSha256", "")).strip()
        user_confirmed = bool(record.get("userConfirmed", False)) and final_image_valid and confirmed_digest == file_sha256(final_image)
        conditions = {
            "imageEditSucceeded": bool(record.get("imageEditSucceeded", False)) and attempts >= 1 and image_file_valid,
            "mapValid": bool(record.get("mapValid", False)) and normalized_map is not None and requested_editable == mapped_editable and requested_frozen == mapped_frozen,
            "layoutRestored": bool(record.get("layoutRestored", False)) and evidence_matches,
            "fullBoardUserReviewRequired": True,
            "userConfirmed": user_confirmed,
        }
        verified = bool(record.get("applied", False)) and all(conditions.values())
        if bool(record.get("replacementVerified", False)) and not verified:
            missing = [key for key, passed in conditions.items() if not passed]
            raise ValueError("replacementVerified=true 与实际验证状态不一致：" + "、".join(missing or ["applied/method"]))
        record.update(conditions)
        record["generationAttempts"] = attempts
        record["maxImageEditAttempts"] = maximum
        record["editedImageFile"] = str(edited_image.resolve()) if image_file_valid else str(record.get("editedImageFile", ""))
        record["mapFile"] = map_file
        record["lockMergeFile"] = lock_file
        record["finalImageFile"] = str(final_image.resolve()) if final_image_valid else str(record.get("finalImageFile", ""))
        record["editableCells"] = requested_editable
        record["frozenCells"] = requested_frozen
        record["replacementVerified"] = verified
        return record
    lock_matches = bool(
        lock_value
        and lock_value.get("method") == "whole-board-lock-merge"
        and lock_value.get("mapValid") is True
        and lock_value.get("lockMergeSucceeded") is True
        and lock_value.get("editableCells") == mapped_editable
        and lock_value.get("frozenCells") == mapped_frozen
    )
    if (normalized_map and normalized_map.get("mergeMode") == "object-regions-v1") or (lock_value or {}).get("mergeMode") == "object-regions-v1":
        # New records bind protection evidence to all inputs, including the mask.
        lock_matches = bool(
            lock_matches and normalized_map
            and normalized_map.get("mergeMode") == "object-regions-v1"
            and normalized_map.get("compositionMode") == "region-lock-v1"
            and (lock_value or {}).get("mergeMode") == "object-regions-v1"
            and (lock_value or {}).get("compositionMode") == "region-lock-v1"
            and (lock_value or {}).get("editRule") == normalized_map.get("editRule")
            and (lock_value or {}).get("protectedPixelsRestored") is True
            and (lock_value or {}).get("outsideMaskChangedPixels") == 0
        )
        for path_value, digest_key in (
            (record.get("editedImageFile"), "editedSha256"),
            (record.get("mapFile"), "mapSha256"),
            (record.get("finalImageFile"), "finalSha256"),
            ((lock_value or {}).get("originalFile"), "originalSha256"),
            ((lock_value or {}).get("maskFile"), "maskSha256"),
        ):
            artifact_path = Path(str(path_value or ""))
            lock_matches = bool(lock_matches and artifact_path.is_file()
                                and file_sha256(artifact_path) == (lock_value or {}).get(digest_key))
    final_image = Path(str(record.get("finalImageFile", ""))).expanduser()
    final_image_valid = bool(str(record.get("finalImageFile", "")).strip()) and final_image.is_file() and final_image.stat().st_size > 0
    conditions = {
        "imageEditSucceeded": bool(record.get("imageEditSucceeded", False)) and attempts >= 1 and image_file_valid,
        "mapValid": bool(record.get("mapValid", False)) and normalized_map is not None and requested_editable == mapped_editable and requested_frozen == mapped_frozen,
        "lockMergeSucceeded": bool(record.get("lockMergeSucceeded", False)) and lock_matches,
        "frozenCellsRestored": bool(record.get("frozenCellsRestored", False)) and lock_matches and lock_value.get("frozenCellsRestored") is True,
    }
    verified = (
        bool(record.get("applied", False))
        and record.get("method") == "whole-board-lock-merge"
        and all(conditions.values())
    )
    if bool(record.get("replacementVerified", False)) and not verified:
        missing = [key for key, passed in conditions.items() if not passed]
        raise ValueError("replacementVerified=true 与实际验证状态不一致：" + "、".join(missing or ["applied/method"]))
    record.update(conditions)
    record["generationAttempts"] = attempts
    record["maxImageEditAttempts"] = maximum
    record["editedImageFile"] = str(edited_image.resolve()) if image_file_valid else str(record.get("editedImageFile", ""))
    record["mapFile"] = map_file
    record["lockMergeFile"] = lock_file
    record["finalImageFile"] = str(final_image.resolve()) if final_image_valid else str(record.get("finalImageFile", ""))
    record["editableCells"] = requested_editable
    record["frozenCells"] = requested_frozen
    if normalized_map and normalized_map.get("mergeMode") == "object-regions-v1" and lock_value:
        record["mergeMode"] = lock_value.get("mergeMode")
        record["compositionMode"] = lock_value.get("compositionMode")
        record["protectedPixelsRestored"] = lock_value.get("protectedPixelsRestored") is True
        record["outsideMaskChangedPixels"] = lock_value.get("outsideMaskChangedPixels")
    record["replacementVerified"] = verified
    return record


def command_init(args: argparse.Namespace) -> Path:
    video_model = str(getattr(args, "video_model", "seedance-2-fast") or "seedance-2-fast")
    duration_mode = str(getattr(args, "duration_mode", "source") or "source")
    target_duration = getattr(args, "target_duration", None)
    custom_requirement = str(getattr(args, "custom_requirement", "") or "")
    target_country = str(getattr(args, "target_country", "") or "跟原视频一致")
    target_language = str(getattr(args, "target_language", "") or "跟原视频一致")
    storyboard_validation_mode = str(getattr(args, "storyboard_validation_mode", "fast") or "fast")
    if storyboard_validation_mode not in {"fast", "strict"}:
        raise ValueError("storyboardValidationMode 必须是 fast 或 strict。")
    if duration_mode not in {"source", "custom"}:
        raise ValueError("durationMode 必须是 source 或 custom。")
    if duration_mode == "custom":
        if isinstance(target_duration, bool) or not isinstance(target_duration, int) or target_duration <= 0:
            raise ValueError("custom 模式必须提供正整数 --target-duration。")
        requested_duration = target_duration
    else:
        if target_duration is not None:
            raise ValueError("source 模式不得提供 --target-duration。")
        requested_duration = None
    root = Path(args.output_root).expanduser().resolve() / args.task_id
    for name in ("analysis", "storyboards/original", "storyboards/edited", "storyboards/generation", "creators", "prompts", "videos", "candidates", "review"):
        (root / name).mkdir(parents=True, exist_ok=True)
    manifest = root / "manifest.json"
    if manifest.exists() and not args.reuse:
        raise ValueError(f"manifest 已存在：{manifest}")
    if not manifest.exists():
        created = now()
        save_manifest(
            manifest,
            {
                "version": "4",
                "schemaRevision": "5.3",
                "skillVersion": skill_version(),
                "taskId": args.task_id,
                "createdAt": created,
                "updatedAt": created,
                "benchmarkVideo": {},
                "product": {"useBenchmarkProduct": True, "productImages": []},
                "userConfig": {
                    "videoModel": video_model,
                    "durationMode": duration_mode,
                    "requestedDuration": requested_duration,
                    "customRequirement": custom_requirement,
                    "targetCountry": target_country,
                    "targetLanguage": target_language,
                    "resolution": "720p",
                    "qualityProfile": "fast",
                    "plannerVersion": "2",
                    "fidelityMode": "high_fidelity",
                    "peopleMode": "recreate",
                    "imageProvider": "agent_local",
                    "videoProvider": "auto",
                    "storyboardValidationMode": storyboard_validation_mode,
                },
                "storyboards": {"original": [], "edited": [], "generation": []},
                "providerCapabilities": {},
                "creators": [],
                "videoBlueprint": {
                    "source": "local_agent",
                    "file": "",
                },
                "videoPrompts": {"file": "", "segments": []},
                "videos": [],
                "videoGeneration": {"status": "not_started", "generated": False},
                "finalVideo": None,
                "qualityReports": [],
                "skillOptimizations": [],
                "activeCandidate": "candidate-01",
                "workflowStatus": "initialized",
            },
        )
    return manifest


def update(path: str | Path, mutator) -> Path:
    data = load_manifest(path)
    mutator(data)
    return save_manifest(path, data)


def set_json_field(path: str | Path, field: str, json_file: str) -> Path:
    source = Path(json_file).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    return update(path, lambda data: data.__setitem__(field, value))


def set_benchmark_analysis(path: str | Path, analysis_file: str) -> Path:
    source = Path(analysis_file).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value.get("media"), dict):
        raise ValueError("基准视频分析缺少 media。")
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("benchmarkVideo", {})["analysis"] = {"file": str(source), **value}
        if value.get("sourceFile"):
            data["benchmarkVideo"]["file"] = str(Path(value["sourceFile"]).expanduser().resolve())
        config = data.setdefault("userConfig", {})
        if value.get("targetDuration") is not None:
            config["duration"] = int(value["targetDuration"])
        if value.get("durationMode") is not None:
            config["durationMode"] = str(value["durationMode"])
        if "requestedDuration" in value:
            config["requestedDuration"] = value["requestedDuration"]
    return update(path, mutate)


def add_storyboard(
    path: str | Path,
    kind: str,
    image: str,
    board_id: int,
    anchors: list[dict[str, Any]] | None = None,
    quality: dict[str, Any] | None = None,
    replacement: dict[str, Any] | None = None,
) -> Path:
    image_path = require_file(image, "Storyboard")
    def mutate(data: dict[str, Any]) -> None:
        boards = data["storyboards"][kind]
        boards[:] = [item for item in boards if int(item["storyboardId"]) != board_id]
        entry: dict[str, Any] = {"storyboardId": board_id, "file": image_path, "layout": "3x3", "realFrames": kind == "original"}
        entry["layout"] = "4x4" if anchors and len(anchors) == 16 else "3x3"
        if anchors is not None:
            entry["anchors"] = anchors
        if quality is not None:
            entry["quality"] = quality
        if replacement is not None:
            if kind != "edited":
                raise ValueError("replacement metadata 只能登记到 edited Storyboard。")
            entry["replacement"] = normalize_replacement_record(replacement)
            entry["replacementVerified"] = bool(entry["replacement"]["replacementVerified"])
        boards.append(entry)
        boards.sort(key=lambda item: int(item["storyboardId"]))
        if kind == "original":
            data["workflowStatus"] = "step1_complete"
        elif kind == "edited":
            data["workflowStatus"] = "step2_complete"
        else:
            data["workflowStatus"] = "step2_artifact_incomplete"
    return update(path, mutate)


def anchors_from_payload(payload: Any, board_id: int) -> list[dict[str, Any]]:
    """Accept either a raw anchors array or storyboard.py's metadata document."""
    if isinstance(payload, list):
        anchors = payload
    elif isinstance(payload, dict) and isinstance(payload.get("boards"), list):
        match = next(
            (
                board
                for board in payload["boards"]
                if int(board.get("storyboardId", board.get("segmentId", -1))) == int(board_id)
            ),
            None,
        )
        if match is None:
            raise ValueError(f"Storyboard metadata 中没有 storyboardId={board_id}。")
        anchors = match.get("anchors")
    else:
        raise ValueError("anchors 文件必须是9项数组或 storyboard-metadata.json。")
    if not isinstance(anchors, list) or len(anchors) not in (9, 16):
        raise ValueError(f"Storyboard {board_id} 必须包含9或16个 anchors。")
    return anchors


def add_original_storyboards_from_metadata(path: str | Path, metadata_file: str | Path) -> Path:
    """Register every original board from storyboard.py with one manifest write."""
    source = Path(metadata_file).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    boards = payload.get("boards") if isinstance(payload, dict) else None
    if not isinstance(boards, list) or not boards:
        raise ValueError("Storyboard metadata 缺少非空 boards 数组。")
    entries: list[dict[str, Any]] = []
    seen: set[int] = set()
    for board in boards:
        board_id = int(board.get("storyboardId", board.get("segmentId", -1)))
        if board_id < 1 or board_id in seen:
            raise ValueError(f"Storyboard metadata 含无效或重复ID：{board_id}")
        seen.add(board_id)
        if str(board.get("layout", payload.get("layout", ""))) not in ("3x3", "4x4"):
            raise ValueError(f"Storyboard {board_id} 不是3x3布局。")
        image_path = require_file(board.get("file", ""), f"Storyboard {board_id}")
        entries.append(
            {
                "storyboardId": board_id,
                "file": image_path,
                "layout": board.get("layout", "3x3"),
                "realFrames": True,
                "anchors": anchors_from_payload(payload, board_id),
            }
        )

    def mutate(data: dict[str, Any]) -> None:
        existing = {
            int(item["storyboardId"]): item
            for item in data["storyboards"]["original"]
            if int(item["storyboardId"]) not in seen
        }
        for entry in entries:
            existing[int(entry["storyboardId"])] = entry
        data["storyboards"]["original"] = [existing[key] for key in sorted(existing)]
        data["workflowStatus"] = "step1_complete"

    return update(path, mutate)


CREATOR_SOURCE_TYPES = {"user_provided", "generated_multiview"}
REQUIRED_MULTIVIEW_ANGLES = {"front", "back"}
SIDE_PROFILE_ANGLES = {"side_profile", "left_profile", "right_profile"}


def add_creator(
    path: str | Path,
    image: str,
    creator_id: str,
    source_type: str,
    views: list[str] | None = None,
    product_free_verified: bool = False,
) -> Path:
    image_path = require_file(image, "Creator 图")
    source = str(source_type).strip().lower()
    if source not in CREATOR_SOURCE_TYPES:
        raise ValueError("Creator来源只允许 user_provided 或 generated_multiview；禁止登记原视频抽帧。")
    normalized_views = sorted({str(value).strip().lower() for value in (views or []) if str(value).strip()})
    if source == "generated_multiview":
        missing = sorted(REQUIRED_MULTIVIEW_ANGLES - set(normalized_views))
        if not SIDE_PROFILE_ANGLES.intersection(normalized_views):
            missing.append("side_profile")
        if missing:
            raise ValueError(f"生成的达人多视图缺少角度：{missing}")
        if not product_free_verified:
            raise ValueError("生成的达人多视图必须完成无产品视觉检查。")
    def mutate(data: dict[str, Any]) -> None:
        data["creators"] = [item for item in data["creators"] if str(item["creatorId"]) != creator_id]
        entry: dict[str, Any] = {
            "creatorId": creator_id,
            "file": image_path,
            "sourceType": source,
            "layout": "multi-view" if source == "generated_multiview" else "provided",
        }
        if source == "generated_multiview":
            entry["views"] = normalized_views
            entry["productFreeVerified"] = True
            entry["imageProvider"] = "agent_native"
        data["creators"].append(entry)
    return update(path, mutate)


def set_product_references(path: str | Path, images: list[str]) -> Path:
    """Register the exact product images that must be sent to video generation."""
    if not images:
        raise ValueError("新产品替换必须至少提供一张产品参考图。")
    normalized: list[str] = []
    for image in images:
        image_path = require_file(image, "产品参考图")
        if image_path not in normalized:
            normalized.append(image_path)

    def mutate(data: dict[str, Any]) -> None:
        product = data.setdefault("product", {})
        if not isinstance(product, dict):
            raise ValueError("manifest.product 必须是对象。")
        product["mode"] = "replacement"
        product["useBenchmarkProduct"] = False
        product["productImages"] = [{"file": value} for value in normalized]
        product.pop("images", None)

    return update(path, mutate)


def set_generation_storyboards(path: str | Path, metadata_file: str) -> Path:
    source = Path(metadata_file).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    boards = value.get("boards") if isinstance(value, dict) else None
    if not isinstance(boards, list) or not boards:
        raise ValueError("段内 Storyboard 元数据缺少非空 boards。")
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in boards:
        board_id = int(item["storyboardId"])
        segment_id = int(item["segmentId"])
        if board_id in seen:
            raise ValueError(f"段内 Storyboard ID 重复：{board_id}")
        seen.add(board_id)
        file_path = require_file(str(item["file"]), "段内 Storyboard")
        global_start = float(item["globalStart"])
        global_end = float(item["globalEnd"])
        local_start = float(item.get("localStart", 0))
        local_end = float(item["localEnd"])
        if global_end <= global_start or abs(local_start) > .01 or abs(local_end - (global_end - global_start)) > .01:
            raise ValueError(f"段内 Storyboard {board_id} 时间范围无效。")
        anchors = item.get("anchors", [])
        if not isinstance(anchors, list) or len(anchors) not in (9, 16):
            raise ValueError(f"段内 Storyboard {board_id} 必须包含9或16个锚点。")
        entry = {
            "storyboardId": board_id,
            "segmentId": segment_id,
            "file": file_path,
            "layout": "4x4" if len(anchors) == 16 else "3x3",
            "globalStart": global_start,
            "globalEnd": global_end,
            "localStart": local_start,
            "localEnd": local_end,
            "replacementVerified": bool(item.get("replacementVerified", False)),
            "anchors": anchors,
        }
        if isinstance(item.get("replacement"), dict):
            entry["replacement"] = normalize_replacement_record(item["replacement"])
            entry["replacementVerified"] = bool(entry["replacement"]["replacementVerified"])
            if entry["replacement"].get("applied") and not entry["replacementVerified"]:
                raise ValueError(f"段内 Storyboard {board_id} 尚未完成本地文件完整性登记。")
        normalized.append(entry)
    normalized.sort(key=lambda item: int(item["segmentId"]))
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("storyboards", {})["generation"] = normalized
        data["schemaRevision"] = "5.3"
        data["generationStoryboardStatus"] = "ready"
    return update(path, mutate)


def set_provider_capabilities(path: str | Path, capability_file: str) -> Path:
    source = Path(capability_file).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("providerCapabilities 必须为对象。")
    return update(path, lambda data: data.__setitem__("providerCapabilities", value))


def validate_prompt_record(prompts: dict[str, Any], data: dict[str, Any]) -> None:
    """Check only fields required to execute video generation; do not judge prompt content."""
    segments = prompts.get("segments") if isinstance(prompts, dict) else None
    if not isinstance(segments, list) or not segments:
        raise ValueError("本地智能体输出缺少非空 videoPrompts.segments。")
    generation_boards = data.get("storyboards", {}).get("generation", [])
    if not generation_boards:
        raise ValueError("Step 4 前必须登记 storyboards.generation。")
    windows = data.get("benchmarkVideo", {}).get("analysis", {}).get("recommendedSegments", [])
    if data.get("timelinePlan"):
        bp = json.loads(Path(data["videoBlueprint"]["file"]).read_text())["videoBlueprint"]
        shots = bp["逐镜头拆解"]
        beats = bp["节奏结构"]["dramaticBeats"]
        utterances = [u for s in shots for u in s["声音"]["utterances"]]
        if len(segments) != len(windows):
            raise ValueError("Prompt必须保留锁定Segment数量")
        for segment, window in zip(segments, windows):
            if any(segment.get(k) != window.get(k) for k in ("segmentId", "globalStart", "globalEnd", "duration")):
                raise ValueError("Prompt改变了锁定时间窗")
            if segment.get("storyboardIds") != [window["segmentId"]]:
                raise ValueError("Storyboard与Segment不对应")
            for field, items, identifier in (("shotIds", shots, "shotId"), ("beatIds", beats, "beatId"), ("utteranceIds", utterances, "utteranceId")):
                expected = {i[identifier] for i in items if i["start"] < window["globalEnd"] and i["end"] > window["globalStart"]}
                if set(segment.get(field, [])) != expected:
                    raise ValueError(f"Segment {window['segmentId']} 的{field}遗漏或越界")
    try:
        from scripts import entity_bindings
    except ModuleNotFoundError:
        import entity_bindings
    for segment in segments:
        context = entity_bindings.segment_context(data, segment)
        if context is not None:
            if set(segment.get("creatorIds", [])) != set(context["creatorIds"]):
                raise ValueError("Prompt的creatorIds必须覆盖完整镜头的目标人物")
            if segment.get("productPresent") is not context["productPresent"]:
                raise ValueError("Prompt的productPresent与完整镜头不一致")
    seen: set[int] = set()
    for position, segment in enumerate(segments, 1):
        if not isinstance(segment, dict):
            raise ValueError(f"videoPrompts.segments[{position}] 必须是对象。")
        try:
            identifier = int(segment["segmentId"])
            duration = float(segment["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Segment {position} 缺少可执行的 segmentId/duration。") from exc
        if identifier < 1 or identifier in seen or duration <= 0:
            raise ValueError(f"Segment {position} 的 segmentId/duration 无法用于生成。")
        seen.add(identifier)
        if not isinstance(segment.get("prompt"), str) or not segment["prompt"].strip():
            raise ValueError(f"Segment {identifier} 缺少非空 prompt。")
        storyboard_ids = segment.get("storyboardIds")
        if not isinstance(storyboard_ids, list) or not storyboard_ids:
            raise ValueError(f"Segment {identifier} 缺少 storyboardIds。")


def set_replacement_bindings(path: str | Path, bindings_file: str) -> Path:
    try:
        from scripts import entity_bindings
    except ModuleNotFoundError:
        import entity_bindings
    data = load_manifest(path)
    bp = entity_bindings.load_blueprint(data)
    if bp is None:
        raise ValueError("旧蓝图不自动迁移；replacementBindings仅用于Blueprint 7.0")
    payload = json.loads(Path(bindings_file).read_text(encoding="utf-8"))
    data["replacementBindings"] = payload["replacementBindings"]
    entity_bindings.validate_bindings(data, bp)
    return update(path, lambda current: current.__setitem__("replacementBindings", data["replacementBindings"]))


def set_video_blueprint(path: str | Path, blueprint_file: str) -> Path:
    source = Path(blueprint_file).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    blueprint = value.get("videoBlueprint") if isinstance(value, dict) else None
    if not isinstance(blueprint, dict) or not blueprint:
        raise ValueError("本地智能体输出缺少 videoBlueprint。")
    if blueprint.get("schemaVersion") not in {None, "6.0"}:
        try:
            from scripts.entity_bindings import validate_blueprint
        except ModuleNotFoundError:
            from entity_bindings import validate_blueprint
        validate_blueprint(blueprint)
    def register(data: dict[str, Any]) -> None:
        previous = data.get("videoBlueprint", {})
        data["videoBlueprint"] = {
            **previous, "source": "local_agent", "file": str(source),
        }
        if previous.get("file"):
            data["videoBlueprint"]["sourceFile"] = previous.get("sourceFile", previous["file"])
    return update(path, register)


def set_prompts(path: str | Path, prompts_file: str) -> Path:
    source = Path(prompts_file).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    prompts = value.get("videoPrompts") if isinstance(value, dict) else None
    data = load_manifest(path)
    validate_prompt_record(prompts, data)
    segments = prompts["segments"]
    quality, adaptation = prompts.get("qualitySpec"), prompts.get("adaptationPlan")
    prompt_record = {"file": str(source), "segments": segments}
    if isinstance(quality, dict):
        prompt_record["qualitySpec"] = quality
    if isinstance(adaptation, dict):
        prompt_record["adaptationPlan"] = adaptation
    def mutate(current: dict[str, Any]) -> None:
        current["videoPrompts"] = prompt_record
        if prompt_record.get("adaptationPlan"):
            current.setdefault("product", {})["adaptationPlan"] = prompt_record["adaptationPlan"]
        current["workflowStatus"] = "step4_complete"
    return update(path, mutate)


def set_video(path: str | Path, segment_id: int, status: str, **fields: Any) -> Path:
    candidate_id = str(fields.pop("candidateId", "candidate-01"))
    def mutate(data: dict[str, Any]) -> None:
        videos = data["videos"]
        entry = next((item for item in videos if int(item["segmentId"]) == segment_id), None)
        if entry is None:
            entry = {"segmentId": segment_id}
            videos.append(entry)
        clean = {key: value for key, value in fields.items() if value not in (None, "")}
        attempt = next((item for item in entry.setdefault("attempts", []) if item.get("candidateId") == candidate_id), None)
        if attempt is None:
            attempt = {"candidateId": candidate_id}
            entry["attempts"].append(attempt)
        attempt.update({"status": status, **clean})
        entry.update({"candidateId": candidate_id, "status": status, **clean})
        videos.sort(key=lambda item: int(item["segmentId"]))
    return update(path, mutate)


def command_set_final(args: argparse.Namespace) -> Path:
    output = require_file(args.output_file, "最终视频")
    candidate_id = str(getattr(args, "candidate_id", "candidate-01"))
    return update(args.manifest, lambda data: (data.__setitem__("finalVideo", {"file": output, "segmentIds": list(args.segment_id), "candidateId": candidate_id}), data.__setitem__("activeCandidate", candidate_id), data.__setitem__("workflowStatus", "complete")))


def next_candidate_id(data: dict[str, Any]) -> str:
    values = [str(item.get("candidateId", "")) for video in data.get("videos", []) for item in video.get("attempts", [])]
    numbers = [int(value.rsplit("-", 1)[-1]) for value in values if value.startswith("candidate-") and value.rsplit("-", 1)[-1].isdigit()]
    return f"candidate-{max(numbers, default=0) + 1:02d}"


def validate_skill_optimization_proposal(proposal: dict[str, Any], report: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    if proposal.get("proposalVersion") != "1.0":
        raise ValueError("技能优化方案 proposalVersion 必须为 1.0。")
    if str(proposal.get("candidateId")) != candidate_id:
        raise ValueError("技能优化方案 candidateId 与质量报告不一致。")
    if proposal.get("targetSkill") not in ACCEPTED_TARGET_SKILLS:
        raise ValueError(
            f"技能优化方案 targetSkill 必须为 {TARGET_SKILL}；"
            f"已有任务兼容值为 {', '.join(sorted(LEGACY_TARGET_SKILLS))}。"
        )
    if proposal.get("confirmationPhrase") != "确认优化 skill":
        raise ValueError("技能优化方案 confirmationPhrase 必须为“确认优化 skill”。")
    if report.get("status") != "failed" or bool(report.get("passed", False)):
        raise ValueError("只有最终状态为 failed 的质量报告可以登记技能优化方案。")

    issues = proposal.get("issues")
    changes = proposal.get("changes")
    exclusions = proposal.get("exclusions")
    if not isinstance(issues, list) or not issues:
        raise ValueError("技能优化方案 issues 必须为非空数组。")
    if not isinstance(changes, list) or not isinstance(exclusions, list):
        raise ValueError("技能优化方案 changes 和 exclusions 必须为数组。")

    source_findings = report.get("findings", [])
    source_hard = [str(value) for value in report.get("hardFailures", [])]
    if not isinstance(source_findings, list):
        raise ValueError("质量报告 findings 必须为数组。")
    issue_ids: set[str] = set()
    finding_coverage: set[int] = set()
    hard_coverage: set[str] = set()
    classifications: dict[str, str] = {}
    for item in issues:
        if not isinstance(item, dict):
            raise ValueError("技能优化方案 issue 必须为对象。")
        issue_id = str(item.get("issueId", "")).strip()
        classification = str(item.get("classification", "")).strip()
        if not issue_id or issue_id in issue_ids:
            raise ValueError("技能优化方案 issueId 不能为空或重复。")
        if classification not in PROPOSAL_CLASSIFICATIONS:
            raise ValueError(f"技能优化方案分类无效：{classification}")
        if not str(item.get("evidence", "")).strip() or not str(item.get("reason", "")).strip():
            raise ValueError(f"技能优化方案 {issue_id} 缺少 evidence 或 reason。")
        issue_ids.add(issue_id)
        classifications[issue_id] = classification
        indices = item.get("sourceFindingIndices", [])
        hard_codes = item.get("hardFailureCodes", [])
        if not isinstance(indices, list) or not isinstance(hard_codes, list):
            raise ValueError(f"技能优化方案 {issue_id} 的来源字段必须为数组。")
        for index in indices:
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(source_findings):
                raise ValueError(f"技能优化方案 {issue_id} 引用了无效 finding 索引。")
            finding_coverage.add(index)
        for code in hard_codes:
            value = str(code)
            if value not in source_hard:
                raise ValueError(f"技能优化方案 {issue_id} 引用了未知 hardFailure：{value}")
            hard_coverage.add(value)
    if finding_coverage != set(range(len(source_findings))):
        raise ValueError("技能优化方案必须覆盖质量报告中的每条 finding。")
    if hard_coverage != set(source_hard):
        raise ValueError("技能优化方案必须覆盖质量报告中的每个 hardFailure。")

    change_ids: set[str] = set()
    addressed: set[str] = set()
    paths: list[str] = []
    for item in changes:
        if not isinstance(item, dict):
            raise ValueError("技能优化方案 change 必须为对象。")
        change_id = str(item.get("changeId", "")).strip()
        if not change_id or change_id in change_ids:
            raise ValueError("技能优化方案 changeId 不能为空或重复。")
        change_ids.add(change_id)
        path = str(optimization_relative_path(str(item.get("path", ""))))
        paths.append(path)
        for key in ("section", "change", "expectedEffect"):
            if not str(item.get(key, "")).strip():
                raise ValueError(f"技能优化方案 {change_id} 缺少 {key}。")
        if not isinstance(item.get("risks"), list) or not isinstance(item.get("tests"), list):
            raise ValueError(f"技能优化方案 {change_id} 的 risks 和 tests 必须为数组。")
        addresses = item.get("addresses")
        if not isinstance(addresses, list) or not addresses:
            raise ValueError(f"技能优化方案 {change_id} 必须引用至少一个 issue。")
        for issue_id in addresses:
            value = str(issue_id)
            if classifications.get(value) != "skill_actionable":
                raise ValueError(f"技能优化修改只能引用 skill_actionable issue：{value}")
            addressed.add(value)
    actionable = {key for key, value in classifications.items() if value == "skill_actionable"}
    if addressed != actionable:
        raise ValueError("每个 skill_actionable issue 必须且只能由 changes 覆盖。")

    excluded_ids: set[str] = set()
    for item in exclusions:
        if not isinstance(item, dict):
            raise ValueError("技能优化方案 exclusion 必须为对象。")
        issue_id = str(item.get("issueId", ""))
        if classifications.get(issue_id) == "skill_actionable" or issue_id not in classifications:
            raise ValueError(f"技能优化排除项引用无效：{issue_id}")
        if issue_id in excluded_ids or not str(item.get("reason", "")).strip():
            raise ValueError("技能优化排除项重复或缺少原因。")
        excluded_ids.add(issue_id)
    expected_excluded = issue_ids - actionable
    if excluded_ids != expected_excluded:
        raise ValueError("exclusions 必须恰好覆盖全部非 skill_actionable issue。")
    if not isinstance(proposal.get("acceptanceTests"), list):
        raise ValueError("技能优化方案 acceptanceTests 必须为数组。")
    return {"paths": paths, "status": "proposed" if changes else "no_change", "issueCount": len(issues), "changeCount": len(changes)}


def set_skill_optimization_proposal(path: str | Path, candidate_id: str, proposal_file: str) -> Path:
    proposal_path = require_file(proposal_file, "技能优化方案")
    proposal = json.loads(Path(proposal_path).read_text(encoding="utf-8"))
    data = load_manifest(path)
    report_entry = next((item for item in reversed(data.get("qualityReports", [])) if item.get("candidateId") == candidate_id), None)
    if not report_entry:
        raise ValueError(f"候选 {candidate_id} 没有质量报告。")
    report_path = require_file(str(report_entry.get("file", "")), "质量报告")
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    validated = validate_skill_optimization_proposal(proposal, report, candidate_id)
    report_digest = file_sha256(report_path)
    proposal_digest = file_sha256(proposal_path)
    fingerprints = skill_file_fingerprints(validated["paths"])

    def mutate(current: dict[str, Any]) -> None:
        records = current.setdefault("skillOptimizations", [])
        record = next((item for item in reversed(records) if item.get("candidateId") == candidate_id and item.get("qualityReportSha256") == report_digest), None)
        if record is None:
            record = {"candidateId": candidate_id, "qualityReport": report_path, "qualityReportSha256": report_digest, "createdAt": now()}
            records.append(record)
        for item in records:
            if item is not record and item.get("candidateId") == candidate_id and item.get("status") in {"proposal_required", "proposed"}:
                item["status"] = "stale"
                item["staleAt"] = now()
        record.update({
            "proposalId": f"{candidate_id}-{proposal_digest[:12]}",
            "proposalFile": proposal_path,
            "proposalSha256": proposal_digest,
            "sourceSkillVersion": skill_version(),
            "sourceFileFingerprints": fingerprints,
            "issueCount": validated["issueCount"],
            "changeCount": validated["changeCount"],
            "status": validated["status"],
            "proposedAt": now(),
        })
    return update(path, mutate)


def set_skill_optimization_result(path: str | Path, candidate_id: str, proposal_file: str, status: str, validation_file: str | None = None) -> Path:
    if status not in {"applied", "rolled_back", "stale"}:
        raise ValueError("技能优化结果状态必须为 applied、rolled_back 或 stale。")
    proposal_path = require_file(proposal_file, "技能优化方案")
    proposal_digest = file_sha256(proposal_path)
    validation_path = require_file(validation_file, "技能优化验证报告") if validation_file else None
    validation = json.loads(Path(validation_path).read_text(encoding="utf-8")) if validation_path else None
    if status == "applied" and (not isinstance(validation, dict) or not bool(validation.get("passed"))):
        raise ValueError("登记 applied 必须提供 passed=true 的验证报告。")
    if status == "rolled_back" and (not isinstance(validation, dict) or bool(validation.get("passed"))):
        raise ValueError("登记 rolled_back 必须提供 passed=false 的验证报告。")

    def mutate(data: dict[str, Any]) -> None:
        record = next((item for item in reversed(data.setdefault("skillOptimizations", [])) if item.get("candidateId") == candidate_id and item.get("proposalSha256") == proposal_digest), None)
        if record is None:
            raise ValueError("manifest 中没有匹配的技能优化方案，或方案内容已变化。")
        if record.get("status") == "no_change":
            raise ValueError("no_change 方案不能应用。")
        record["status"] = status
        record["resultAt"] = now()
        if validation_path:
            record["validationReport"] = validation_path
        if status == "applied":
            record["appliedSkillVersion"] = skill_version()
            record["appliedFileFingerprints"] = skill_file_fingerprints(list(record.get("sourceFileFingerprints", {}).keys()))
    return update(path, mutate)


def set_quality_report(path: str | Path, candidate_id: str, report_file: str) -> Path:
    report_path = require_file(report_file, "质量报告")
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    report_digest = file_sha256(report_path)
    def mutate(data: dict[str, Any]) -> None:
        reports = data.setdefault("qualityReports", [])
        reports[:] = [item for item in reports if item.get("candidateId") != candidate_id]
        reports.append({"candidateId": candidate_id, "file": report_path, "sha256": report_digest, "status": report.get("status", "reported"), "passed": bool(report.get("passed", False)), "deliveryBlocked": False})
        final_video = data.get("finalVideo")
        if isinstance(final_video, dict) and final_video.get("candidateId") == candidate_id:
            final_video["qualityReport"] = report_path
            final_video["qualityStatus"] = report.get("status", "reported")
            final_video["qualityPassed"] = bool(report.get("passed", False))
            data["workflowStatus"] = "complete"
    return update(path, mutate)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--task-id", required=True)
    init.add_argument("--output-root", required=True)
    init.add_argument("--reuse", action="store_true")
    init.add_argument("--video-model", default="seedance-2-fast")
    init.add_argument("--duration-mode", choices=("source", "custom"), default="source")
    init.add_argument("--target-duration", type=int)
    init.add_argument("--custom-requirement", default="")
    init.add_argument("--target-country", default="跟原视频一致")
    init.add_argument("--target-language", default="跟原视频一致")
    init.add_argument("--storyboard-validation-mode", choices=("fast", "strict"), default="fast")
    board = sub.add_parser("add-storyboard")
    board.add_argument("--manifest", required=True)
    board.add_argument("--kind", choices=("original", "edited"), required=True)
    board.add_argument("--image", required=True)
    board.add_argument("--storyboard-id", type=int, required=True)
    board.add_argument("--anchors-file")
    board.add_argument("--quality-file")
    board.add_argument("--replacement-file")
    batch_boards = sub.add_parser("add-storyboards-from-metadata")
    batch_boards.add_argument("--manifest", required=True)
    batch_boards.add_argument("--metadata-file", required=True)
    creator = sub.add_parser("add-creator")
    creator.add_argument("--manifest", required=True)
    creator.add_argument("--image", required=True)
    creator.add_argument("--creator-id", required=True)
    creator.add_argument("--source-type", choices=sorted(CREATOR_SOURCE_TYPES), required=True)
    creator.add_argument("--view", action="append", default=[])
    creator.add_argument("--product-free-verified", action="store_true")
    product = sub.add_parser("set-product-references")
    product.add_argument("--manifest", required=True)
    product.add_argument("--image", action="append", required=True)
    generation_boards = sub.add_parser("set-generation-storyboards")
    generation_boards.add_argument("--manifest", required=True)
    generation_boards.add_argument("--metadata-file", required=True)
    capabilities = sub.add_parser("set-provider-capabilities")
    capabilities.add_argument("--manifest", required=True)
    capabilities.add_argument("--file", required=True)
    blueprint = sub.add_parser("set-video-blueprint")
    blueprint.add_argument("--manifest", required=True)
    blueprint.add_argument("--file", required=True)
    bindings = sub.add_parser("set-replacement-bindings")
    bindings.add_argument("--manifest", required=True)
    bindings.add_argument("--file", required=True)
    prompts = sub.add_parser("set-prompts")
    prompts.add_argument("--manifest", required=True)
    prompts.add_argument("--file", required=True)
    final = sub.add_parser("set-final")
    final.add_argument("--manifest", required=True)
    final.add_argument("--output-file", required=True)
    final.add_argument("--segment-id", type=int, action="append", default=[])
    final.add_argument("--candidate-id", default="candidate-01")
    quality_report = sub.add_parser("set-quality-report")
    quality_report.add_argument("--manifest", required=True)
    quality_report.add_argument("--candidate-id", required=True)
    quality_report.add_argument("--file", required=True)
    optimization_proposal = sub.add_parser("set-skill-optimization-proposal")
    optimization_proposal.add_argument("--manifest", required=True)
    optimization_proposal.add_argument("--candidate-id", required=True)
    optimization_proposal.add_argument("--file", required=True)
    optimization_result = sub.add_parser("set-skill-optimization-result")
    optimization_result.add_argument("--manifest", required=True)
    optimization_result.add_argument("--candidate-id", required=True)
    optimization_result.add_argument("--proposal", required=True)
    optimization_result.add_argument("--status", choices=("applied", "rolled_back", "stale"), required=True)
    optimization_result.add_argument("--validation-file")
    benchmark_analysis = sub.add_parser("set-benchmark-analysis")
    benchmark_analysis.add_argument("--manifest", required=True)
    benchmark_analysis.add_argument("--file", required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = command_init(args)
        elif args.command == "add-storyboard":
            anchors_payload = json.loads(Path(args.anchors_file).read_text(encoding="utf-8")) if args.anchors_file else None
            anchors = anchors_from_payload(anchors_payload, args.storyboard_id) if anchors_payload is not None else None
            quality = json.loads(Path(args.quality_file).read_text(encoding="utf-8")) if args.quality_file else None
            replacement = json.loads(Path(args.replacement_file).read_text(encoding="utf-8")) if args.replacement_file else None
            result = add_storyboard(args.manifest, args.kind, args.image, args.storyboard_id, anchors, quality, replacement)
        elif args.command == "add-storyboards-from-metadata":
            result = add_original_storyboards_from_metadata(args.manifest, args.metadata_file)
        elif args.command == "add-creator":
            result = add_creator(
                args.manifest, args.image, args.creator_id, args.source_type,
                args.view, args.product_free_verified,
            )
        elif args.command == "set-product-references":
            result = set_product_references(args.manifest, args.image)
        elif args.command == "set-generation-storyboards":
            result = set_generation_storyboards(args.manifest, args.metadata_file)
        elif args.command == "set-provider-capabilities":
            result = set_provider_capabilities(args.manifest, args.file)
        elif args.command == "set-video-blueprint":
            result = set_video_blueprint(args.manifest, args.file)
        elif args.command == "set-replacement-bindings":
            result = set_replacement_bindings(args.manifest, args.file)
        elif args.command == "set-prompts":
            result = set_prompts(args.manifest, args.file)
        elif args.command == "set-quality-report":
            result = set_quality_report(args.manifest, args.candidate_id, args.file)
        elif args.command == "set-skill-optimization-proposal":
            result = set_skill_optimization_proposal(args.manifest, args.candidate_id, args.file)
        elif args.command == "set-skill-optimization-result":
            result = set_skill_optimization_result(args.manifest, args.candidate_id, args.proposal, args.status, args.validation_file)
        elif args.command == "set-benchmark-analysis":
            result = set_benchmark_analysis(args.manifest, args.file)
        else:
            result = command_set_final(args)
        print(json.dumps({"ok": True, "manifest": str(result)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
