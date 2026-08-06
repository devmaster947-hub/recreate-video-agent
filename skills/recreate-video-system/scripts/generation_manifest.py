#!/usr/bin/env python3
"""Create and update a recreation generation manifest."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


DECISION_STAGES = ("creator_images", "storyboards", "videos")
DECISION_VALUES = ("yes", "no", "skipped")
IMAGE_BACKENDS = ("lzstudio-image",)
SEGMENT_STATES = ("planned", "submitted", "querying", "success", "failed", "skipped")
MIN_PUBLIC_URL_REMAINING_SECONDS = 600
PROMPT_TASK_STATES = ("submitted", "running", "succeeded", "failed")
PROMPT_OVERRIDE_TARGETS = ("creator-images", "storyboards", "videos")
PROMPT_OVERRIDE_MODES = ("append", "prepend", "replace")


def prompt_task_defaults() -> dict:
    return {
        "submit_id": "",
        "status": "",
        "result_file": "",
        "credits_consumed": None,
        "remaining_credits": None,
        "balance_error": "",
        "last_fetch_at": "",
        "last_error": "",
    }


def prompt_override_defaults() -> dict:
    return {target: {} for target in PROMPT_OVERRIDE_TARGETS}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema_version")
    data.setdefault("public_media", {})
    prompt_task = data.setdefault("prompt_task", {})
    for key, value in prompt_task_defaults().items():
        prompt_task.setdefault(key, value)
    overrides = data.setdefault("prompt_overrides", {})
    for target in PROMPT_OVERRIDE_TARGETS:
        overrides.setdefault(target, {})
    return data


def save_manifest(path: Path, data: dict) -> None:
    data["updated_at"] = now()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def require_local_file(value: str, role: str) -> str:
    if value.startswith(("http://", "https://")):
        raise ValueError(f"{role}必须是本地文件。")
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"{role}不存在或为空：{path}")
    return str(path)


def is_http_url(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def parse_expired_at(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("expiredAt 必须是 ISO 8601 字符串或空值。")
    normalized = value.strip()
    normalized = re.sub(
        r"(\.\d{6})\d+(?=(?:Z|[+-]\d\d:\d\d)$)",
        r"\1",
        normalized,
    )
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError("expiredAt 必须是有效的 ISO 8601 时间。") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def public_media_is_usable(
    media: object,
    *,
    at: datetime | None = None,
    min_remaining_seconds: int = MIN_PUBLIC_URL_REMAINING_SECONDS,
) -> bool:
    if not isinstance(media, dict) or not is_http_url(media.get("url")):
        return False
    try:
        expires = parse_expired_at(media.get("expiredAt"))
    except ValueError:
        return False
    if expires is None:
        return True
    current = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return expires - current >= timedelta(seconds=min_remaining_seconds)


def command_init(args: argparse.Namespace) -> Path:
    root = Path(args.output_root).expanduser().resolve() / args.task_id
    for directory in ("creators", "storyboards", "videos", "prompts"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    manifest = root / "manifest.json"
    if manifest.exists() and not args.reuse:
        raise ValueError(f"manifest 已存在：{manifest}；使用 --reuse 读取现有任务。")
    if not manifest.exists():
        created = now()
        save_manifest(
            manifest,
            {
                "schema_version": 1,
                "task_id": args.task_id,
                "model": args.model,
                "created_at": created,
                "updated_at": created,
                "decisions": {stage: None for stage in DECISION_STAGES},
                "image_backend": None,
                "assets": {
                    "product_images": [],
                    "creators": {},
                    "storyboards": {},
                },
                "public_media": {},
                "prompt_task": prompt_task_defaults(),
                "prompt_overrides": prompt_override_defaults(),
                "segments": {},
                "final_video": None,
            },
        )
    return manifest


def update_prompt_task(
    manifest: str | Path,
    *,
    submit_id: str | None = None,
    status: str | None = None,
    result_file: str | Path | None = None,
    credits_consumed: int | float | None = None,
    remaining_credits: int | float | None = None,
    balance_error: str | None = None,
    last_fetch_at: str | None = None,
    last_error: str | None = None,
) -> Path:
    path = Path(manifest).expanduser().resolve()
    data = load_manifest(path)
    prompt_task = data["prompt_task"]
    previous_id = str(prompt_task.get("submit_id", "")).strip()
    requested_id = str(submit_id).strip() if submit_id is not None else ""
    if previous_id and requested_id and requested_id != previous_id:
        raise ValueError(
            f"拆解任务已绑定 submit_id={previous_id}，禁止替换为新任务。"
        )
    if status is not None and status not in PROMPT_TASK_STATES:
        raise ValueError(f"不支持的拆解任务状态：{status}")
    if prompt_task.get("status") == "succeeded" and status not in {None, "succeeded"}:
        raise ValueError("拆解任务已成功，禁止回退状态。")
    if status in {"submitted", "running", "succeeded", "failed"} and not (
        requested_id or previous_id
    ):
        raise ValueError(f"状态 {status} 必须绑定 submit_id。")
    if result_file is not None:
        result_path = Path(result_file).expanduser().resolve()
        if not result_path.is_file() or result_path.stat().st_size <= 0:
            raise ValueError(f"拆解结果文件不存在或为空：{result_path}")
        prompt_task["result_file"] = str(result_path)
    if requested_id:
        prompt_task["submit_id"] = requested_id
    if status is not None:
        prompt_task["status"] = status
    if credits_consumed is not None:
        prompt_task["credits_consumed"] = credits_consumed
    if remaining_credits is not None:
        prompt_task["remaining_credits"] = remaining_credits
    if balance_error is not None:
        prompt_task["balance_error"] = balance_error
    if last_fetch_at is not None:
        prompt_task["last_fetch_at"] = last_fetch_at
    if last_error is not None:
        prompt_task["last_error"] = last_error
    save_manifest(path, data)
    return path


def command_decision(args: argparse.Namespace) -> Path:
    path = Path(args.manifest).expanduser().resolve()
    data = load_manifest(path)
    data["decisions"][args.stage] = args.value
    save_manifest(path, data)
    return path


def command_image_backend(args: argparse.Namespace) -> Path:
    path = Path(args.manifest).expanduser().resolve()
    data = load_manifest(path)
    data["image_backend"] = args.backend
    save_manifest(path, data)
    return path


def command_add_product(args: argparse.Namespace) -> Path:
    path = Path(args.manifest).expanduser().resolve()
    data = load_manifest(path)
    image = require_local_file(args.image, "产品图")
    products = data["assets"]["product_images"]
    if image not in products:
        products.append(image)
    save_manifest(path, data)
    return path


def command_add_creator(args: argparse.Namespace) -> Path:
    path = Path(args.manifest).expanduser().resolve()
    data = load_manifest(path)
    image = require_local_file(args.image, "达人图")
    data["assets"]["creators"][args.creator_id] = {
        "image": image,
        "appears_in_segments": list(dict.fromkeys(args.segment)),
    }
    save_manifest(path, data)
    return path


def command_add_storyboard(args: argparse.Namespace) -> Path:
    path = Path(args.manifest).expanduser().resolve()
    data = load_manifest(path)
    image = require_local_file(args.image, "分镜图")
    data["assets"]["storyboards"][str(args.segment_id)] = {
        "image": image,
        "grid": args.grid,
    }
    save_manifest(path, data)
    return path


def command_set_public_media(args: argparse.Namespace) -> Path:
    path = Path(args.manifest).expanduser().resolve()
    data = load_manifest(path)
    local_file = require_local_file(args.file, "公网媒体对应文件")
    if not is_http_url(args.url):
        raise ValueError("公网媒体 URL 必须是有效的 HTTP(S) URL。")
    if args.expired_at:
        parse_expired_at(args.expired_at)
    data["public_media"][local_file] = {
        "url": args.url.strip(),
        "mimeType": args.mime_type or "",
        "expiredAt": args.expired_at or "",
    }
    save_manifest(path, data)
    return path


def apply_prompt_override(
    data: dict,
    target: str,
    identifier: str | int,
    prompt: str,
) -> str:
    if target not in PROMPT_OVERRIDE_TARGETS:
        raise ValueError(f"不支持的 Prompt 修改目标：{target}")
    entry = data.get("prompt_overrides", {}).get(target, {}).get(str(identifier))
    if not entry:
        return prompt
    mode = entry.get("mode")
    text = entry.get("text")
    if mode not in PROMPT_OVERRIDE_MODES or not isinstance(text, str) or not text:
        raise ValueError(f"{target} {identifier} 的 Prompt 修改记录无效。")
    if mode == "replace":
        return text
    if mode == "prepend":
        return f"{text}\n{prompt}"
    return f"{prompt}\n{text}"


def command_set_prompt_override(args: argparse.Namespace) -> Path:
    path = Path(args.manifest).expanduser().resolve()
    data = load_manifest(path)
    text_path = Path(args.text_file).expanduser().resolve()
    if not text_path.is_file() or text_path.stat().st_size <= 0:
        raise ValueError(f"Prompt 修改文件不存在或为空：{text_path}")
    text = text_path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("Prompt 修改内容不得为空。")
    task_kind = {
        "creator-images": "creators",
        "storyboards": "storyboards",
        "videos": "videos",
    }[args.target]
    for identifier in args.id:
        key = str(identifier)
        task = data.get("generation_tasks", {}).get(task_kind, {}).get(key, {})
        segment = data.get("segments", {}).get(key, {}) if args.target == "videos" else {}
        if task.get("submit_id") or task.get("attempts") or task.get("status") in {"submitted", "success"}:
            raise ValueError(f"{args.target} {key} 已经提交，禁止中途修改 Prompt。")
        if segment.get("submit_id") or segment.get("status") in {"submitted", "querying", "success"}:
            raise ValueError(f"videos {key} 已经提交，禁止中途修改 Prompt。")
    updated_at = now()
    target_overrides = data["prompt_overrides"][args.target]
    for identifier in args.id:
        target_overrides[str(identifier)] = {
            "mode": args.mode,
            "text": text,
            "user_explicit": True,
            "updated_at": updated_at,
        }
    save_manifest(path, data)
    return path


def command_set_segment(args: argparse.Namespace) -> Path:
    path = Path(args.manifest).expanduser().resolve()
    data = load_manifest(path)
    key = str(args.segment_id)
    previous = data["segments"].get(key, {})
    previous_submit_id = previous.get("submit_id")
    if previous_submit_id and args.submit_id and args.submit_id != previous_submit_id:
        raise ValueError(
            f"Segment {args.segment_id} 已绑定 submit_id={previous_submit_id}，禁止替换为新任务。"
        )
    if previous.get("status") == "success" and args.status != "success":
        raise ValueError(f"Segment {args.segment_id} 已成功，禁止回退任务状态。")
    if args.status in {"submitted", "querying", "success"} and not (
        args.submit_id or previous_submit_id
    ):
        raise ValueError(f"状态 {args.status} 必须绑定 submit_id。")
    if args.status == "success" and not (args.output_file or previous.get("output_file")):
        raise ValueError("success 状态必须记录已下载的本地视频文件。")
    entry = {
        "title": args.title if args.title is not None else previous.get("title"),
        "duration": args.duration if args.duration is not None else previous.get("duration"),
        "status": args.status,
        "provider": args.provider if args.provider is not None else previous.get("provider"),
        "submit_id": args.submit_id if args.submit_id is not None else previous.get("submit_id"),
        "references": args.reference if args.reference else previous.get("references", []),
        "output_file": (
            require_local_file(args.output_file, "完成视频")
            if args.output_file
            else previous.get("output_file")
        ),
    }
    data["segments"][key] = entry
    save_manifest(path, data)
    return path


def select_segment_references(
    data: dict,
    segment_id: int,
    *,
    at: datetime | None = None,
) -> dict:
    products = list(data["assets"].get("product_images", []))
    creators: list[str] = []
    for creator in data["assets"].get("creators", {}).values():
        if segment_id in creator.get("appears_in_segments", []):
            creators.append(creator["image"])
    storyboard_entry = data["assets"].get("storyboards", {}).get(str(segment_id))
    storyboard = storyboard_entry.get("image") if storyboard_entry else None
    ordered = products + creators + ([storyboard] if storyboard else [])
    media_by_file = data.get("public_media", {})
    ordered_assets: list[dict] = []
    reference_urls: list[str] = []
    upload_required: list[str] = []
    for local_file in ordered:
        media = media_by_file.get(local_file, {})
        usable = public_media_is_usable(media, at=at)
        ordered_assets.append(
            {
                "file": local_file,
                "url": media.get("url", "") if isinstance(media, dict) else "",
                "mimeType": media.get("mimeType", "") if isinstance(media, dict) else "",
                "expiredAt": media.get("expiredAt", "") if isinstance(media, dict) else "",
                "usableUrl": usable,
            }
        )
        if usable:
            reference_urls.append(media["url"].strip())
        else:
            upload_required.append(local_file)
    return {
        "segment_id": segment_id,
        "product_images": products,
        "creator_images": creators,
        "storyboard_image": storyboard,
        "ordered_images": ordered,
        "ordered_assets": ordered_assets,
        "reference_urls": reference_urls,
        "upload_required": upload_required,
    }


def command_references(args: argparse.Namespace) -> None:
    path = Path(args.manifest).expanduser().resolve()
    data = load_manifest(path)
    print(json.dumps(select_segment_references(data, args.segment_id), ensure_ascii=False, indent=2))


def command_set_final(args: argparse.Namespace) -> Path:
    path = Path(args.manifest).expanduser().resolve()
    data = load_manifest(path)
    ordered_ids = [str(value) for value in args.segment_id]
    if not ordered_ids:
        raise ValueError("最终视频必须包含至少一个 Segment。")
    if len(set(ordered_ids)) != len(ordered_ids):
        raise ValueError("最终视频的 Segment 顺序不得重复。")
    for segment_id in ordered_ids:
        segment = data.get("segments", {}).get(segment_id)
        if not segment or segment.get("status") != "success":
            raise ValueError(f"Segment {segment_id} 尚未成功，不得记录完整视频。")
    output = require_local_file(args.output_file, "最终视频")
    data["final_video"] = {
        "output_file": output,
        "segment_order": [int(value) for value in ordered_ids],
        "created_at": now(),
    }
    save_manifest(path, data)
    return path


def command_clone_video_attempt(args: argparse.Namespace) -> Path:
    source_path = Path(args.source_manifest).expanduser().resolve()
    source = load_manifest(source_path)
    result_file = Path(source["prompt_task"].get("result_file", "")).expanduser().resolve()
    if not result_file.is_file() or result_file.stat().st_size <= 0:
        raise ValueError(f"源拆解结果不存在或为空：{result_file}")
    root = Path(args.output_root).expanduser().resolve() / args.task_id
    manifest = root / "manifest.json"
    if manifest.exists():
        raise ValueError(f"新视频尝试 manifest 已存在：{manifest}")
    for directory in ("creators", "storyboards", "videos", "prompts"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    created = now()
    prompt_task = deepcopy(source["prompt_task"])
    save_manifest(
        manifest,
        {
            "schema_version": 1,
            "task_id": args.task_id,
            "model": args.model,
            "created_at": created,
            "updated_at": created,
            "source_task_id": source.get("task_id", ""),
            "source_manifest": str(source_path),
            "decisions": {"creator_images": "skipped", "storyboards": "skipped", "videos": "yes"},
            "image_backend": source.get("image_backend"),
            "assets": deepcopy(source.get("assets", {"product_images": [], "creators": {}, "storyboards": {}})),
            "public_media": deepcopy(source.get("public_media", {})),
            "prompt_task": prompt_task,
            "prompt_overrides": deepcopy(source.get("prompt_overrides", prompt_override_defaults())),
            "segments": {},
            "final_video": None,
            "workflow_status": "videos_pending",
            "generation_tasks": {},
        },
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--task-id", required=True)
    init_parser.add_argument("--model", required=True)
    init_parser.add_argument("--output-root", default="output/recreate-video")
    init_parser.add_argument("--reuse", action="store_true")
    init_parser.set_defaults(handler=command_init)

    decision = subparsers.add_parser("decision")
    decision.add_argument("--manifest", required=True)
    decision.add_argument("--stage", choices=DECISION_STAGES, required=True)
    decision.add_argument("--value", choices=DECISION_VALUES, required=True)
    decision.set_defaults(handler=command_decision)

    image_backend = subparsers.add_parser("image-backend")
    image_backend.add_argument("--manifest", required=True)
    image_backend.add_argument("--backend", choices=IMAGE_BACKENDS, required=True)
    image_backend.set_defaults(handler=command_image_backend)

    product = subparsers.add_parser("add-product")
    product.add_argument("--manifest", required=True)
    product.add_argument("--image", required=True)
    product.set_defaults(handler=command_add_product)

    creator = subparsers.add_parser("add-creator")
    creator.add_argument("--manifest", required=True)
    creator.add_argument("--creator-id", required=True)
    creator.add_argument("--image", required=True)
    creator.add_argument("--segment", type=int, action="append", default=[])
    creator.set_defaults(handler=command_add_creator)

    storyboard = subparsers.add_parser("add-storyboard")
    storyboard.add_argument("--manifest", required=True)
    storyboard.add_argument("--segment-id", type=int, required=True)
    storyboard.add_argument("--image", required=True)
    storyboard.add_argument("--grid", required=True)
    storyboard.set_defaults(handler=command_add_storyboard)

    public_media = subparsers.add_parser("set-public-media")
    public_media.add_argument("--manifest", required=True)
    public_media.add_argument("--file", required=True)
    public_media.add_argument("--url", required=True)
    public_media.add_argument("--mime-type", default="")
    public_media.add_argument("--expired-at", default="")
    public_media.set_defaults(handler=command_set_public_media)

    prompt_override = subparsers.add_parser("set-prompt-override")
    prompt_override.add_argument("--manifest", required=True)
    prompt_override.add_argument("--target", choices=PROMPT_OVERRIDE_TARGETS, required=True)
    prompt_override.add_argument("--id", action="append", required=True)
    prompt_override.add_argument("--mode", choices=PROMPT_OVERRIDE_MODES, required=True)
    prompt_override.add_argument("--text-file", required=True)
    prompt_override.set_defaults(handler=command_set_prompt_override)

    segment = subparsers.add_parser("set-segment")
    segment.add_argument("--manifest", required=True)
    segment.add_argument("--segment-id", type=int, required=True)
    segment.add_argument("--title")
    segment.add_argument("--duration", type=int)
    segment.add_argument("--status", choices=SEGMENT_STATES, required=True)
    segment.add_argument("--provider")
    segment.add_argument("--submit-id")
    segment.add_argument("--reference", action="append", default=[])
    segment.add_argument("--output-file")
    segment.set_defaults(handler=command_set_segment)

    references = subparsers.add_parser("references")
    references.add_argument("--manifest", required=True)
    references.add_argument("--segment-id", type=int, required=True)
    references.set_defaults(handler=command_references)

    final = subparsers.add_parser("set-final")
    final.add_argument("--manifest", required=True)
    final.add_argument("--output-file", required=True)
    final.add_argument("--segment-id", type=int, action="append", required=True)
    final.set_defaults(handler=command_set_final)

    clone = subparsers.add_parser("clone-video-attempt")
    clone.add_argument("--source-manifest", required=True)
    clone.add_argument("--task-id", required=True)
    clone.add_argument("--model", required=True)
    clone.add_argument("--output-root", default="output/recreate-video")
    clone.set_defaults(handler=command_clone_video_attempt)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        path = args.handler(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if path is not None:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
