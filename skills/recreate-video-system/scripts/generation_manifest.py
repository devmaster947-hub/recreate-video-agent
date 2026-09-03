#!/usr/bin/env python3
"""Create and update the recreate-video-system v4 manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.prompt_preflight import validate_segments
    from scripts.creator_mapping import normalize_creator_replacement_map
except ModuleNotFoundError:
    from prompt_preflight import validate_segments
    from creator_mapping import normalize_creator_replacement_map


SKILL_ROOT = Path(__file__).resolve().parent.parent
TARGET_SKILL = "recreate-video-system"
LEGACY_TARGET_SKILLS = {"recreate-product-video", "recreate-product-video-v4"}
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
    match = re.search(r"^#\s+recreate-video-system\s+v([^\s]+)", source.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else "unknown"


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
    if migrating_50 or revision.startswith("5.1") or revision.startswith("5.2") or revision.startswith("5.3"):
        data["schemaRevision"] = "5.4"
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
    config.setdefault("resolution", "720p")
    config.setdefault("qualityProfile", "strict")
    config.setdefault("fidelityMode", "high_fidelity")
    config.setdefault("peopleMode", "recreate")
    data.setdefault("providerCapabilities", {})
    data.setdefault("creatorReplacementMap", [])
    data.setdefault("qualityReports", [])
    data.setdefault("skillOptimizations", [])
    return data


def save_manifest(path: str | Path, data: dict[str, Any]) -> Path:
    value = Path(path).expanduser().resolve()
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
    """Compute replacementVerified from the six v5.11 verification conditions."""
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
    audit_file, audit_value = artifact("auditFile")
    validation_file, validation_value = artifact("validationFile")
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
    audited_indices = sorted({
        int(item.get("index", 0))
        for item in (audit_value or {}).get("cells", [])
        if isinstance(item, dict)
    })
    lock_matches = bool(
        lock_value
        and lock_value.get("method") == "whole-board-lock-merge"
        and lock_value.get("mapValid") is True
        and lock_value.get("lockMergeSucceeded") is True
        and lock_value.get("editableCells") == mapped_editable
        and lock_value.get("frozenCells") == mapped_frozen
    )
    conditions = {
        "imageEditSucceeded": bool(record.get("imageEditSucceeded", False)) and attempts >= 1 and image_file_valid,
        "mapValid": bool(record.get("mapValid", False)) and normalized_map is not None and requested_editable == mapped_editable and requested_frozen == mapped_frozen,
        "lockMergeSucceeded": bool(record.get("lockMergeSucceeded", False)) and lock_matches,
        "frozenCellsRestored": bool(record.get("frozenCellsRestored", False)) and lock_matches and lock_value.get("frozenCellsRestored") is True,
        "editableCellsAudited": bool(record.get("editableCellsAudited", False)) and audit_value is not None and audited_indices == mapped_editable,
        "validationPassed": bool(record.get("validationPassed", False)) and validation_value is not None and validation_value.get("passed") is True,
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
    record["auditFile"] = audit_file
    record["validationFile"] = validation_file
    record["editableCells"] = requested_editable
    record["frozenCells"] = requested_frozen
    record["replacementVerified"] = verified
    return record


def command_init(args: argparse.Namespace) -> Path:
    video_model = str(getattr(args, "video_model", "seedance-2-fast") or "seedance-2-fast")
    duration_mode = str(getattr(args, "duration_mode", "source") or "source")
    target_duration = getattr(args, "target_duration", None)
    custom_requirement = str(getattr(args, "custom_requirement", "") or "")
    if duration_mode not in {"source", "opening_10", "custom"}:
        raise ValueError("durationMode 必须是 source、opening_10 或 custom。")
    if duration_mode == "custom":
        if isinstance(target_duration, bool) or not isinstance(target_duration, int) or target_duration <= 0:
            raise ValueError("custom 模式必须提供正整数 --target-duration。")
        requested_duration = target_duration
    elif duration_mode == "opening_10":
        if target_duration not in (None, 10):
            raise ValueError("opening_10 模式固定为10秒。")
        requested_duration = 10
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
                "schemaRevision": "5.4",
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
                    "resolution": "720p",
                    "qualityProfile": "strict",
                    "fidelityMode": "high_fidelity",
                    "peopleMode": "recreate",
                },
                "storyboards": {"original": [], "edited": [], "generation": []},
                "providerCapabilities": {},
                "creators": [],
                "creatorReplacementMap": [],
                "videoBlueprint": {"source": "user_provided_gemini", "file": ""},
                "videoPrompts": {"file": "", "segments": []},
                "publicMedia": {},
                "videos": [],
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
        entry: dict[str, Any] = {"storyboardId": board_id, "file": image_path, "layout": "4x4", "realFrames": kind == "original"}
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
        data["workflowStatus"] = "step1_complete" if kind == "original" else "step2_complete"
    return update(path, mutate)


def add_creator(path: str | Path, image: str, creator_id: str) -> Path:
    image_path = require_file(image, "Creator 图")
    def mutate(data: dict[str, Any]) -> None:
        data["creators"] = [item for item in data["creators"] if str(item["creatorId"]) != creator_id]
        data["creators"].append({"creatorId": creator_id, "file": image_path, "layout": "2x2"})
    return update(path, mutate)


def set_creator_replacement_map(path: str | Path, mapping_file: str) -> Path:
    source = Path(mapping_file).expanduser().resolve()
    raw = json.loads(source.read_text(encoding="utf-8"))
    value = raw.get("creatorReplacementMap") if isinstance(raw, dict) else raw
    data = load_manifest(path)
    available_targets = {str(item.get("creatorId", "")) for item in data.get("creators", [])}
    normalized = normalize_creator_replacement_map(value, available_target_ids=available_targets)
    observed_sources = {
        str(identifier)
        for board in data.get("storyboards", {}).get("original", [])
        for anchor in board.get("anchors", [])
        if isinstance(anchor, dict)
        for identifier in anchor.get("sourceCreatorIds", [])
    }
    mapped_sources = {str(item["sourceCreatorId"]) for item in normalized}
    if observed_sources and mapped_sources != observed_sources:
        missing = sorted(observed_sources - mapped_sources)
        extra = sorted(mapped_sources - observed_sources)
        raise ValueError(f"达人映射必须完整覆盖原片可识别人物；缺失={missing}，多余={extra}")
    return update(path, lambda current: current.__setitem__("creatorReplacementMap", normalized))


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
        if not isinstance(anchors, list) or len(anchors) != 16:
            raise ValueError(f"段内 Storyboard {board_id} 必须包含16个锚点。")
        entry = {
            "storyboardId": board_id,
            "segmentId": segment_id,
            "file": file_path,
            "layout": "4x4",
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
        normalized.append(entry)
    normalized.sort(key=lambda item: int(item["segmentId"]))
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("storyboards", {})["generation"] = normalized
        data["schemaRevision"] = "5.4"
        data["generationStoryboardStatus"] = "ready"
    return update(path, mutate)


def set_blueprint(path: str | Path, blueprint_file: str) -> Path:
    source = Path(blueprint_file).expanduser().resolve()
    original = source.read_text(encoding="utf-8")
    raw = original.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            raw = "\n".join(lines[1:-1]).strip()
        if raw.lower().startswith("json\n"):
            raw = raw[5:].lstrip()
    if not raw:
        raise ValueError("用户返回的Gemini拆解结果为空。")
    analysis_dir = Path(path).expanduser().resolve().parent / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    raw_copy = analysis_dir / "gemini-user-result.raw.txt"
    raw_copy.write_text(original, encoding="utf-8")
    normalized = analysis_dir / "gemini-analysis.txt"
    normalized.write_text(raw + "\n", encoding="utf-8")
    return update(path, lambda data: (data.__setitem__("videoBlueprint", {"source": "user_provided_gemini", "file": str(normalized.resolve()), "rawFile": str(raw_copy.resolve()), "acceptedDirectly": True}), data.__setitem__("workflowStatus", "step3_complete")))


def set_provider_capabilities(path: str | Path, capability_file: str) -> Path:
    source = Path(capability_file).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("providerCapabilities 必须为对象。")
    return update(path, lambda data: data.__setitem__("providerCapabilities", value))


def set_prompts(path: str | Path, prompts_file: str) -> Path:
    source = Path(prompts_file).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    prompts = value.get("videoPrompts") if isinstance(value, dict) else None
    segments = prompts.get("segments") if isinstance(prompts, dict) else None
    if not isinstance(segments, list) or not segments:
        raise ValueError("Codex 输出缺少非空 videoPrompts.segments。")
    data = load_manifest(path)
    config = data.get("userConfig", {})
    generation_boards = data.get("storyboards", {}).get("generation", [])
    if not generation_boards:
        raise ValueError("Step 4 前必须登记 storyboards.generation。")
    windows = {int(item["storyboardId"]): item for item in generation_boards}
    adaptation = prompts.get("adaptationPlan")
    quality = prompts.get("qualitySpec")
    if not isinstance(adaptation, dict) or not isinstance(quality, dict):
        raise ValueError("videoPrompts.adaptationPlan 和 qualitySpec 必须是对象。")
    custom_requirement = str(config.get("customRequirement", ""))
    if custom_requirement:
        if adaptation.get("customRequirement") != custom_requirement:
            raise ValueError("adaptationPlan.customRequirement 必须原样保留用户自定义复刻要求。")
        if not any(custom_requirement in str(item.get("prompt", "")) for item in segments):
            raise ValueError("至少一个Segment Prompt必须原样落实用户自定义复刻要求。")
    for key in ("hardAnchors", "requiredCuts", "proof", "cta"):
        if not isinstance(quality.get(key), list):
            raise ValueError(f"videoPrompts.qualitySpec.{key} 必须是数组。")
    validate_segments(
        segments,
        str(config.get("videoModel", "seedance-2-fast")),
        str(config.get("resolution", "720p")),
        target_duration=int(config["duration"]) if config.get("duration") is not None else None,
        available_storyboards={int(item["storyboardId"]) for item in generation_boards},
        available_creators={str(item["creatorId"]) for item in data.get("creators", [])},
        storyboard_windows=windows,
    )
    prompt_record = {
        "file": str(source), "segments": segments,
        "qualitySpec": quality,
        "adaptationPlan": adaptation,
    }
    def mutate(current: dict[str, Any]) -> None:
        current["videoPrompts"] = prompt_record
        if prompt_record["adaptationPlan"]:
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
    return update(args.manifest, lambda data: (data.__setitem__("finalVideo", {"file": output, "segmentIds": list(args.segment_id), "candidateId": candidate_id}), data.__setitem__("activeCandidate", candidate_id), data.__setitem__("workflowStatus", "step5_review_pending")))


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
        reports.append({"candidateId": candidate_id, "file": report_path, "sha256": report_digest, "status": report.get("status", "needs_visual_review"), "passed": bool(report.get("passed", False))})
        final_video = data.get("finalVideo")
        if isinstance(final_video, dict) and final_video.get("candidateId") == candidate_id:
            final_video["qualityReport"] = report_path
        data["workflowStatus"] = "complete" if report.get("passed") else "step5_review_pending"
        if report.get("status") == "failed" and not bool(report.get("passed", False)):
            records = data.setdefault("skillOptimizations", [])
            if not any(item.get("candidateId") == candidate_id and item.get("qualityReportSha256") == report_digest for item in records):
                for item in records:
                    if item.get("candidateId") == candidate_id and item.get("status") in {"proposal_required", "proposed"}:
                        item["status"] = "stale"
                        item["staleAt"] = now()
                records.append({
                    "candidateId": candidate_id,
                    "targetSkill": TARGET_SKILL,
                    "qualityReport": report_path,
                    "qualityReportSha256": report_digest,
                    "status": "proposal_required",
                    "createdAt": now(),
                })
    return update(path, mutate)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--task-id", required=True)
    init.add_argument("--output-root", required=True)
    init.add_argument("--reuse", action="store_true")
    init.add_argument("--video-model", default="seedance-2-fast")
    init.add_argument("--duration-mode", choices=("source", "opening_10", "custom"), default="source")
    init.add_argument("--target-duration", type=int)
    init.add_argument("--custom-requirement", default="")
    board = sub.add_parser("add-storyboard")
    board.add_argument("--manifest", required=True)
    board.add_argument("--kind", choices=("original", "edited"), required=True)
    board.add_argument("--image", required=True)
    board.add_argument("--storyboard-id", type=int, required=True)
    board.add_argument("--anchors-file")
    board.add_argument("--quality-file")
    board.add_argument("--replacement-file")
    creator = sub.add_parser("add-creator")
    creator.add_argument("--manifest", required=True)
    creator.add_argument("--image", required=True)
    creator.add_argument("--creator-id", required=True)
    creator_map = sub.add_parser("set-creator-replacement-map")
    creator_map.add_argument("--manifest", required=True)
    creator_map.add_argument("--file", required=True)
    generation_boards = sub.add_parser("set-generation-storyboards")
    generation_boards.add_argument("--manifest", required=True)
    generation_boards.add_argument("--metadata-file", required=True)
    blueprint = sub.add_parser("set-blueprint")
    blueprint.add_argument("--manifest", required=True)
    blueprint.add_argument("--file", required=True)
    capabilities = sub.add_parser("set-provider-capabilities")
    capabilities.add_argument("--manifest", required=True)
    capabilities.add_argument("--file", required=True)
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
            anchors = json.loads(Path(args.anchors_file).read_text(encoding="utf-8")) if args.anchors_file else None
            quality = json.loads(Path(args.quality_file).read_text(encoding="utf-8")) if args.quality_file else None
            replacement = json.loads(Path(args.replacement_file).read_text(encoding="utf-8")) if args.replacement_file else None
            result = add_storyboard(args.manifest, args.kind, args.image, args.storyboard_id, anchors, quality, replacement)
        elif args.command == "add-creator":
            result = add_creator(args.manifest, args.image, args.creator_id)
        elif args.command == "set-creator-replacement-map":
            result = set_creator_replacement_map(args.manifest, args.file)
        elif args.command == "set-generation-storyboards":
            result = set_generation_storyboards(args.manifest, args.metadata_file)
        elif args.command == "set-blueprint":
            result = set_blueprint(args.manifest, args.file)
        elif args.command == "set-provider-capabilities":
            result = set_provider_capabilities(args.manifest, args.file)
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
