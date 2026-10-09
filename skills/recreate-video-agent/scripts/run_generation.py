#!/usr/bin/env python3
"""Run optional local video generation from a completed recreate-video-agent manifest."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from scripts import concat_videos, generation_manifest, local_video_cli, quality_review, reference_audit, reference_board, service_privacy, server_video_analysis  # noqa: E402
from scripts.model_capabilities import capability  # noqa: E402


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
    current_policy = reference_audit.current_or_legacy_at_least(str(data.get("skillVersion", "")), 6, 5)
    if current_policy:
        reference_audit.validate_creator_references(data, selected)
    return [
        str(item["file"])
        for item in data.get("creators", [])
        if str(item.get("creatorId")) in selected
        and (
            not current_policy
            or str(item.get("sourceType", "")).strip().lower() in {"user_provided", "generated_multiview"}
        )
    ]


def storyboard_files(data: dict[str, Any], segment: dict[str, Any]) -> list[str]:
    selected = {int(value) for value in segment.get("storyboardIds", [])}
    boards = data.get("storyboards", {}).get("generation", [])
    if not selected:
        raise ValueError(f"Segment {segment.get('segmentId')} 缺少 storyboardIds。")
    result = [str(item["file"]) for item in boards if int(item["storyboardId"]) in selected]
    if len(result) != len(selected):
        raise ValueError(f"Segment {segment.get('segmentId')} 引用了不存在的最终 Segment Storyboard。")
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
        raise ValueError(f"请求的 Segment 不存在：{missing}")
    return [item for item in segments if int(item["segmentId"]) in requested]


def prepare_references(data: dict[str, Any], segment: dict[str, Any], root: Path, model: str) -> list[str]:
    boards = storyboard_files(data, segment)
    board_id = int(segment["storyboardIds"][0])
    board = next(
        item for item in data.get("storyboards", {}).get("generation", [])
        if int(item["storyboardId"]) == board_id
    )
    needs_product = reference_audit.product_presence(board, segment) and not reference_audit.use_benchmark_product(data)
    context = reference_audit.entity_bindings.segment_context(data, segment)
    products = context["productReferenceImages"] if context is not None else product_files(data) if needs_product else []
    creators = creator_files(data, segment)
    maximum = int((capability(model) or {}).get("maxImages", 9))
    files = boards + products + creators
    if len(files) > maximum and len(products) > 1:
        identity_board = reference_board.render(
            [Path(value).expanduser().resolve() for value in products],
            root / "references" / f"product-identity-board-{segment['segmentId']}.jpg",
        )
        files = boards + [str(identity_board)] + creators
    if len(files) > maximum:
        raise ValueError(
            f"Segment {segment.get('segmentId')} 需要 {len(files)} 张参考图，超过模型上限 {maximum}；不得静默丢弃素材。"
        )
    return files


def product_reference_required(data: dict[str, Any]) -> bool:
    product = data.get("product", {})
    if not isinstance(product, dict):
        return True
    mode = str(product.get("mode", "")).strip().lower()
    return not (product.get("useBenchmarkProduct") is True or mode in {"benchmark", "use_benchmark", "original"})


def mark_skipped(manifest_path: Path, data: dict[str, Any], model: str, channels: dict[str, bool]) -> None:
    data["videoGeneration"] = {
        "status": "skipped_no_local_cli",
        "generated": False,
        "model": model,
        "availability": channels,
    }
    generation_manifest.save_manifest(manifest_path, data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument(
        "--video-provider",
        choices=("auto", "dreamina_cli", "xiaoyunque_cli", "lululab_cli"),
        default="auto",
    )
    parser.add_argument("--skip-concat", action="store_true")
    parser.add_argument("--new-candidate", action="store_true")
    parser.add_argument("--quality-retry-approved", action="store_true")
    parser.add_argument("--full-quality-review", action="store_true", help="生成后执行完整视觉扫描和原片/成片对齐图；默认仅做快速技术质检。")
    parser.add_argument("--generation-approved", action="store_true")
    parser.add_argument("--segment-id", type=int, action="append", default=[])
    args = parser.parse_args()

    lock_handle = None
    try:
        manifest_path = Path(args.manifest).expanduser().resolve()
        lock_handle = server_video_analysis.acquire_run_lock(manifest_path, lock_path=manifest_path.parent / "analysis" / "video-generation.lock")
        data = generation_manifest.load_manifest(manifest_path)
        prompts_file = Path(data["videoPrompts"]["file"]).expanduser().resolve()
        prompts = json.loads(prompts_file.read_text(encoding="utf-8"))["videoPrompts"]
        segments = prompts["segments"]
        config = data.get("userConfig", {})
        model = str(config.get("videoModel", "seedance-2-fast"))
        resolution = str(config.get("resolution", "720p"))
        target_duration = int(config.get("duration", sum(int(item["duration"]) for item in segments)))
        generation_manifest.validate_prompt_record(prompts, data)
        selected_segments = select_segments(segments, args.segment_id)
        if len(selected_segments) != len(segments) and not args.skip_concat:
            raise ValueError("部分 Segment 生成必须传入 --skip-concat，禁止拼接缺段候选。")

        requested_provider = args.video_provider if args.video_provider != "auto" else "lululab_cli"
        channels = {"lululab_cli": local_video_cli.lululab_cli_available()} if requested_provider == "lululab_cli" else local_video_cli.detect_video_providers(model)
        if requested_provider != "auto" and not channels.get(requested_provider):
            mark_skipped(manifest_path, data, model, channels)
            raise ValueError(f"视频生成渠道不可用：{requested_provider}；已保留素材和任务记录。")
        provider = local_video_cli.resolve_video_provider(requested_provider, availability=channels)
        if provider is None:
            mark_skipped(manifest_path, data, model, channels)
            print(json.dumps({
                "ok": True,
                "generated": False,
                "status": "skipped_no_local_cli",
                "model": model,
                "availability": channels,
                "manifest": str(manifest_path),
            }, ensure_ascii=False))
            return 0

        initial_audit = reference_audit.audit(data, selected_segments)
        data["referenceAudit"] = initial_audit
        generation_manifest.save_manifest(manifest_path, data)
        if args.new_candidate and not args.quality_retry_approved:
            raise ValueError("创建新候选会再次提交视频任务；必须先取得用户同意并传入 --quality-retry-approved。")
        candidate_id = generation_manifest.next_candidate_id(data) if args.new_candidate else str(data.get("activeCandidate", "candidate-01"))
        existing_videos = {int(item["segmentId"]): item for item in data.get("videos", [])}

        requires_submit = False
        for segment in selected_segments:
            segment_id = int(segment["segmentId"])
            previous = selected_attempt(existing_videos.get(segment_id, {}), candidate_id)
            previous_output = Path(str(previous.get("output_file", ""))).expanduser()
            reusable = previous.get("status") == "success" and previous_output.is_file() and previous_output.stat().st_size > 0
            resumable = bool(str(previous.get("taskId", "")).strip()) and previous.get("status") != "terminal_failed"
            if not reusable and not resumable:
                requires_submit = True
                break
        if requires_submit and not args.generation_approved:
            raise ValueError("首次视频生成必须处于用户已“确认开始”的本轮授权内；授权后传入 --generation-approved。")

        root = manifest_path.parent
        products = product_files(data)
        if product_reference_required(data) and not products:
            raise ValueError("已选择新产品替换，但生成引用中没有产品图；请检查 product.productImages/images。")

        plans: list[dict[str, Any]] = []
        audit_segments: list[dict[str, Any]] = []
        for segment in selected_segments:
            files = prepare_references(data, segment, root, model)
            for file in files:
                path = Path(file).expanduser().resolve()
                if not path.is_file() or path.stat().st_size <= 0:
                    raise ValueError(f"参考图不存在或为空：{path}")
            plans.append({"segment": segment, "files": files})
            boards = storyboard_files(data, segment)
            creators = creator_files(data, segment)
            segment_products = [value for value in files if value in products]
            required_product_files = reference_audit.validate_product_references(data, next(
                item for item in data.get("storyboards", {}).get("generation", [])
                if int(item["storyboardId"]) == int(segment["storyboardIds"][0])
            ))
            if required_product_files and not set(required_product_files) <= {str(Path(value).expanduser().resolve()) for value in files}:
                raise ValueError(f"Segment {segment.get('segmentId')} 的最终视频提交参数缺少产品参考图。")
            audit_segments.append({
                "segmentId": int(segment["segmentId"]),
                "storyboards": boards,
                "registeredProductImages": required_product_files,
                "productImages": segment_products,
                "creatorImages": creators,
                "orderedFiles": files,
            })
        data["referenceAudit"] = {
            "passed": True,
            "provider": provider,
            "warnings": initial_audit.get("warnings", []),
            "segments": audit_segments,
        }
        data["videoGeneration"] = {
            "status": "ready",
            "generated": False,
            "provider": provider,
            "model": model,
            "availability": channels,
        }
        generation_manifest.save_manifest(manifest_path, data)

        candidate_root = root / "candidates" / candidate_id
        results: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        new_plans: list[dict[str, Any]] = []
        for plan in plans:
            segment = plan["segment"]
            segment_id = int(segment["segmentId"])
            previous = selected_attempt(existing_videos.get(segment_id, {}), candidate_id)
            previous_output = Path(str(previous.get("output_file", ""))).expanduser()
            if previous.get("status") == "success" and previous_output.is_file() and previous_output.stat().st_size > 0:
                results.append({
                    "ok": True, "reused": True, "segment": segment,
                    "taskId": previous.get("taskId", ""), "output": str(previous_output.resolve()),
                    "media": previous.get("media", {}),
                })
                continue
            previous_task = str(previous.get("taskId", "")).strip()
            previous_provider = str(previous.get("provider", "")).strip()
            if previous_task and previous.get("status") != "terminal_failed":
                if previous_provider and previous_provider != provider:
                    raise ValueError(f"Segment {segment_id} 已锁定 provider={previous_provider}，禁止切换。")
                pending.append({**plan, "taskId": previous_task})
                continue
            if previous.get("status") in {"submit_pending", "submit_outcome_unknown", "terminal_failed"}:
                raise ValueError(f"Segment {segment_id} 已有提交或失败记录；禁止自动重新提交，请核查原任务或明确授权新候选。")
            new_plans.append(plan)

        def submit(plan: dict[str, Any]) -> tuple[dict[str, Any], str]:
            segment = plan["segment"]
            task_id = local_video_cli.submit_video(
                provider,
                model,
                str(segment["prompt"]),
                int(segment["duration"]),
                reference_files=plan["files"],
                resolution=resolution,
                aspect_ratio=str(config.get("aspectRatio", "9:16")),
            )
            return plan, task_id

        submit_errors: list[str] = []
        if new_plans:
            for plan in new_plans:
                generation_manifest.set_video(manifest_path, int(plan["segment"]["segmentId"]), "submit_pending", candidateId=candidate_id, provider=provider, model=model)
            with ThreadPoolExecutor(max_workers=len(new_plans)) as pool:
                futures = {pool.submit(submit, plan): plan for plan in new_plans}
                for future in as_completed(futures):
                    try:
                        plan, task_id = future.result()
                    except service_privacy.AuthorizationUnavailableError:
                        raise
                    except (OSError, ValueError, local_video_cli.LocalVideoCliError, server_video_analysis.AnalysisError) as exc:
                        generation_manifest.set_video(manifest_path, int(futures[future]["segment"]["segmentId"]), "submit_outcome_unknown", candidateId=candidate_id, provider=provider, error=service_privacy.public_error(str(exc), "提交结果未知。"))
                        submit_errors.append(f"Segment {futures[future]['segment'].get('segmentId')} submit失败：{exc}")
                        continue
                    segment = plan["segment"]
                    segment_id = int(segment["segmentId"])
                    generation_manifest.set_video(
                        manifest_path, segment_id, "submitted", candidateId=candidate_id,
                        taskId=task_id, provider=provider, model=model, resolution=resolution,
                        prompt=str(segment["prompt"]),
                    )
                    pending.append({**plan, "taskId": task_id})
        if submit_errors:
            raise ValueError("；".join(submit_errors) + "。其他已返回的任务ID已保存；再次运行只恢复这些ID。")

        for plan in pending:
            generation_manifest.set_video(
                manifest_path, int(plan["segment"]["segmentId"]), "querying",
                candidateId=candidate_id, taskId=plan["taskId"], provider=provider,
            )

        def poll_and_download(plan: dict[str, Any]) -> dict[str, Any]:
            segment = plan["segment"]
            segment_id = int(segment["segmentId"])
            raw = local_video_cli.poll_task(
                lambda task_id: local_video_cli.fetch_video(provider, task_id),
                plan["taskId"], timeout=3600,
            )
            media_value = local_video_cli.media(raw, "video/mp4")
            output = local_video_cli.download_media(media_value, candidate_root / f"segment-{segment_id}.mp4")
            return {
                "ok": True, "reused": False, "segment": segment,
                "taskId": plan["taskId"], "output": str(output),
                "media": media_value,
            }

        if pending:
            with ThreadPoolExecutor(max_workers=len(pending)) as pool:
                future_map = {pool.submit(poll_and_download, plan): plan for plan in pending}
                for future in as_completed(future_map):
                    plan = future_map[future]
                    try:
                        results.append(future.result())
                    except service_privacy.AuthorizationUnavailableError:
                        raise
                    except (OSError, ValueError, local_video_cli.LocalVideoCliError, server_video_analysis.AnalysisError) as exc:
                        results.append({
                            "ok": False,
                            "reused": False,
                            "segment": plan["segment"],
                            "taskId": plan["taskId"],
                            "error": service_privacy.public_error(str(exc), "任务查询失败。"),
                            "terminalFailed": isinstance(exc, local_video_cli.TaskFailedError),
                        })

        results.sort(key=lambda item: int(item["segment"]["segmentId"]))
        for item in results:
            segment_id = int(item["segment"]["segmentId"])
            if item["ok"]:
                generation_manifest.set_video(
                    manifest_path, segment_id, "success", candidateId=candidate_id,
                    taskId=item["taskId"], provider=provider, output_file=item["output"],
                    media=item["media"], model=model, resolution=resolution,
                )
            else:
                generation_manifest.set_video(
                    manifest_path, segment_id, "terminal_failed" if item.get("terminalFailed") else "querying", candidateId=candidate_id,
                    taskId=item["taskId"], provider=provider, error=item["error"],
                    model=model, resolution=resolution,
                )
        failures = [item for item in results if not item["ok"]]
        if failures:
            current = generation_manifest.load_manifest(manifest_path)
            current["videoGeneration"] = {
                "status": "failed",
                "generated": False,
                "provider": provider,
                "model": model,
                "availability": channels,
            }
            generation_manifest.save_manifest(manifest_path, current)
            print(json.dumps({"ok": False, "stage": "step5", "failures": failures}, ensure_ascii=False))
            return 2

        report: dict[str, Any] | None = None
        if not args.skip_concat:
            current = generation_manifest.load_manifest(manifest_path)
            ordered = concat_videos.ordered_segments(current)
            final = candidate_root / "final.mp4"
            concat_videos.render([path for _, path in ordered], final, quality_review.resolve("ffmpeg"))
            generation_manifest.command_set_final(argparse.Namespace(
                manifest=str(manifest_path), output_file=str(final),
                segment_id=[identifier for identifier, _ in ordered], candidate_id=candidate_id,
            ))
            benchmark_file = local_path(data.get("benchmarkVideo", {}))
            review_dir = candidate_root / "review"
            report_file = review_dir / "quality-report.json"
            try:
                if not benchmark_file or not Path(benchmark_file).expanduser().is_file():
                    raise ValueError("原视频文件不可用，已跳过自动对齐分析。")
                report = quality_review.build_review(
                    quality_review.resolve("ffmpeg"), quality_review.resolve_optional("ffprobe"),
                    Path(benchmark_file).expanduser().resolve(), final, review_dir,
                    target_duration=target_duration,
                    profile="full" if args.full_quality_review else "fast",
                )
            except Exception as exc:  # Quality report must not hide a successfully generated video.
                report = {
                    "status": "report_error", "passed": False, "deliveryBlocked": False,
                    "requiresVisualReview": False, "mayAutoRegenerate": False, "error": str(exc),
                }
            review_dir.mkdir(parents=True, exist_ok=True)
            report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            generation_manifest.set_quality_report(manifest_path, candidate_id, str(report_file))

        current = generation_manifest.load_manifest(manifest_path)
        current["videoGeneration"] = {
            "status": "success",
            "generated": True,
            "provider": provider,
            "model": model,
            "availability": channels,
            "candidateId": candidate_id,
        }
        generation_manifest.save_manifest(manifest_path, current)
        print(json.dumps({
            "ok": True, "generated": True, "provider": provider,
            "candidateId": candidate_id, "segments": results,
            "qualityReport": report, "manifest": str(manifest_path),
        }, ensure_ascii=False))
        return 0
    except service_privacy.AuthorizationUnavailableError as exc:
        raise SystemExit(str(exc)) from None
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError, local_video_cli.LocalVideoCliError, server_video_analysis.AnalysisError) as exc:
        raise SystemExit(str(exc)) from None

    finally:
        if lock_handle is not None:
            server_video_analysis.release_run_lock(lock_handle)


if __name__ == "__main__":
    raise SystemExit(main())
