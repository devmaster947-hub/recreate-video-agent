#!/usr/bin/env python3
"""Verify the configured Lingzhi API key without submitting a paid task."""

from __future__ import annotations

import argparse
import json

try:
    from scripts import service_privacy
    from scripts.server_video_analysis import AnalysisError, load_key, resolve_cli, verify_key
except ModuleNotFoundError:
    import service_privacy  # type: ignore[no-redef]
    from server_video_analysis import AnalysisError, load_key, resolve_cli, verify_key  # type: ignore[no-redef]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli")
    args = parser.parse_args()
    try:
        cli = resolve_cli(args.cli)
        key = load_key()
        verify_key(cli, key)
        print(json.dumps({"ok": True, "authenticated": True}, ensure_ascii=False))
        return 0
    except service_privacy.AuthorizationUnavailableError as exc:
        parser.exit(1, f"{exc}\n")
    except (AnalysisError, OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"灵智 API Key 预检未通过：{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
