#!/usr/bin/env python3
"""Detect the first usable video CLI in the skill's fixed priority order."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from scripts import generation_manifest, local_video_cli  # noqa: E402


def inspect(manifest_path: Path) -> dict[str, object]:
    started = time.monotonic()
    manifest_path = manifest_path.expanduser().resolve()
    data = generation_manifest.load_manifest(manifest_path)
    model = str(data.get("userConfig", {}).get("videoModel", "seedance-2-fast"))
    if local_video_cli.libtv_cli_available():
        executable = str(local_video_cli.resolve_libtv_cli())
        availability: dict[str, bool | None] = {
            "libtv_cli": True,
            "xiaoyunque_cli": None,
            "dreamina_cli": None,
        }
        selected = "libtv_cli"
        lower_priority_skipped = True
    else:
        local = local_video_cli.detect_video_providers(model)
        availability = {"libtv_cli": False, **local}
        executable = ""
        selected = local_video_cli.resolve_generation_provider(availability=availability)
        lower_priority_skipped = False
    return {
        "ok": True,
        "model": model,
        "priority": list(local_video_cli.GENERATION_PROVIDER_ORDER),
        "availability": availability,
        "selected": selected,
        "executable": executable,
        "lowerPriorityChecksSkipped": lower_priority_skipped,
        "elapsedSeconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    print(json.dumps(inspect(Path(args.manifest)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
