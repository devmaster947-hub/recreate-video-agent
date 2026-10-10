#!/usr/bin/env python3
"""Read saved recreation artifacts without contacting the provider or submitting work."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def existing_file(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser().resolve()
    return str(path) if path.is_file() and path.stat().st_size > 0 else None


def summarize(manifest: dict[str, Any]) -> dict[str, Any]:
    final_record = manifest.get("finalVideo") or {}
    final_path = existing_file(final_record.get("file"))
    videos = manifest.get("videos") or []
    segments = []
    for item in videos:
        if not isinstance(item, dict):
            continue
        entry = {
            "segmentId": item.get("segmentId"),
            "status": item.get("status"),
            "taskId": item.get("taskId"),
            "file": existing_file(item.get("output_file")),
        }
        segments.append(entry)
    quality_path = existing_file(final_record.get("qualityReport"))
    quality = None
    if quality_path:
        try:
            report = json.loads(Path(quality_path).read_text(encoding="utf-8"))
            quality = {
                "file": quality_path,
                "technicalPassed": report.get("technicalPassed"),
                "semanticStatus": report.get("semanticStatus"),
                "deliveryBlocked": report.get("deliveryBlocked"),
            }
        except (OSError, json.JSONDecodeError):
            quality = {"file": quality_path, "readable": False}
    blueprint = manifest.get("videoBlueprint") or {}
    analysis_file = existing_file(blueprint.get("file"))
    analysis = {
        "status": blueprint.get("status"),
        "taskId": blueprint.get("taskId"),
        "file": analysis_file,
    }
    if final_path:
        status = "video_ready"
    elif final_record.get("file"):
        status = "final_file_missing"
    elif any(item.get("taskId") and not item.get("file") for item in segments):
        status = "video_task_pending"
    elif segments and all(item.get("file") for item in segments):
        status = "segments_ready"
    elif analysis_file:
        status = "analysis_ready"
    elif analysis.get("taskId"):
        status = "analysis_task_pending"
    else:
        status = "initialized"
    return {
        "status": status,
        "deliverableReady": bool(final_path),
        "finalVideo": final_path,
        "analysis": analysis,
        "segments": segments,
        "quality": quality,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    try:
        manifest_path = Path(args.manifest).expanduser().resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("Manifest must be a JSON object.")
        print(json.dumps(summarize(manifest), ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Cannot inspect saved task: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
