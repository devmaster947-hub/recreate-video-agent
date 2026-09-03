#!/usr/bin/env python3
"""Run only Step 5 from a completed recreate-video-system v4 manifest."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from scripts import concat_videos, generation_manifest, quality_review, reference_audit, reference_board, run_cli  # noqa: E402
from scripts.model_capabilities import capability  # noqa: E402
from scripts.prompt_preflight import validate_segments  # noqa: E402


def local_path(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("file") or value.get("filePath") or "")
    return ""


def product_files(data: dict[str, Any]) -> list[str]:
    product = data.get("product", {})
    if not isinstance(product, dict):
        return []
    values = product.get("productImages")
    if values is None:
        values = product.get("images", [])
    if not isinstance(values, list):
        raise ValueError("product.productImages/images 必须是数组。")
    return [path for item in values if (path := local_path(item))]


def creator_files(data: dict[str, Any], segment: dict[str, Any]) -> list[str]:
    selected = {str(value) for value in segment.get("creatorIds", [])}
    if not selected:
        return []
    creators = data.get("creators", [])
    return [
        str(item["file"])
        for item in creators
        if str(item.get("creatorId")) in selected
    ]


def storyboard_files(data: dict[str, Any], segment: dict[str, Any]) -> list[str]:
    selected = {int(value) for value in segment.get("storyboardIds", [])}
    storyboard_data = data.get("storyboards", {})
    boards = storyboard_data.get("generation", [])
    if not selected:
        raise ValueError(f"Segment {segment.get('segmentId')} 缺少 storyboardIds。")
    result = [str(item["file"]) for item in boards if int(item["storyboardId"]) in selected]
    if len(result) != len(selected):
        raise ValueError(f"Segment {segment.get('segmentId')} 引用了不存在的最终Segment Storyboard。")
    return result


def selected_attempt(entry: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    attempt = next((item for item in entry.get("attempts", []) if item.get("candidateId") == candidate_id), None)
    if attempt is not None:
        return attempt
    return entry if candidate_id == "candidate-01" and not entry.get("attempts") else {}


def select_segments(segments: list[dict[str, Any]], selected_ids: list[int] | None = None) -> list[dict[str, Any]]:
    requested = {int(value) for value in (selected_ids or [])}
    if not requested:
        return segments
    available = {int(item["segmentId"]) for item in segments}
    missing = sorted(requested - available)
    if missing:
        raise ValueError(f"请求的Segment不存在：{missing}")
    return [item for item in segments if int(item["segmentId"]) in requested]


def prepare_references(data: dict[str, Any], segment: dict[str, Any], root: Path, model: str) -> list[str]:
    boards = storyboard_files(data, segment)
    board_id = int(segment["storyboardIds"][0])
    board = next(item for item in data.get("storyboards", {}).get("generation", []) if int(item["storyboardId"]) == board_id)
    needs_product = reference_audit.product_presence(board) and not reference_audit.use_benchmark_product(data)
    products = product_files(data) if needs_product else []
    creators = creator_files(data, segment)
    maximum = int((capability(model) or {}).get("maxImages", 9))
    files = boards + products + creators
    if len(files) > maximum and len(products) > 1:
        board = reference_board.render([Path(value).expanduser().resolve() for value in products], root / "references" / "product-identity-board.jpg")
        files = boards + [str(board)] + creators
    if len(files) > maximum:
        raise ValueError(f"Segment {segment.get('segmentId')} 需要 {len(files)} 张参考图，超过模型上限 {maximum}；不得静默丢弃素材。")
    return files


def product_reference_required(data: dict[str, Any]) -> bool:
    product = data.get("product", {})
    if not isinstance(product, dict):
        return True
    mode = str(product.get("mode", "")).strip().lower()
    return not (product.get("useBenchmarkProduct") is True or mode in {"benchmark", "use_benchmark", "original"})


def upload_references(data: dict[str, Any], files: list[str]) -> tuple[list[str], bool]:
    media_map = data.setdefault("publicMedia", {})
    urls: list[str] = []
    changed = False
    for raw in files:
        path = str(Path(raw).expanduser().resolve())
        existing = media_map.get(path, {})
        if isinstance(existing, dict) and str(existing.get("url", "")).startswith(("http://", "https://")):
            urls.append(str(existing["url"]))
            continue
        media = run_cli.upload_file(path)
        media_map[path] = media
        urls.append(str(media["url"]))
        changed = True
    return urls, changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--video-provider", choices=("auto", "official_cli", "xiaoyunque_cli", "lingzhi_cli"), default="auto")
    parser.add_argument("--skip-concat", action="store_true")
    parser.add_argument("--new-candidate", action="store_true")
    parser.add_argument("--quality-retry-approved", action="store_true")
    parser.add_argument("--generation-approved", action="store_true")
    parser.add_argument("--segment-id", type=int, action="append", default=[])
    args = parser.parse_args()

    try:
        manifest_path = Path(args.manifest).expanduser().resolve()
        data = generation_manifest.load_manifest(manifest_path)
        prompts_file = Path(data["videoPrompts"]["file"]).expanduser().resolve()
        prompts = json.loads(prompts_file.read_text(encoding="utf-8"))["videoPrompts"]
        segments = prompts["segments"]
        config = data.get("userConfig", {})
        model = str(config.get("videoModel", "seedance-2-fast"))
        resolution = str(config.get("resolution", "720p"))
        target_duration = int(config.get("duration", sum(int(item["duration"]) for item in segments)))
        storyboard_data = data.get("storyboards", {})
        generation_boards = storyboard_data.get("generation", [])
        available_boards = generation_boards
        windows = {int(item["storyboardId"]): item for item in generation_boards}
        validate_segments(
            segments, model, resolution, target_duration=target_duration,
            available_storyboards={int(item["storyboardId"]) for item in available_boards},
            available_creators={str(item["creatorId"]) for item in data.get("creators", [])},
            storyboard_windows=windows,
        )
        selected_segments = select_segments(segments, args.segment_id)
        if len(selected_segments) != len(segments) and not args.skip_concat:
            raise ValueError("部分Segment生成必须传入 --skip-concat，禁止拼接缺段候选。")
        data["referenceAudit"] = reference_audit.audit(data, selected_segments)
        generation_manifest.save_manifest(manifest_path, data)
        if args.new_candidate and not args.quality_retry_approved:
            raise ValueError("创建新候选会消耗积分；必须先取得用户同意并传入 --quality-retry-approved。")
        candidate_id = generation_manifest.next_candidate_id(data) if args.new_candidate else str(data.get("activeCandidate", "candidate-01"))
        existing_videos = {
            int(item["segmentId"]): item for item in data.get("videos", [])
        }
        requires_submit = False
        for segment in selected_segments:
            segment_id = int(segment["segmentId"])
            previous = selected_attempt(existing_videos.get(segment_id, {}), candidate_id)
            previous_output = Path(str(previous.get("output_file", ""))).expanduser()
            reusable = previous.get("status") == "success" and previous_output.is_file() and previous_output.stat().st_size > 0
            resumable = bool(str(previous.get("taskId", "")).strip()) and previous.get("status") in {"submitted", "querying"}
            if not reusable and not resumable:
                requires_submit = True
                break
        if requires_submit and not args.generation_approved:
            raise ValueError("视频提示词必须先完整展示给用户并取得明确“确认生成”；确认后传入 --generation-approved。")

        channels = run_cli.video_provider_availability(model, availability=run_cli.detect_video_providers())
        provider = run_cli.resolve_video_provider(args.video_provider, availability=channels)
        root = manifest_path.parent
        products = product_files(data)
        if product_reference_required(data) and not products:
            raise ValueError("已选择新产品替换，但生成引用中没有产品图；请检查 product.productImages/images，禁止无产品身份参考提交。")
        plans: list[dict[str, Any]] = []
        audit_segments: list[dict[str, Any]] = []
        for segment in selected_segments:
            files = prepare_references(data, segment, root, model)
            for file in files:
                path = Path(file).expanduser().resolve()
                if not path.is_file() or path.stat().st_size <= 0:
                    raise ValueError(f"参考图不存在或为空：{path}")
            urls: list[str] = []
            if provider == "lingzhi_cli":
                urls, _ = upload_references(data, files)
            plans.append({"segment": segment, "files": files, "urls": urls})
            boards = storyboard_files(data, segment)
            creators = creator_files(data, segment)
            segment_products = [value for value in files if value in products]
            audit_segments.append({
                "segmentId": int(segment["segmentId"]),
                "storyboards": boards,
                "productImages": segment_products,
                "creatorImages": creators,
                "orderedFiles": files,
            })
        data["referenceAudit"] = {"passed": True, "provider": provider, "segments": audit_segments}
        generation_manifest.save_manifest(manifest_path, data)

        candidate_root = root / "candidates" / candidate_id
        manifest_lock = Lock()
        def worker(plan: dict[str, Any]) -> dict[str, Any]:
            segment = plan["segment"]
            segment_id = int(segment["segmentId"])
            previous_entry = existing_videos.get(segment_id, {})
            previous = selected_attempt(previous_entry, candidate_id)
            previous_output = Path(str(previous.get("output_file", ""))).expanduser()
            if previous.get("status") == "success" and previous_output.is_file() and previous_output.stat().st_size > 0:
                return {"ok": True, "reused": True, "segment": segment, "taskId": previous.get("taskId", ""), "output": str(previous_output.resolve()), "media": previous.get("media", {}), "credits": 0}
            previous_task = str(previous.get("taskId", "")).strip()
            previous_provider = str(previous.get("provider", "")).strip()
            if previous_task and previous.get("status") in {"submitted", "querying"}:
                if previous_provider and previous_provider != provider:
                    raise ValueError(f"Segment {segment_id} 已锁定 provider={previous_provider}，禁止切换。")
                with manifest_lock:
                    generation_manifest.set_video(manifest_path, segment_id, "querying", candidateId=candidate_id, taskId=previous_task, provider=provider)
                if provider == "official_cli":
                    raw = run_cli.poll_task(run_cli.fetch_official_video, previous_task, timeout=3600)
                    media = run_cli._media(raw, "video/mp4")
                    credits = run_cli._credits(raw)
                else:
                    media = run_cli.poll_video(previous_task)
                    credits = run_cli._credits(run_cli.fetch_video(previous_task))
                output = run_cli.download_media(media, candidate_root / f"segment-{segment_id}.mp4")
                return {"ok": True, "reused": False, "segment": segment, "taskId": previous_task, "output": str(output), "media": media, "credits": credits}
            submitted: list[str] = []
            def record_submit(task_id: str) -> None:
                submitted.append(task_id)
                with manifest_lock:
                    generation_manifest.set_video(manifest_path, segment_id, "submitted", candidateId=candidate_id, taskId=task_id, provider=provider, model=model, resolution=resolution, prompt=str(segment["prompt"]))
            result = run_cli.generate_video(
                model,
                str(segment["prompt"]),
                int(segment["duration"]),
                video_provider=provider,
                reference_images=plan["urls"],
                reference_files=plan["files"],
                resolution=resolution,
                aspect_ratio=str(config.get("aspectRatio", "9:16")),
                availability=channels,
                on_submit=record_submit,
            )
            if result.get("video") is None:
                return {"ok": False, "segment": segment, "taskId": submitted[-1] if submitted else "", "error": result.get("errorMessage", "任务失败")}
            output = run_cli.download_media(result["video"], candidate_root / f"segment-{int(segment['segmentId'])}.mp4")
            return {"ok": True, "segment": segment, "taskId": submitted[-1] if submitted else "", "output": str(output), "media": result["video"], "credits": result.get("credits", 0)}

        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max(1, len(plans))) as pool:
            futures = [pool.submit(worker, plan) for plan in plans]
            for future in as_completed(futures):
                results.append(future.result())
        results.sort(key=lambda item: int(item["segment"]["segmentId"]))
        for item in results:
            segment_id = int(item["segment"]["segmentId"])
            if item["ok"]:
                generation_manifest.set_video(manifest_path, segment_id, "success", candidateId=candidate_id, taskId=item["taskId"], provider=provider, output_file=item["output"], media=item["media"], credits=item["credits"], model=model, resolution=resolution)
            else:
                generation_manifest.set_video(manifest_path, segment_id, "failed", candidateId=candidate_id, taskId=item["taskId"], provider=provider, error=item["error"], model=model, resolution=resolution)
        failures = [item for item in results if not item["ok"]]
        if failures:
            print(json.dumps({"ok": False, "stage": "step5", "failures": failures}, ensure_ascii=False))
            return 2
        if not args.skip_concat:
            current = generation_manifest.load_manifest(manifest_path)
            ordered = concat_videos.ordered_segments(current)
            ffmpeg = str((Path.home() / ".local" / "bin" / "ffmpeg"))
            final = candidate_root / "final.mp4"
            concat_videos.render([path for _, path in ordered], final, ffmpeg)
            generation_manifest.command_set_final(argparse.Namespace(manifest=str(manifest_path), output_file=str(final), segment_id=[identifier for identifier, _ in ordered], candidate_id=candidate_id))
            benchmark_value = data.get("benchmarkVideo", {})
            benchmark_file = local_path(benchmark_value)
            if benchmark_file and Path(benchmark_file).expanduser().is_file():
                review_dir = candidate_root / "review"
                report = quality_review.build_review(
                    quality_review.resolve("ffmpeg"), quality_review.resolve_optional("ffprobe"),
                    Path(benchmark_file).expanduser().resolve(), final, review_dir,
                    target_duration=target_duration,
                )
                report_file = review_dir / "quality-report.json"
                report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                generation_manifest.set_quality_report(manifest_path, candidate_id, str(report_file))
        print(json.dumps({"ok": True, "provider": provider, "candidateId": candidate_id, "segments": results, "manifest": str(manifest_path)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError, run_cli.LzStudioError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
