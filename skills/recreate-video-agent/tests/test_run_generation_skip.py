from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import generation_manifest, run_generation  # noqa: E402


class RunGenerationSkipTests(unittest.TestCase):
    def test_single_segment_without_user_creator_does_not_submit_creator_image(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            board = root / "board.png"
            board.write_bytes(b"board")
            data = {
                "skillVersion": "4.3",
                "videoPrompts": {"segments": [{"segmentId": 1}]},
                "creators": [],
            }
            self.assertEqual(
                run_generation.creator_files(data, {"segmentId": 1, "creatorIds": ["person-a"]}),
                [],
            )

    def test_user_provided_creator_is_submitted_even_for_single_segment(self):
        with tempfile.TemporaryDirectory() as td:
            creator = Path(td) / "creator.png"
            creator.write_bytes(b"creator")
            data = {
                "skillVersion": "4.3",
                "videoPrompts": {"segments": [{"segmentId": 1}]},
                "creators": [{
                    "creatorId": "person-a", "file": str(creator),
                    "sourceType": "user_provided", "layout": "provided",
                }],
            }
            self.assertEqual(
                run_generation.creator_files(data, {"segmentId": 1, "creatorIds": ["person-a"]}),
                [str(creator)],
            )

    def test_prepare_references_includes_registered_product_after_storyboard(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            board = root / "board.png"
            product = root / "product.png"
            board.write_bytes(b"board")
            product.write_bytes(b"product")
            data = {
                "product": {
                    "mode": "replacement",
                    "useBenchmarkProduct": False,
                    "productImages": [{"file": str(product)}],
                },
                "storyboards": {"generation": [{
                    "storyboardId": 1,
                    "file": str(board),
                    "anchors": [{"productPresent": True}],
                }]},
                "creators": [],
            }
            segment = {"segmentId": 1, "storyboardIds": [1], "creatorIds": []}
            self.assertEqual(
                run_generation.prepare_references(data, segment, root, "seedance-2-fast"),
                [str(board), str(product)],
            )

    def test_no_local_cli_is_successful_skip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": td, "task_id": "task", "reuse": False})()
            )
            board = root / "board.png"
            board.write_bytes(b"board")
            data = generation_manifest.load_manifest(manifest)
            data["userConfig"]["duration"] = 10
            data["storyboards"]["generation"] = [{
                "storyboardId": 1, "segmentId": 1, "file": str(board), "layout": "3x3",
                "globalStart": 0, "globalEnd": 10, "localStart": 0, "localEnd": 10,
                "anchors": [{"index": i + 1, "localTimestamp": i, "globalTimestamp": i} for i in range(9)],
            }]
            prompts = root / "prompts.json"
            prompts.write_text(json.dumps({"videoPrompts": {"summary": "", "segments": [{
                "segmentId": 1, "title": "test", "duration": 10, "globalStart": 0, "globalEnd": 10,
                "storyboardIds": [1], "creatorIds": [], "productPresent": False, "prompt": "test prompt"
            }]}}), encoding="utf-8")
            data["videoPrompts"] = {"file": str(prompts), "segments": json.loads(prompts.read_text())["videoPrompts"]["segments"]}
            generation_manifest.save_manifest(manifest, data)

            argv = ["run_generation.py", "--manifest", str(manifest)]
            with patch.object(sys, "argv", argv), patch.object(
                run_generation.local_video_cli,
                "detect_video_providers",
                return_value={"dreamina_cli": False, "xiaoyunque_cli": False, "lingzhi_cli": False},
            ):
                self.assertEqual(run_generation.main(), 0)
            saved = generation_manifest.load_manifest(manifest)
            self.assertEqual(saved["videoGeneration"]["status"], "skipped_no_local_cli")
            self.assertFalse(saved["videoGeneration"]["generated"])


if __name__ == "__main__":
    unittest.main()
