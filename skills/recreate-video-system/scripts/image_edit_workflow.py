#!/usr/bin/env python3
"""Run one Storyboard image edit through Lingzhi CLI and report safe fallback state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from scripts import run_cli
except ModuleNotFoundError:
    import run_cli  # type: ignore[no-redef]


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def execute(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    prompt_file = Path(args.prompt_file).expanduser().resolve()
    report_file = Path(args.report).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("图片编辑Prompt不能为空。")
    references = [Path(item).expanduser().resolve() for item in args.reference_image]
    if not references:
        raise ValueError("至少需要一张参考图，第一张必须是原Storyboard。")
    for path in references:
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"参考图不存在或为空：{path}")

    task_id = str(args.resume_task_id or "").strip()
    if not task_id:
        urls: list[str] = []
        try:
            for path in references:
                media = run_cli.upload_file(path)
                urls.append(str(media["url"]))
        except run_cli.LzStudioError as exc:
            return 2, {
                "status": "cli_unavailable",
                "fallbackAllowed": True,
                "taskId": "",
                "message": str(exc),
                "fallbackReason": "CLI在创建图片任务前失败，可使用智能体图片编辑能力。",
            }
        try:
            task_id = run_cli.submit_image(
                prompt,
                reference_images=urls,
                aspect_ratio=args.aspect_ratio,
                resolution=args.resolution,
            )
        except run_cli.LzStudioError as exc:
            return 3, {
                "status": "submit_outcome_unknown",
                "fallbackAllowed": False,
                "taskId": "",
                "message": str(exc),
                "fallbackReason": "提交结果不明确，禁止切换渠道以避免重复扣费。",
            }
        write_json(report_file, {"status": "submitted", "fallbackAllowed": False, "taskId": task_id})

    try:
        media = run_cli.poll_image(task_id)
        saved = run_cli.download_media(media, output)
    except run_cli.TaskFailedError as exc:
        return 2, {
            "status": "terminal_failed",
            "fallbackAllowed": True,
            "taskId": task_id,
            "message": str(exc),
            "fallbackReason": "灵智图片任务已明确终止且无可用结果。",
        }
    except run_cli.LzStudioError as exc:
        return 3, {
            "status": "poll_unknown",
            "fallbackAllowed": False,
            "taskId": task_id,
            "message": str(exc),
            "fallbackReason": "任务已取得ID，只能恢复轮询，不得切换渠道。",
        }
    return 0, {
        "status": "success",
        "provider": "lingzhi_cli",
        "model": run_cli.IMAGE_MODEL_ID,
        "fallbackAllowed": False,
        "taskId": task_id,
        "output": str(saved),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--reference-image", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--aspect-ratio", required=True)
    parser.add_argument("--resolution", default="1K")
    parser.add_argument("--resume-task-id")
    args = parser.parse_args()
    try:
        code, report = execute(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report = {"status": "invalid_input", "fallbackAllowed": False, "taskId": "", "message": str(exc)}
        code = 1
    write_json(Path(args.report).expanduser().resolve(), report)
    print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
