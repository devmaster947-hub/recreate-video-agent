from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import generation_manifest, prepare_manual_video_handoff  # noqa: E402


class PrepareManualVideoHandoffTests(unittest.TestCase):
    def test_lists_exact_storyboard_and_prompt_file_for_each_segment(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": td, "task_id": "task", "reuse": False})()
            )
            board = root / "board.png"
            board.write_bytes(b"board")
            prompt_file = root / "local-video-prompts.json"
            segment = {
                "segmentId": 1,
                "title": "test",
                "duration": 10,
                "globalStart": 0,
                "globalEnd": 10,
                "storyboardIds": [1],
                "creatorIds": [],
                "productPresent": False,
                "prompt": "copy-ready prompt",
            }
            prompt_file.write_text(
                json.dumps({"videoPrompts": {"segments": [segment]}}),
                encoding="utf-8",
            )
            data = generation_manifest.load_manifest(manifest)
            data["skillVersion"] = "4.0"
            data["storyboards"]["generation"] = [{
                "storyboardId": 1,
                "segmentId": 1,
                "file": str(board),
                "anchors": [{"productPresent": False}],
            }]
            data["videoPrompts"] = {"file": str(prompt_file), "segments": [segment]}
            generation_manifest.save_manifest(manifest, data)

            result = prepare_manual_video_handoff.build(Path(manifest))

            self.assertEqual(result["status"], "manual_handoff_ready")
            self.assertEqual(
                result["segments"][0]["references"],
                [{"role": "storyboard", "file": str(board.resolve())}],
            )
            exported = Path(result["segments"][0]["promptFile"])
            self.assertEqual(exported.read_text(encoding="utf-8"), "copy-ready prompt")


if __name__ == "__main__":
    unittest.main()
