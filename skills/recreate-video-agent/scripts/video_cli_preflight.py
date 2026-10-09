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
    available = local_video_cli.lululab_cli_available()
    availability = {"lululab_cli": available}
    selected = "lululab_cli" if available else None
    executable = str(local_video_cli.resolve_lululab_cli()) if available else ""
    lower_priority_skipped = True
    return {
        "ok": True,
        "model": model,
        "priority": ["lululab_cli"],
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
