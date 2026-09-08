#!/usr/bin/env python3
"""Generate all replacement Storyboards through Lingzhi CLI in parallel."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from scripts import generation_manifest, run_cli, storyboard_cells, storyboard_regions  # noqa: E402


def _path(value: Any, base: Path) -> Path:
    path = Path(str(value or "")).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def _file(value: Any, role: str, base: Path) -> Path:
    path = _path(value, base)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"{role}不存在或为空：{path}")
    return path


def load_jobs(path: str | Path, manifest: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    base = source.parent
    raw_jobs = value.get("jobs") if isinstance(value, dict) else None
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise ValueError("图片任务文件必须包含非空 jobs 数组。")
    originals = {
        int(item["storyboardId"]): item
        for item in manifest.get("storyboards", {}).get("original", [])
    }
    jobs: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in raw_jobs:
        if not isinstance(raw, dict):
            raise ValueError("每个图片任务必须是对象。")
        segment_id = int(raw["segmentId"])
        board_id = int(raw.get("storyboardId", segment_id))
        if segment_id in seen:
            raise ValueError(f"图片任务 Segment 重复：{segment_id}")
        seen.add(segment_id)
        original_entry = originals.get(board_id)
        original = _file(raw.get("original") or (original_entry or {}).get("file"), "原Storyboard", base)
        replacement_map = _file(raw.get("replacementMap"), "Replacement Map", base)
        normalized_map = storyboard_cells.normalize_replacement_map(
            json.loads(replacement_map.read_text(encoding="utf-8"))
        )
        if int(normalized_map.get("segmentId", -1)) != segment_id:
            raise ValueError(f"Segment {segment_id} 与 Replacement Map 不一致。")
        region_mode = normalized_map.get("mergeMode") == "object-regions-v1"
        canvas = None
        canvas_file = original
        if region_mode:
            if not original_entry or original.resolve() != Path(original_entry["file"]).resolve():
                raise ValueError("必须使用manifest登记的原始Storyboard。")
            storyboard_regions.validate_anchors(normalized_map, original_entry.get("anchors"))
            ffmpeg = storyboard_cells.executable("ffmpeg", None)
            width, height = storyboard_cells.dimensions(None, original, ffmpeg)
            storyboard_regions.board_mask(normalized_map, width, height)
            canvas_file = root / "storyboards" / "edit-inputs" / f"segment-{segment_id:02d}-canvas.png"
            canvas = storyboard_regions.prepare_canvas(ffmpeg, original, canvas_file, width, height)
            if raw.get("aspectRatio") and raw["aspectRatio"] != canvas["aspectRatio"]:
                raise ValueError("图片比例必须匹配加边画布，不能使用视频比例覆盖。")
        prompt = str(raw.get("prompt", "")).strip()
        if not prompt:
            raise ValueError(f"Segment {segment_id} 图片 Prompt 不能为空。")
        if region_mode:
            prompt += (
                "\n画布约束：Image 1 是原始3×3故事板加外边后的完整画布，"
                f"尺寸{canvas['width']}×{canvas['height']}，比例{canvas['aspectRatio']}。"
                "保留整个画布、外侧灰色留边、所有格线和时间条的位置；不裁切、不缩放单格、不重排。"
                "仅在指定对象的原位置替换身份；保持每格的可见范围、数量、动作阶段和遮挡接触关系。"
            )
        references = [canvas_file]
        for item in raw.get("referenceFiles", []):
            candidate = _file(item, f"Segment {segment_id} 参考图", base)
            if candidate not in references:
                references.append(candidate)
        output = _path(raw["output"], base) if raw.get("output") else (root / "storyboards" / "edited" / f"segment-{segment_id:02d}-storyboard-3x3.png").resolve()
        raw_output = output.with_name(output.stem + "-raw.png")
        jobs.append({
            "segmentId": segment_id,
            "storyboardId": board_id,
            "original": original,
            "originalEntry": original_entry or {},
            "replacementMap": replacement_map,
            "normalizedMap": normalized_map,
            "prompt": prompt,
            "references": references,
            "output": output,
            "rawOutput": raw_output,
            "aspectRatio": canvas["aspectRatio"] if canvas else str(raw.get("aspectRatio") or manifest.get("userConfig", {}).get("aspectRatio", "9:16")),
            "resolution": str(raw.get("resolution", "2K" if region_mode else "1K")),
            "regionMode": region_mode,
        })
    return sorted(jobs, key=lambda item: item["segmentId"])


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _upload(job: dict[str, Any]) -> list[str]:
    return [
        str(run_cli.resolve_cached_upload(path, kind="storyboard-edit-reference")["url"])
        for path in job["references"]
    ]


def _job_fingerprint(job: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(job["prompt"].encode("utf-8"))
    digest.update(job["aspectRatio"].encode("utf-8"))
    digest.update(job["resolution"].encode("utf-8"))
    for path in [job["replacementMap"], *job["references"]]:
        digest.update(str(path).encode("utf-8"))
        digest.update(generation_manifest.file_sha256(path).encode("ascii"))
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--jobs", required=True)
    parser.add_argument("--image-generation-approved", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=8.0)
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=360.0,
        help="Maximum parallel polling time in seconds. Timeouts keep task IDs resumable.",
    )
    parser.add_argument(
        "--submit-only",
        action="store_true",
        help="Submit all new jobs, persist task IDs, then return without polling.",
    )
    parser.add_argument("--max-workers", type=int, default=0, help="0 means one worker per Segment.")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    args = parser.parse_args()

    try:
        manifest_path = Path(args.manifest).expanduser().resolve()
        data = generation_manifest.load_manifest(manifest_path)
        jobs = load_jobs(args.jobs, data, manifest_path.parent)
        workers = args.max_workers or len(jobs)
        if workers < 1:
            raise ValueError("--max-workers 必须大于0，或使用0表示全部并行。")
        state_file = manifest_path.parent / "storyboards" / "edited" / "image-edit-tasks.json"
        if state_file.is_file():
            state = json.loads(state_file.read_text(encoding="utf-8"))
        else:
            state = {"provider": "lingzhi_cli", "jobs": {}}
        records = state.setdefault("jobs", {})

        ready: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        new_jobs: list[dict[str, Any]] = []
        for job in jobs:
            record = records.get(str(job["segmentId"]), {})
            fingerprint = _job_fingerprint(job)
            if str(record.get("taskId", "")).strip() and record.get("inputFingerprint") != fingerprint:
                raise ValueError(
                    f"Segment {job['segmentId']} 输入已变化，旧任务ID不能恢复；必须先由用户确认新的图片生成。"
                )
            job["inputFingerprint"] = fingerprint
            if record.get("status") == "awaiting_user_confirmation" and job["output"].is_file():
                if record.get("finalSha256") and record["finalSha256"] != generation_manifest.file_sha256(job["output"]):
                    raise ValueError("已完成Storyboard像素已变化，不能静默恢复或覆盖。")
                ready.append({**job, "taskId": str(record.get("taskId", "")), "credits": record.get("credits", 0), "reused": True})
            elif str(record.get("taskId", "")).strip() and record.get("status") in {"submitted", "querying"}:
                pending.append({**job, "taskId": str(record["taskId"])})
            elif str(record.get("taskId", "")).strip() and record.get("status") == "failed":
                raise ValueError(
                    f"Segment {job['segmentId']} 图片任务 {record['taskId']} 已终态失败；禁止自动重新submit。"
                )
            elif str(record.get("taskId", "")).strip():
                raise ValueError(
                    f"Segment {job['segmentId']} 已有任务ID但本地状态或成品缺失；"
                    "请恢复原任务资料，禁止自动重新submit。"
                )
            else:
                new_jobs.append(job)
        if any(not job.get("regionMode") for job in new_jobs):
            raise ValueError("新图片任务必须使用object-regions-v1区域保护；旧任务仅允许恢复已存在ID。")
        if new_jobs and not args.image_generation_approved:
            raise ValueError("图片生成会消耗积分；用户确认开始后必须传入 --image-generation-approved。")

        # Upload references in parallel, then submit every new image task before polling any task.
        uploaded: dict[int, list[str]] = {}
        submit_errors: list[str] = []
        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(new_jobs)))) as pool:
            futures = {pool.submit(_upload, job): job for job in new_jobs}
            for future in as_completed(futures):
                job = futures[future]
                uploaded[job["segmentId"]] = future.result()
        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(new_jobs)))) as pool:
            futures = {
                pool.submit(
                    run_cli.submit_image,
                    job["prompt"],
                    reference_images=uploaded[job["segmentId"]],
                    aspect_ratio=job["aspectRatio"],
                    resolution=job["resolution"],
                ): job
                for job in new_jobs
            }
            for future in as_completed(futures):
                job = futures[future]
                try:
                    task_id = future.result()
                except (OSError, ValueError, run_cli.LzStudioError) as exc:
                    submit_errors.append(f"Segment {job['segmentId']} submit失败：{exc}")
                    continue
                records[str(job["segmentId"])] = {
                    "segmentId": job["segmentId"],
                    "storyboardId": job["storyboardId"],
                    "provider": "lingzhi_cli",
                    "taskId": task_id,
                    "inputFingerprint": job["inputFingerprint"],
                    "status": "submitted",
                }
                _write_state(state_file, state)
                pending.append({**job, "taskId": task_id})
        if submit_errors:
            raise ValueError("；".join(submit_errors) + "。其他已返回的任务ID已保存；再次运行只恢复这些ID。")

        if args.submit_only and pending:
            print(json.dumps({
                "ok": True,
                "provider": "lingzhi_cli",
                "parallel": len(pending) > 1,
                "status": "submitted",
                "storyboards": [
                    {"segmentId": job["segmentId"], "taskId": job["taskId"]}
                    for job in sorted(pending, key=lambda item: item["segmentId"])
                ],
                "nextCommand": (
                    f"python3 scripts/run_storyboard_edits.py --manifest {manifest_path} "
                    f"--jobs {Path(args.jobs).expanduser().resolve()}"
                ),
            }, ensure_ascii=False))
            return 0

        ffmpeg = storyboard_cells.executable("ffmpeg", args.ffmpeg)
        ffprobe = storyboard_cells.executable("ffprobe", args.ffprobe, required=False)

        def poll_and_finish(job: dict[str, Any]) -> dict[str, Any]:
            raw = run_cli.poll_task(
                run_cli.fetch_image,
                job["taskId"],
                interval=args.poll_interval,
                timeout=args.poll_timeout,
            )
            media = run_cli._media(raw, "image/png")
            job["rawOutput"].parent.mkdir(parents=True, exist_ok=True)
            raw_output = run_cli.download_media(media, job["rawOutput"])
            lock = storyboard_cells.lock_merge(
                ffmpeg,
                ffprobe,
                job["original"],
                Path(raw_output),
                job["replacementMap"],
                job["output"],
            )
            return {**job, "credits": run_cli._credits(raw), "lock": lock, "reused": False}

        finish_errors: list[str] = []
        still_processing: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(pending)))) as pool:
            futures = {pool.submit(poll_and_finish, job): job for job in pending}
            for future in as_completed(futures):
                try:
                    completed = future.result()
                except (OSError, ValueError, RuntimeError, KeyError, run_cli.LzStudioError) as exc:
                    failed_job = futures[future]
                    message = str(exc)
                    timeout_like = isinstance(exc, TimeoutError) or "timeout" in message.lower() or "超时" in message
                    records[str(failed_job["segmentId"])]["lastError"] = message
                    if timeout_like:
                        records[str(failed_job["segmentId"])]["status"] = "querying"
                        still_processing.append({
                            "segmentId": failed_job["segmentId"],
                            "taskId": failed_job["taskId"],
                        })
                        _write_state(state_file, state)
                        continue
                    _write_state(state_file, state)
                    finish_errors.append(f"Segment {failed_job['segmentId']}: {exc}")
                    continue
                records[str(completed["segmentId"])].update({
                    "status": "awaiting_user_confirmation",
                    "output": str(completed["output"]),
                    "rawOutput": str(completed["rawOutput"]),
                    "credits": completed.get("credits", 0),
                    "finalSha256": generation_manifest.file_sha256(completed["output"]),
                })
                _write_state(state_file, state)
                ready.append(completed)

        if finish_errors:
            raise ValueError("；".join(finish_errors) + "。各段任务ID和已完成结果均已保存；禁止自动重新submit。")
        ready.sort(key=lambda item: item["segmentId"])
        results: list[dict[str, Any]] = []
        for job in ready:
            normalized_map = job["normalizedMap"]
            lock_file = job["output"].with_suffix(".review.json")
            replacement = {
                "applied": True,
                "method": "whole-board-user-review",
                "provider": "lingzhi_cli",
                "taskId": job["taskId"],
                "generationAttempts": 1,
                "maxImageEditAttempts": 1,
                "imageEditSucceeded": True,
                "mapValid": True,
                "layoutRestored": True,
                "fullBoardUserReviewRequired": True,
                "userConfirmed": False,
                "editedImageFile": str(job["rawOutput"]),
                "finalImageFile": str(job["output"]),
                "mapFile": str(job["replacementMap"]),
                "lockMergeFile": str(lock_file),
                "editableCells": normalized_map["editableCells"],
                "frozenCells": normalized_map["frozenCells"],
                "replacementVerified": False,
            }
            existing = next((b for b in data["storyboards"]["edited"] if int(b["storyboardId"]) == job["storyboardId"]), None)
            if job.get("reused") and existing and existing.get("replacementVerified"):
                # Re-querying unchanged results must not erase user approval.
                generation_manifest.normalize_replacement_record(existing["replacement"])
                replacement = existing["replacement"]
            generation_manifest.add_storyboard(
                manifest_path,
                "edited",
                str(job["output"]),
                job["storyboardId"],
                anchors=job["originalEntry"].get("anchors"),
                replacement=replacement,
            )
            records[str(job["segmentId"])].update({
                "status": "awaiting_user_confirmation",
                "output": str(job["output"]),
                "rawOutput": str(job["rawOutput"]),
                "credits": job.get("credits", 0),
            })
            results.append({
                "segmentId": job["segmentId"],
                "taskId": job["taskId"],
                "output": str(job["output"]),
                "credits": job.get("credits", 0),
                "reused": bool(job.get("reused", False)),
            })
        _write_state(state_file, state)
        all_confirmed = all(
            b.get("replacementVerified") for b in generation_manifest.load_manifest(manifest_path)["storyboards"]["edited"]
            if int(b["storyboardId"]) in {j["storyboardId"] for j in jobs}
        )
        print(json.dumps({
            "ok": True,
            "provider": "lingzhi_cli",
            "parallel": len(jobs) > 1,
            "status": "processing" if still_processing else ("confirmed" if all_confirmed else "awaiting_user_confirmation"),
            "storyboards": results,
            "processing": sorted(still_processing, key=lambda item: item["segmentId"]),
            "nextCommand": (
                f"python3 scripts/run_storyboard_edits.py --manifest {manifest_path} "
                f"--jobs {Path(args.jobs).expanduser().resolve()}"
                if still_processing else
                (None if all_confirmed else f"python3 scripts/generation_manifest.py confirm-storyboards --manifest {manifest_path} --user-confirmed")
            ),
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError, run_cli.LzStudioError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
