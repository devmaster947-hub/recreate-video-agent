#!/usr/bin/env python3
"""Transcribe a local benchmark video into the audioReview manifest contract."""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_MLX_MODEL = "mlx-community/whisper-turbo"
DEFAULT_OPENAI_MODEL = "small"


def has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def available_backends() -> dict[str, bool]:
    return {
        "mlx": platform.machine() == "arm64" and has_module("mlx_whisper"),
        "openai": has_module("whisper"),
    }


def resolve_backend(requested: str) -> str:
    available = available_backends()
    if requested == "auto":
        for candidate in ("mlx", "openai"):
            if available[candidate]:
                return candidate
        raise ValueError(
            "未检测到本地Whisper后端。Apple Silicon推荐使用："
            "uv run --with mlx-whisper python scripts/local_transcribe.py ..."
        )
    if not available.get(requested, False):
        raise ValueError(f"本地Whisper后端不可用：{requested}")
    return requested


def media_has_audio(video: Path) -> bool:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=index",
        "-of",
        "json",
        str(video),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout or "{}")
    return bool(payload.get("streams"))


def transcribe_mlx(video: Path, model: str, language: str | None) -> dict[str, Any]:
    import mlx_whisper

    options: dict[str, Any] = {
        "path_or_hf_repo": model,
        "word_timestamps": True,
        "verbose": False,
    }
    if language:
        options["language"] = language
    return mlx_whisper.transcribe(str(video), **options)


def transcribe_openai(video: Path, model: str, language: str | None) -> dict[str, Any]:
    import whisper

    loaded = whisper.load_model(model)
    options: dict[str, Any] = {"word_timestamps": True, "verbose": False}
    if language:
        options["language"] = language
    return loaded.transcribe(str(video), **options)


def clean_segments(result: dict[str, Any]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for raw in result.get("segments", []):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text", "")).strip()
        if not text:
            continue
        try:
            start, end = float(raw["start"]), float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < 0 or end <= start:
            continue
        segment: dict[str, Any] = {
            "id": len(cleaned) + 1,
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
        }
        words = []
        for word in raw.get("words", []) if isinstance(raw.get("words"), list) else []:
            if not isinstance(word, dict) or not str(word.get("word", "")).strip():
                continue
            try:
                word_start, word_end = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            words.append(
                {
                    "start": round(word_start, 3),
                    "end": round(word_end, 3),
                    "text": str(word["word"]).strip(),
                }
            )
        if words:
            segment["words"] = words
        cleaned.append(segment)
    return cleaned


def build_audio_review(
    result: dict[str, Any], *, video: Path, backend: str, model: str
) -> dict[str, Any]:
    segments = clean_segments(result)
    speech_detected = bool(segments)
    review: dict[str, Any] = {
        "status": "transcript_ready" if speech_detected else "verified_no_speech",
        "hasAudio": True,
        "speechDetected": speech_detected,
        "reviewMethod": f"local_{backend}_whisper",
        "backend": backend,
        "model": model,
        "language": str(result.get("language", "") or "unknown"),
        "sourceVideo": str(video),
        "text": " ".join(item["text"] for item in segments),
        "segments": segments,
        "audioUploaded": False,
    }
    return {"audioReview": review}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video")
    parser.add_argument("--output")
    parser.add_argument("--backend", choices=("auto", "mlx", "openai"), default="auto")
    parser.add_argument("--model")
    parser.add_argument("--language")
    parser.add_argument("--detect", action="store_true")
    args = parser.parse_args()

    if args.detect:
        print(json.dumps({"ok": True, "backends": available_backends()}, ensure_ascii=False))
        return 0
    if not args.video or not args.output:
        raise SystemExit("转写必须同时提供 --video 和 --output。")

    video = Path(args.video).expanduser().resolve()
    if not video.is_file() or video.stat().st_size <= 0:
        raise SystemExit(f"视频不存在或为空：{video}")
    output = Path(args.output).expanduser().resolve()
    if not media_has_audio(video):
        payload = {
            "audioReview": {
                "status": "no_audio",
                "hasAudio": False,
                "speechDetected": False,
                "reviewMethod": "ffprobe",
                "sourceVideo": str(video),
                "segments": [],
                "audioUploaded": False,
            }
        }
    else:
        try:
            backend = resolve_backend(args.backend)
            model = args.model or (DEFAULT_MLX_MODEL if backend == "mlx" else DEFAULT_OPENAI_MODEL)
            result = (
                transcribe_mlx(video, model, args.language)
                if backend == "mlx"
                else transcribe_openai(video, model, args.language)
            )
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            raise SystemExit(str(exc)) from None
        payload = build_audio_review(result, video=video, backend=backend, model=model)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "status": payload["audioReview"]["status"],
                "language": payload["audioReview"].get("language"),
                "segments": len(payload["audioReview"].get("segments", [])),
                "audioUploaded": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
