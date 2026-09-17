from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import generation_manifest, video_cli_preflight  # noqa: E402


class VideoCliPreflightTests(unittest.TestCase):
    def test_uses_fixed_priority_and_selects_first_available(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": td, "task_id": "task", "reuse": False})()
            )
            availability = {
                "libtv_cli": False,
                "xiaoyunque_cli": True,
                "dreamina_cli": True,
            }
            with patch.object(
                video_cli_preflight.local_video_cli, "libtv_cli_available", return_value=False
            ), patch.object(
                video_cli_preflight.local_video_cli,
                "detect_video_providers",
                return_value={"xiaoyunque_cli": True, "dreamina_cli": True},
            ):
                result = video_cli_preflight.inspect(Path(manifest))
            self.assertEqual(
                result["priority"],
                ["libtv_cli", "xiaoyunque_cli", "dreamina_cli"],
            )
            self.assertEqual(result["selected"], "xiaoyunque_cli")

    def test_selects_none_when_every_cli_is_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": td, "task_id": "task", "reuse": False})()
            )
            availability = {
                "libtv_cli": False,
                "xiaoyunque_cli": False,
                "dreamina_cli": False,
            }
            with patch.object(
                video_cli_preflight.local_video_cli, "libtv_cli_available", return_value=False
            ), patch.object(
                video_cli_preflight.local_video_cli,
                "detect_video_providers",
                return_value={"xiaoyunque_cli": False, "dreamina_cli": False},
            ):
                result = video_cli_preflight.inspect(Path(manifest))
            self.assertIsNone(result["selected"])


if __name__ == "__main__":
    unittest.main()
