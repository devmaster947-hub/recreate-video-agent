#!/usr/bin/env python3
"""Validate local product images before required local visual inspection."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
FORBIDDEN_SUFFIXES = {
    ".gif", ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
    ".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg",
}


def validate_images(values: list[str]) -> dict:
    if not values:
        raise ValueError("至少需要一张产品图。")
    images: list[str] = []
    seen: set[Path] = set()
    for value in values:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"产品图不存在或不是文件：{path}")
        suffix = path.suffix.lower()
        if suffix in FORBIDDEN_SUFFIXES or suffix not in ALLOWED_SUFFIXES:
            raise ValueError(
                f"产品图必须是静态图片 {sorted(ALLOWED_SUFFIXES)}：{path}"
            )
        if path.stat().st_size <= 0:
            raise ValueError(f"产品图为空文件：{path}")
        if path in seen:
            raise ValueError(f"产品图列表包含重复文件：{path}")
        seen.add(path)
        images.append(str(path))
    return {
        "ok": True,
        "images": images,
        "count": len(images),
        "requires_view_image": True,
        "uploaded_to_api": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", action="append", required=True)
    args = parser.parse_args()
    try:
        result = validate_images(args.image)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
