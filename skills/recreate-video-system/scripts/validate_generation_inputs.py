#!/usr/bin/env python3
"""Validate model-routed, per-Segment video generation inputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
FORBIDDEN_SUFFIXES = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".gif",
    ".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg",
}
MODEL_MAP = {
    "Seedance2 Mini": "seedance2.0mini",
    "Seedance 2 Mini": "seedance2.0mini",
    "Seedance 2 mini": "seedance2.0mini",
    "Seedance2 Fast": "seedance2.0fast",
    "Seedance 2 Fast": "seedance2.0fast",
    "Seedance 2 fast": "seedance2.0fast",
    "Seedance2 Fast VIP": "seedance2.0fast_vip",
    "Seedance 2 Fast VIP": "seedance2.0fast_vip",
    "Seedance 2 fast vip": "seedance2.0fast_vip",
}


def check_image(path_text: str, role: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"{role}不存在或不是文件：{path}")
    suffix = path.suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES:
        raise ValueError(f"{role}不能是视频、音频、GIF或动态参考：{path}")
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError(f"{role}必须是静态图片 {sorted(IMAGE_SUFFIXES)}：{path}")
    if path.stat().st_size <= 0:
        raise ValueError(f"{role}为空文件：{path}")
    return path


def trusted_product_path(path_text: str) -> Path:
    """Normalize a product path already validated before it entered the manifest."""
    return Path(path_text).expanduser().resolve()


def validate(args: argparse.Namespace) -> dict:
    if args.video_model not in MODEL_MAP:
        raise ValueError(
            "视频模型必须是 Seedance2 Mini、Seedance2 Fast 或 "
            "Seedance2 Fast VIP。"
        )
    if not 4 <= args.duration <= 15:
        raise ValueError("Segment 时长必须在 4–15 秒；不得自动拆分或改写服务端 prompt。")
    if args.max_images < 1:
        raise ValueError("--max-images 至少为 1。")
    if args.extra_reference:
        raise ValueError(
            "检测到禁止的额外参考。只允许产品图、本段达人图和本段分镜图。"
        )

    # Product images are globally validated once by validate_product_images.py
    # before analysis and then selected from the manifest. Avoid repeating file
    # existence, size, and format I/O for every Segment.
    products = [trusted_product_path(value) for value in args.product_image]
    creators = [check_image(value, "本段达人图") for value in args.creator_image]
    storyboard = (
        check_image(args.storyboard_image, "本段分镜图")
        if args.storyboard_image
        else None
    )
    ordered = products + creators + ([storyboard] if storyboard else [])
    if len(set(ordered)) != len(ordered):
        raise ValueError("产品图、达人图和分镜图包含重复或复用文件。")
    if len(ordered) > args.max_images:
        raise ValueError(
            f"本段引用图共 {len(ordered)} 张，超过平台上限 {args.max_images} 张；"
            "请让用户减少产品图，不得静默丢弃达人图或分镜图。"
        )

    return {
        "ok": True,
        "video_model": args.video_model,
        "model_version": MODEL_MAP[args.video_model],
        "duration": args.duration,
        "ratio": "9:16",
        "video_resolution": "720p",
        "generation_command": "multimodal2video" if ordered else "text2video",
        "product_images": [str(path) for path in products],
        "creator_images": [str(path) for path in creators],
        "storyboard_image": str(storyboard) if storyboard else None,
        "ordered_images": [str(path) for path in ordered],
        "total_image_count": len(ordered),
        "max_images": args.max_images,
        "benchmark_video_allowed": False,
        "dynamic_reference_allowed": False,
        "audio_reference_allowed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate one Segment's Seedance 2 references without uploading anything."
    )
    parser.add_argument("--video-model", required=True)
    parser.add_argument("--duration", type=int, required=True)
    parser.add_argument("--product-image", action="append", default=[])
    parser.add_argument("--creator-image", action="append", default=[])
    parser.add_argument("--storyboard-image")
    parser.add_argument("--max-images", type=int, default=9)
    parser.add_argument("--extra-reference", action="append", default=[])
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = validate(args)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
