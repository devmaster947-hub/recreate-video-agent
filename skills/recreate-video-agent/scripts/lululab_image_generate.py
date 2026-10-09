#!/usr/bin/env python3
"""Generate or edit one image through LuluLab CLI through the configured ImageGenV2 workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from scripts import local_video_cli, service_privacy, server_video_analysis  # noqa: E402


def write_report(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        value = Path(args.prompt_file).expanduser().resolve().read_text(encoding="utf-8")
    else:
        value = args.prompt or ""
    if not value.strip():
        raise ValueError("图片 Prompt 不能为空。")
    return value.strip()


def _execute(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    report_path = Path(args.report).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    prompt = read_prompt(args)
    references = [Path(value).expanduser().resolve() for value in args.reference_image]
    for path in references:
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"参考图不存在或为空：{path}")

    task_id = str(args.resume_task_id or "").strip()
    if report_path.is_file():
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        previous_id = str(previous.get("taskId", "")).strip() if isinstance(previous, dict) else ""
        previous_status = str(previous.get("status", "")).strip() if isinstance(previous, dict) else ""
        if task_id and previous_id and task_id != previous_id:
            raise ValueError("恢复任务 ID 与已保存的图片任务不一致。")
        if not task_id and (previous_id or previous_status in {"submit_pending", "submit_outcome_unknown", "success"}):
            raise ValueError("该输出已有提交记录；只能恢复原任务，禁止重复提交。")

    if not task_id:
        try:
            local_video_cli.image_workflow_id()
        except local_video_cli.LocalVideoCliError as exc:
            raise ValueError(str(exc)) from None

    if not task_id:
        write_report(report_path, {
            "status": "submit_pending",
            "taskId": "",
            "provider": "lululab_cli",
            "model": local_video_cli.LULULAB_IMAGE_MODEL_ID,
            "resolution": local_video_cli.LULULAB_IMAGE_RESOLUTION,
            "output": str(output_path),
        })
        try:
            task_id = local_video_cli.submit_lululab_image(
                prompt,
                reference_files=references,
                aspect_ratio=args.aspect_ratio,
            )
        except (local_video_cli.LocalVideoCliError, service_privacy.AuthorizationUnavailableError) as exc:
            return 3, {
                "status": "submit_outcome_unknown",
                "taskId": "",
                "provider": "lululab_cli",
                "model": local_video_cli.LULULAB_IMAGE_MODEL_ID,
                "resolution": local_video_cli.LULULAB_IMAGE_RESOLUTION,
                "message": service_privacy.public_error(str(exc), "LuluLab图片提交失败。"),
            }
        write_report(report_path, {
            "status": "submitted",
            "taskId": task_id,
            "provider": "lululab_cli",
            "model": local_video_cli.LULULAB_IMAGE_MODEL_ID,
            "resolution": local_video_cli.LULULAB_IMAGE_RESOLUTION,
            "output": str(output_path),
        })

    try:
        raw = local_video_cli.poll_task(local_video_cli.fetch_lululab_image, task_id, timeout=1800)
        media = local_video_cli.media(raw, "image/png")
        saved = local_video_cli.download_media(media, output_path)
    except local_video_cli.TaskFailedError as exc:
        return 2, {
            "status": "terminal_failed",
            "taskId": task_id,
            "provider": "lululab_cli",
            "model": local_video_cli.LULULAB_IMAGE_MODEL_ID,
            "resolution": local_video_cli.LULULAB_IMAGE_RESOLUTION,
            "message": str(exc),
        }
    except (local_video_cli.LocalVideoCliError, service_privacy.AuthorizationUnavailableError) as exc:
        return 3, {
            "status": "poll_unknown",
            "taskId": task_id,
            "provider": "lululab_cli",
            "model": local_video_cli.LULULAB_IMAGE_MODEL_ID,
            "resolution": local_video_cli.LULULAB_IMAGE_RESOLUTION,
            "message": service_privacy.public_error(str(exc), "LuluLab图片任务查询失败。"),
        }
    return 0, {
        "status": "success",
        "taskId": task_id,
        "provider": "lululab_cli",
        "model": local_video_cli.LULULAB_IMAGE_MODEL_ID,
        "resolution": local_video_cli.LULULAB_IMAGE_RESOLUTION,
        "output": str(saved),
        "media": media,
    }


def execute(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    report = Path(args.report).expanduser().resolve()
    lock = server_video_analysis.acquire_run_lock(report, lock_path=report.with_suffix(report.suffix + ".lock"))
    try:
        code, result = _execute(args)
        write_report(report, result)
        return code, result
    finally:
        server_video_analysis.release_run_lock(lock)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file")
    parser.add_argument("--reference-image", action="append", default=[])
    parser.add_argument("--aspect-ratio", required=True, choices=("16:9", "1:1", "3:4", "4:3", "9:16"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--resume-task-id")
    args = parser.parse_args()
    report_path = Path(args.report).expanduser().resolve()
    try:
        code, report = execute(args)
    except (OSError, ValueError, json.JSONDecodeError, server_video_analysis.AnalysisError) as exc:
        report = {"status": "invalid_input", "taskId": "", "message": str(exc)}
        code = 1
    print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
