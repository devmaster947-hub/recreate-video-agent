from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))

from scripts import generation_manifest, prepare_prompt_handoff  # noqa: E402


class PreparePromptHandoffTests(unittest.TestCase):
    def test_single_segment_prepare_reuses_final_board_without_split(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": str(root), "task_id": "direct", "reuse": False})()
            )
            board = root / "board.png"
            board.write_bytes(b"board")
            anchors = [
                {
                    "index": index + 1,
                    "timestamp": float(index),
                    "anchorRole": "hard" if index == 0 else "context",
                    "eventType": "first_frame" if index == 0 else "context",
                    "personPresent": False,
                    "personCount": 0,
                    "creatorIds": [],
                }
                for index in range(9)
            ]
            data = generation_manifest.load_manifest(manifest)
            data["userConfig"]["duration"] = 9
            data["benchmarkVideo"]["analysis"] = {
                "recommendedSegments": [{"segmentId": 1, "globalStart": 0, "globalEnd": 9}]
            }
            data["storyboards"]["original"] = [{
                "storyboardId": 1,
                "segmentId": 1,
                "file": str(board),
                "globalStart": 0,
                "globalEnd": 9,
                "layout": "3x3",
                "anchors": anchors,
            }]
            generation_manifest.save_manifest(manifest, data)

            result = prepare_prompt_handoff.prepare(manifest, root / "handoff")
            saved = generation_manifest.load_manifest(manifest)["storyboards"]["generation"][0]
            self.assertTrue(result["directReuse"])
            self.assertEqual(saved["file"], str(board.resolve()))
            self.assertEqual(saved["anchors"][5]["localTimestamp"], 5.0)

    def test_multi_segment_prepare_reuses_every_board_without_pixel_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": str(root), "task_id": "multi-direct", "reuse": False})()
            )
            data = generation_manifest.load_manifest(manifest)
            data["userConfig"]["duration"] = 18
            data["benchmarkVideo"]["analysis"] = {"recommendedSegments": [
                {"segmentId": 1, "globalStart": 0, "globalEnd": 10},
                {"segmentId": 2, "globalStart": 10, "globalEnd": 18},
            ]}
            boards = []
            for segment_id, start, end in ((1, 0, 10), (2, 10, 18)):
                board = root / f"target-{segment_id}.png"
                board.write_bytes(f"target-bytes-{segment_id}".encode())
                anchors = [
                    {"index": index + 1, "timestamp": start + (end - start) * index / 9,
                     "anchorRole": "context", "eventType": "context",
                     "personPresent": False, "personCount": 0, "creatorIds": []}
                    for index in range(9)
                ]
                boards.append({"storyboardId": segment_id, "segmentId": segment_id, "file": str(board),
                               "globalStart": start, "globalEnd": end, "layout": "3x3", "anchors": anchors})
            data["storyboards"]["original"] = boards
            generation_manifest.save_manifest(manifest, data)

            result = prepare_prompt_handoff.prepare(manifest, root / "handoff")
            saved = generation_manifest.load_manifest(manifest)["storyboards"]["generation"]
            self.assertTrue(result["directReuse"])
            self.assertEqual(result["pixelPolicy"], "target-board-byte-for-byte")
            self.assertEqual([item["file"] for item in saved], [str((root / "target-1.png").resolve()), str((root / "target-2.png").resolve())])
            self.assertFalse((root / "handoff" / "cells").exists())
            self.assertFalse((root / "handoff" / "segment-storyboard-plan.json").exists())
            self.assertEqual(saved[1]["anchors"][0]["localTimestamp"], 0.0)

    def test_build_plan_infers_windows_and_preserves_generic_entity_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            board = root / "board.png"
            board.write_bytes(b"board")
            cells = root / "cells"
            cells.mkdir()
            anchors = []
            for index in range(9):
                (cells / f"cell-{index + 1:02d}.png").write_bytes(b"cell")
                anchors.append({
                    "timestamp": float(index),
                    "anchorRole": "hard" if index == 0 else "context",
                    "eventType": "first_frame" if index == 0 else "context",
                    "productPresent": True,
                    "productVisibility": "full",
                    "productCount": 1,
                    "personPresent": True,
                    "personExtent": "partial",
                    "personCount": 1,
                    "creatorIds": [],
                    "interactionState": f"state-{index}",
                    "semanticSource": "unobserved",
                })
            manifest = {
                "benchmarkVideo": {"analysis": {"recommendedSegments": [{"segmentId": 1, "globalStart": 0, "globalEnd": 9}]}},
                "userConfig": {"duration": 9},
                "storyboards": {"original": [{"storyboardId": 1, "file": str(board), "anchors": anchors}], "edited": []},
            }
            plan = prepare_prompt_handoff.build_plan(manifest, {1: cells})
            segment = plan["segments"][0]
            self.assertEqual((segment["globalStart"], segment["globalEnd"]), (0.0, 9.0))
            self.assertEqual(segment["cells"][5]["interactionState"], "state-5")
            self.assertEqual(segment["cells"][5]["semanticSource"], "unobserved")
            self.assertEqual(segment["cells"][0]["globalTimestamp"], 0.0)
            self.assertEqual(len(segment["cells"]), 9)

    def test_rejects_unverified_replacement(self):
        manifest = {
            "storyboards": {
                "original": [{"storyboardId": 1, "file": "raw.png", "anchors": [{}] * 9}],
                "edited": [{"storyboardId": 1, "file": "target.png", "anchors": [{}] * 9,
                            "replacement": {"applied": True}, "replacementVerified": False}],
            }
        }
        with self.assertRaises(ValueError):
            prepare_prompt_handoff._selected_boards(manifest)

    def test_rejects_product_replacement_when_product_image_was_not_registered(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw.png"
            target = root / "target.png"
            replacement_map = root / "replacement-map.json"
            raw.write_bytes(b"raw")
            target.write_bytes(b"target")
            replacement_map.write_text(json.dumps({"replaceProduct": True}), encoding="utf-8")
            manifest = {
                "skillVersion": "6.1",
                "product": {"useBenchmarkProduct": True, "productImages": []},
                "creators": [],
                "storyboards": {
                    "original": [{"storyboardId": 1, "file": str(raw), "anchors": []}],
                    "edited": [{
                        "storyboardId": 1,
                        "file": str(target),
                        "anchors": [],
                        "replacement": {"applied": True, "mapFile": str(replacement_map)},
                        "replacementVerified": True,
                    }],
                },
            }
            with self.assertRaisesRegex(ValueError, "set-product-references"):
                prepare_prompt_handoff._selected_boards(manifest)

    def test_v62_rejects_visible_person_without_registered_identity(self):
        manifest = {
            "skillVersion": "6.2",
            "creators": [],
            "storyboards": {
                "original": [{
                    "storyboardId": 1,
                    "file": "raw.png",
                    "anchors": [{
                        "personPresent": True,
                        "personCount": 1,
                        "creatorIds": [],
                    }] + [{
                        "personPresent": False,
                        "personCount": 0,
                        "creatorIds": [],
                    } for _ in range(8)],
                }],
                "edited": [],
            },
        }
        with self.assertRaisesRegex(ValueError, "未绑定全部creatorIds"):
            prepare_prompt_handoff._selected_boards(manifest)


if __name__ == "__main__":
    unittest.main()
