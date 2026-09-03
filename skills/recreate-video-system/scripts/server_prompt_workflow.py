#!/usr/bin/env python3
"""Upload locked visual context and call the server-side recreate prompt workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from scripts import generation_manifest, run_cli
    from scripts.creator_mapping import public_creator_replacement_map
    from scripts.model_capabilities import capability
except ModuleNotFoundError:
    import generation_manifest  # type: ignore[no-redef]
    import run_cli  # type: ignore[no-redef]
    from creator_mapping import public_creator_replacement_map  # type: ignore[no-redef]
    from model_capabilities import capability  # type: ignore[no-redef]


INPUT_FIELDS = (
    "benchmarkVideoUrl",
    "userConfig",
    "productBrief",
    "creatorBrief",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _regular_file(value: str | Path, role: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"{role}不存在或为空：{path}")
    return path


def _dry_media(path: Path, kind: str) -> dict[str, str]:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg" if suffix in {".jpg", ".jpeg"} else "video/mp4"
    return {
        "url": f"https://dry-run.invalid/{kind}/{digest}/{path.name}",
        "mimeType": mime,
    }


def _upload(
    path: Path,
    kind: str,
    *,
    dry_run: bool,
    uploader: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, str]:
    if dry_run:
        return _dry_media(path, kind)
    media = run_cli._resolve_cached_upload(
        path,
        kind=kind,
        benchmark=kind == "benchmark",
        uploader=uploader,
    )
    return {
        "url": str(media["url"]),
        "mimeType": str(media.get("mimeType") or ("video/mp4" if kind == "benchmark" else "image/png")),
    }


def _product_files(product: Mapping[str, Any]) -> list[Path]:
    raw = product.get("productImages") or product.get("images") or []
    result: list[Path] = []
    for item in raw:
        value = item.get("file") if isinstance(item, dict) else item
        if value:
            path = _regular_file(str(value), "产品参考图")
            if path not in result:
                result.append(path)
    return result


def _creator_files(creators: list[dict[str, Any]]) -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for item in creators:
        creator_id = str(item.get("creatorId", "")).strip()
        if not creator_id or creator_id in seen:
            raise ValueError("Creator必须具有唯一且非空的creatorId。")
        path = _regular_file(str(item.get("file", "")), f"Creator图 {creator_id}")
        seen.add(creator_id)
        result.append((creator_id, path))
    return result


def build_payload(
    manifest_path: str | Path,
    benchmark_path: str | Path,
    *,
    dry_run: bool = False,
    uploader: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    manifest = generation_manifest.load_manifest(manifest_path)
    benchmark = _regular_file(benchmark_path, "对标视频")
    boards = manifest.get("storyboards", {}).get("generation", [])
    if not isinstance(boards, list) or not boards:
        raise ValueError("服务端提示词任务前必须登记非空storyboards.generation。")

    product = manifest.get("product", {})
    use_benchmark_product = bool(product.get("useBenchmarkProduct", False))
    product_files = _product_files(product)
    if not use_benchmark_product and not product_files:
        raise ValueError("新产品模式必须至少有一张产品参考图。")

    benchmark_media = _upload(benchmark, "benchmark", dry_run=dry_run, uploader=uploader)
    visual_segments: list[dict[str, Any]] = []
    expected_segment = 1
    for item in sorted(boards, key=lambda value: int(value["segmentId"])):
        segment_id = int(item["segmentId"])
        storyboard_id = int(item["storyboardId"])
        if segment_id != expected_segment:
            raise ValueError("storyboards.generation的segmentId必须从1连续递增。")
        expected_segment += 1
        board = _regular_file(str(item.get("file", "")), f"Segment {segment_id}最终Storyboard")
        anchors = item.get("anchors")
        if not isinstance(anchors, list) or len(anchors) != 16:
            raise ValueError(f"Segment {segment_id}必须包含16个anchors。")
        global_start = float(item["globalStart"])
        global_end = float(item["globalEnd"])
        if global_end <= global_start:
            raise ValueError(f"Segment {segment_id}时间窗无效。")
        product_present = any(bool(anchor.get("productPresent")) for anchor in anchors if isinstance(anchor, dict))
        if not use_benchmark_product and product_present and not bool(item.get("replacementVerified", False)):
            raise ValueError(f"Segment {segment_id}含产品画面但产品替换尚未通过验证。")
        media = _upload(board, "storyboard", dry_run=dry_run, uploader=uploader)
        visual_segments.append({
            "segmentId": segment_id,
            "storyboardId": storyboard_id,
            "globalStart": global_start,
            "globalEnd": global_end,
            "duration": global_end - global_start,
            "storyboardUrl": media["url"],
            "storyboardMimeType": media["mimeType"],
            "replacementVerified": bool(item.get("replacementVerified", False)),
            "anchors": anchors,
        })

    product_urls = [
        _upload(path, "product", dry_run=dry_run, uploader=uploader)
        for path in product_files
    ]
    creator_urls = []
    for creator_id, path in _creator_files(list(manifest.get("creators", []))):
        media = _upload(path, "creator", dry_run=dry_run, uploader=uploader)
        creator_urls.append({"creatorId": creator_id, **media})

    config = dict(manifest.get("userConfig", {}))
    model = str(config.get("videoModel", "seedance-2-fast"))
    config["modelCapabilities"] = capability(model)
    config["visualContext"] = {"segments": visual_segments}

    product_brief = {
        "useBenchmarkProduct": use_benchmark_product,
        "productAnalysis": product.get("productAnalysis", {}),
        "productImageUrls": product_urls,
    }
    creator_brief = {
        "creators": creator_urls,
        "replacementMap": public_creator_replacement_map(list(manifest.get("creatorReplacementMap", []))),
        "mappingRules": [
            "只替换replacementMap中action=replace的原片人物。",
            "action=keep的人物必须保持最终Storyboard中的原身份。",
            "一个targetCreatorId只能对应一个sourceCreatorId，禁止复制同一达人身份补齐多人。",
        ],
    }
    input_value = {
        "benchmarkVideoUrl": benchmark_media["url"],
        "userConfig": config,
        "productBrief": product_brief,
        "creatorBrief": creator_brief,
    }
    if tuple(input_value) != INPUT_FIELDS:
        raise AssertionError("CLI input二级字段发生意外变化。")
    return {"input": input_value}


def cli_submit_arguments(payload: Mapping[str, Any]) -> list[str]:
    value = payload["input"]
    return [
        "recreate-video-prompt",
        "submit",
        "--benchmark-video-url",
        str(value["benchmarkVideoUrl"]),
        "--user-config",
        _json(value["userConfig"]),
        "--product-brief",
        _json(value["productBrief"]),
        "--creator-brief",
        _json(value["creatorBrief"]),
    ]


def _find_video_prompts(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    queue = [value]
    visited: set[int] = set()
    failure = ""
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            identity = id(current)
            if identity in visited:
                continue
            visited.add(identity)
            if current.get("success") is False:
                failure = str(current.get("errorMessage") or current.get("message") or failure)
            prompts = current.get("videoPrompts")
            if isinstance(prompts, dict) and isinstance(prompts.get("segments"), list):
                return prompts, current
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    raise run_cli.LzStudioError(failure or "服务端结果缺少videoPrompts.segments。")


def _record_task(manifest_path: str | Path, task_id: str, status: str) -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["serverPromptTask"] = {"taskId": task_id, "status": status}
    generation_manifest.update(manifest_path, mutate)


def execute(
    manifest_path: str | Path,
    benchmark_path: str | Path,
    *,
    resume_task_id: str = "",
    dry_run: bool = False,
    poll_interval: float = 10.0,
    poll_timeout: float = 3600.0,
) -> dict[str, Any]:
    payload = build_payload(manifest_path, benchmark_path, dry_run=dry_run)
    if dry_run:
        return payload

    if resume_task_id:
        task_id = resume_task_id.strip()
    else:
        submitted = run_cli.run_cli(cli_submit_arguments(payload), timeout=600)
        task_id = run_cli._task_id(submitted)
        _record_task(manifest_path, task_id, "submitted")

    result = run_cli.poll_task(
        lambda identifier: run_cli.run_cli(
            ["recreate-video-prompt", "fetch", "--id", identifier],
            timeout=300,
        ),
        task_id,
        interval=poll_interval,
        timeout=poll_timeout,
    )
    prompts, envelope = _find_video_prompts(result)
    output_path = Path(manifest_path).expanduser().resolve().parent / "prompts" / "server-video-prompts.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_json({"videoPrompts": prompts}) + "\n", encoding="utf-8")
    generation_manifest.set_prompts(manifest_path, str(output_path))
    _record_task(manifest_path, task_id, "succeeded")
    return {
        "success": True,
        "taskId": task_id,
        "promptFile": str(output_path),
        "videoPrompts": prompts,
        "credits": envelope.get("credits", result.get("credits", 0) if isinstance(result, dict) else 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--resume-task-id", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--poll-timeout", type=float, default=3600.0)
    args = parser.parse_args()
    try:
        result = execute(
            args.manifest,
            args.benchmark,
            resume_task_id=args.resume_task_id,
            dry_run=args.dry_run,
            poll_interval=args.poll_interval,
            poll_timeout=args.poll_timeout,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, run_cli.LzStudioError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
