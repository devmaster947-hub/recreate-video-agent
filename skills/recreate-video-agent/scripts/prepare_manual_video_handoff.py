#!/usr/bin/env python3
"""Build a copy-ready prompt and exact reference-image handoff when no video CLI exists."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from scripts import export_prompt_texts, generation_manifest, reference_audit, run_generation  # noqa: E402


def build(manifest_path: Path, output_dir: Path | None = None) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    data = generation_manifest.load_manifest(manifest_path)
    prompt_segments = export_prompt_texts.registered_segments(data, manifest_path)
    reference_audit.audit(data, prompt_segments)
    prompt_export = export_prompt_texts.export(manifest_path, output_dir)
    prompt_files = {
        str(item["segmentId"]): str(Path(item["file"]).resolve())
        for item in prompt_export["segments"]
    }
    model = str(data.get("userConfig", {}).get("videoModel", "seedance-2-fast"))
    root = manifest_path.parent
    segments: list[dict[str, Any]] = []
    for segment in prompt_segments:
        segment_id = str(segment.get("segmentId"))
        ordered = run_generation.prepare_references(data, segment, root, model)
        storyboards = set(run_generation.storyboard_files(data, segment))
        creators = set(run_generation.creator_files(data, segment))
        references = []
        for file in ordered:
            role = "storyboard" if file in storyboards else "creator" if file in creators else "product"
            references.append({"role": role, "file": str(Path(file).expanduser().resolve())})
        segments.append({
            "segmentId": segment.get("segmentId"),
            "title": segment.get("title", ""),
            "duration": segment.get("duration"),
            "promptFile": prompt_files[segment_id],
            "references": references,
            "orderedFiles": [item["file"] for item in references],
        })
    return {
        "ok": True,
        "status": "manual_handoff_ready",
        "model": model,
        "segments": segments,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else None
    print(json.dumps(build(Path(args.manifest), output_dir), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
