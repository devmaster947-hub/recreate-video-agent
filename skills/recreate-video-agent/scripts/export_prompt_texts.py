#!/usr/bin/env python3
"""Export one copy-ready UTF-8 text file per registered video prompt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON根节点必须是对象：{path}")
    return data


def registered_segments(manifest: dict[str, Any], manifest_path: Path) -> list[dict[str, Any]]:
    video_prompts = manifest.get("videoPrompts")
    if not isinstance(video_prompts, dict):
        raise ValueError("manifest.videoPrompts必须是对象。")

    segments = video_prompts.get("segments")
    if not segments:
        prompt_file = video_prompts.get("file")
        if not isinstance(prompt_file, str) or not prompt_file.strip():
            raise ValueError("manifest尚未登记Video Prompt。")
        source_path = Path(prompt_file).expanduser()
        if not source_path.is_absolute():
            source_path = manifest_path.parent / source_path
        source = load_json(source_path.resolve())
        wrapped = source.get("videoPrompts", source)
        if not isinstance(wrapped, dict):
            raise ValueError("Prompt文件中的videoPrompts必须是对象。")
        segments = wrapped.get("segments")

    if not isinstance(segments, list) or not segments:
        raise ValueError("videoPrompts.segments必须是非空数组。")

    normalized: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            raise ValueError(f"Segment {index}必须是对象。")
        prompt = segment.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError(f"Segment {index}缺少非空prompt。")
        normalized.append(segment)
    return normalized


def export(manifest_path: Path, output_dir: Path | None = None) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    manifest = load_json(manifest_path)
    segments = registered_segments(manifest, manifest_path)
    destination = (output_dir or manifest_path.parent / "prompts").expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    outputs: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        segment_id = segment.get("segmentId", index)
        try:
            number = int(segment_id)
        except (TypeError, ValueError):
            number = index
        target = destination / f"segment-{number:02d}-prompt.txt"
        prompt = segment["prompt"]
        target.write_text(prompt, encoding="utf-8")
        outputs.append(
            {
                "segmentId": segment_id,
                "title": segment.get("title", ""),
                "duration": segment.get("duration"),
                "file": str(target),
                "characters": len(prompt),
            }
        )

    return {"ok": True, "outputDir": str(destination), "segments": outputs}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export each registered Video Prompt as a copy-ready UTF-8 text file."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    result = export(
        Path(args.manifest),
        Path(args.output_dir) if args.output_dir else None,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
