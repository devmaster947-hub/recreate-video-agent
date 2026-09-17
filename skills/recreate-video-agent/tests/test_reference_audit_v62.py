from __future__ import annotations

import tempfile
import unittest
import hashlib
import json
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import generation_manifest, reference_audit  # noqa: E402


def anchor(person: bool, creator_ids: list[str] | None = None) -> dict:
    return {
        "personPresent": person,
        "personCount": 1 if person else 0,
        "creatorIds": creator_ids or [],
        "productPresent": False,
    }


class ReferenceAuditV62Tests(unittest.TestCase):
    def manifest(self, root: Path, anchors: list[dict], selected: list[str]) -> dict:
        board = root / "board.png"
        board.write_bytes(b"board")
        creator = root / "creator.png"
        creator.write_bytes(b"creator")
        return {
            "skillVersion": "6.2",
            "product": {"useBenchmarkProduct": True},
            "creators": [{"creatorId": "person-a", "file": str(creator)}],
            "storyboards": {"generation": [{
                "storyboardId": 1,
                "segmentId": 1,
                "file": str(board),
                "anchors": anchors,
            }]},
            "videoPrompts": {"segments": [{
                "segmentId": 1,
                "storyboardIds": [1],
                "creatorIds": selected,
                "productPresent": False,
            }]},
        }

    def test_blocks_visible_person_without_identity_binding(self):
        with tempfile.TemporaryDirectory() as td:
            data = self.manifest(Path(td), [anchor(True)] + [anchor(False) for _ in range(8)], [])
            with self.assertRaisesRegex(ValueError, "未绑定全部creatorIds"):
                reference_audit.audit(data)

    def test_blocks_prompt_that_omits_storyboard_person(self):
        with tempfile.TemporaryDirectory() as td:
            data = self.manifest(Path(td), [anchor(True, ["person-a"])] + [anchor(False) for _ in range(8)], [])
            with self.assertRaisesRegex(ValueError, "必须与Storyboard实际出镜人物完全一致"):
                reference_audit.audit(data)

    def test_accepts_complete_preserved_identity_roster(self):
        with tempfile.TemporaryDirectory() as td:
            data = self.manifest(Path(td), [anchor(True, ["person-a"])] + [anchor(False) for _ in range(8)], ["person-a"])
            self.assertTrue(reference_audit.audit(data)["passed"])

    def test_blocks_direct_ai_board_for_new_tasks(self):
        with tempfile.TemporaryDirectory() as td:
            data = self.manifest(Path(td), [anchor(False) for _ in range(9)], [])
            data["storyboards"]["generation"][0].update({
                "replacementVerified": True,
                "replacement": {"applied": True, "method": "local-board-direct"},
            })
            with self.assertRaisesRegex(ValueError, "只允许whole-board-lock-merge"):
                reference_audit.audit(data)

    def test_region_lock_evidence_is_normalized_for_generation_gate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = root / "original.png"
            edited = root / "edited.png"
            final = root / "final.png"
            mask = root / "mask.png"
            for path, value in ((original, b"o"), (edited, b"e"), (final, b"f"), (mask, b"m")):
                path.write_bytes(value)
            cells = []
            for index in range(1, 10):
                replace = index == 1
                cells.append({
                    "index": index,
                    "interactionState": "",
                    "product": {"state": "full" if replace else "none", "count": 1 if replace else 0, "replace": replace},
                    "creator": {"state": "none", "count": 0, "replace": False},
                    "regions": {
                        "product": [[[0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]] if replace else [],
                        "creator": [],
                        "protect": [],
                    },
                })
            plan = root / "map.json"
            plan.write_text(json.dumps({
                "segmentId": 1,
                "replaceProduct": True,
                "replaceCreator": False,
                "mergeMode": "object-regions-v1",
                "coordinateSpace": "original-cell-visual-normalized",
                "labelHeight": 38,
                "compositionMode": "region-lock-v1",
                "editRule": "replace only the product",
                "cells": cells,
            }), encoding="utf-8")
            sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            lock = root / "final.lock.json"
            lock.write_text(json.dumps({
                "method": "whole-board-lock-merge",
                "mergeMode": "object-regions-v1",
                "compositionMode": "region-lock-v1",
                "editRule": "replace only the product",
                "mapValid": True,
                "lockMergeSucceeded": True,
                "frozenCellsRestored": True,
                "protectedPixelsRestored": True,
                "outsideMaskChangedPixels": 0,
                "editableCells": [1],
                "frozenCells": list(range(2, 10)),
                "originalFile": str(original),
                "maskFile": str(mask),
                "originalSha256": sha(original),
                "editedSha256": sha(edited),
                "mapSha256": sha(plan),
                "finalSha256": sha(final),
                "maskSha256": sha(mask),
            }), encoding="utf-8")
            record = generation_manifest.normalize_replacement_record({
                "applied": True,
                "method": "whole-board-lock-merge",
                "generationAttempts": 1,
                "maxImageEditAttempts": 1,
                "imageEditSucceeded": True,
                "mapValid": True,
                "lockMergeSucceeded": True,
                "frozenCellsRestored": True,
                "editedImageFile": str(edited),
                "finalImageFile": str(final),
                "mapFile": str(plan),
                "lockMergeFile": str(lock),
                "editableCells": [1],
                "frozenCells": list(range(2, 10)),
            })
            self.assertTrue(record["replacementVerified"])
            self.assertEqual(record["mergeMode"], "object-regions-v1")
            self.assertTrue(record["protectedPixelsRestored"])
            self.assertEqual(record["outsideMaskChangedPixels"], 0)


class ReferenceAuditCurrentCreatorPolicyTests(unittest.TestCase):
    def test_multi_segment_rejects_missing_creator_reference(self):
        manifest = {
            "skillVersion": "4.3",
            "creators": [],
            "videoPrompts": {"segments": [
                {"segmentId": 1, "creatorIds": ["person-a"]},
                {"segmentId": 2, "creatorIds": ["person-a"]},
            ]},
        }
        with self.assertRaisesRegex(ValueError, "多Segment任务必须"):
            reference_audit.validate_creator_references(manifest, {"person-a"})

    def test_multi_segment_accepts_complete_generated_multiview(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "multiview.png"
            image.write_bytes(b"creator")
            manifest = {
                "skillVersion": "4.3",
                "creators": [{
                    "creatorId": "person-a",
                    "file": str(image),
                    "sourceType": "generated_multiview",
                    "layout": "multi-view",
                    "views": ["front", "side_profile", "back"],
                    "productFreeVerified": True,
                }],
                "videoPrompts": {"segments": [
                    {"segmentId": 1, "creatorIds": ["person-a"]},
                    {"segmentId": 2, "creatorIds": ["person-a"]},
                ]},
            }
            result = reference_audit.validate_creator_references(manifest, {"person-a"})
            self.assertEqual(result["mode"], "multi_segment_required")
            self.assertEqual(result["creatorIds"], ["person-a"])

    def test_single_segment_rejects_generated_creator_reference(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "multiview.png"
            image.write_bytes(b"creator")
            manifest = {
                "skillVersion": "4.3",
                "creators": [{
                    "creatorId": "person-a",
                    "file": str(image),
                    "sourceType": "generated_multiview",
                    "layout": "multi-view",
                    "views": ["front", "side_profile", "back"],
                    "productFreeVerified": True,
                }],
                "videoPrompts": {"segments": [{"segmentId": 1}]},
            }
            with self.assertRaisesRegex(ValueError, "单Segment"):
                reference_audit.validate_creator_references(manifest, {"person-a"})


if __name__ == "__main__":
    unittest.main()
