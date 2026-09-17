from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from scripts import generation_manifest, prepare_prompt_handoff, reference_audit, storyboard_cells


def make_board(path: Path, width: int, height: int) -> None:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    cell_width, cell_height = width // 3, height // 3
    colors = ("#9b2c2c", "#2c5282", "#276749", "#744210", "#553c9a", "#285e61")
    for index in range(9):
        row, column = divmod(index, 3)
        left, top = column * cell_width, row * cell_height
        right, bottom = (column + 1) * cell_width - 1, (row + 1) * cell_height - 1
        draw.rectangle((left, top, right, bottom), fill=colors[index % len(colors)])
        label_height = max(24, round(cell_height * .08))
        draw.rectangle((left, bottom - label_height, right, bottom), fill="black")
        draw.rectangle((left, top, right, bottom), outline="white", width=max(2, width // 300))
    image.save(path)


class StoryboardFastModeTests(unittest.TestCase):
    def test_new_manifest_defaults_fast_and_legacy_defaults_strict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": str(root), "task_id": "new", "reuse": False})()
            )
            self.assertEqual(
                generation_manifest.load_manifest(manifest)["userConfig"]["storyboardValidationMode"],
                "fast",
            )
            legacy = root / "legacy.json"
            legacy.write_text(json.dumps({"version": "4", "userConfig": {}}), encoding="utf-8")
            self.assertEqual(
                generation_manifest.load_manifest(legacy)["userConfig"]["storyboardValidationMode"],
                "strict",
            )

    def test_explicit_strict_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = generation_manifest.command_init(
                type(
                    "Args",
                    (),
                    {
                        "output_root": temporary,
                        "task_id": "strict",
                        "reuse": False,
                        "storyboard_validation_mode": "strict",
                    },
                )()
            )
            self.assertEqual(
                generation_manifest.load_manifest(manifest)["userConfig"]["storyboardValidationMode"],
                "strict",
            )

    def test_fast_prepare_restores_layout_and_writes_mechanical_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, edited, output = root / "original.png", root / "edited.png", root / "final.png"
            make_board(original, 720, 1392)
            make_board(edited, 900, 1740)
            result = storyboard_cells.fast_prepare(
                storyboard_cells.executable("ffmpeg", None),
                storyboard_cells.executable("ffprobe", None, required=False),
                original,
                edited,
                output,
            )
            self.assertEqual(result["method"], "whole-board-fast-v1")
            self.assertEqual(result["validationLevel"], "mechanical")
            self.assertFalse(result["visualReviewRequired"])
            self.assertTrue(output.is_file())
            self.assertTrue(output.with_suffix(".fast.json").is_file())
            self.assertEqual(storyboard_cells.dimensions(None, output, storyboard_cells.executable("ffmpeg", None)), (720, 1392))

    def test_fast_prepare_rejects_aspect_drift_over_fifteen_percent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, edited = root / "original.png", root / "edited.png"
            make_board(original, 720, 1392)
            make_board(edited, 1200, 1200)
            with self.assertRaisesRegex(ValueError, "15%"):
                storyboard_cells.fast_prepare(
                    storyboard_cells.executable("ffmpeg", None),
                    storyboard_cells.executable("ffprobe", None, required=False),
                    original,
                    edited,
                    root / "final.png",
                )

    def test_fast_record_is_verified_only_by_matching_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, edited, final = root / "original.png", root / "edited.png", root / "final.png"
            original.write_bytes(b"original")
            edited.write_bytes(b"edited")
            final.write_bytes(b"final")
            evidence = root / "final.fast.json"
            evidence.write_text(json.dumps({
                "method": "whole-board-fast-v1",
                "validationLevel": "mechanical",
                "layoutRestored": True,
                "labelsRestored": True,
                "originalFile": str(original),
                "originalSha256": generation_manifest.file_sha256(original),
                "editedSha256": generation_manifest.file_sha256(edited),
                "finalSha256": generation_manifest.file_sha256(final),
            }), encoding="utf-8")
            record = generation_manifest.normalize_replacement_record({
                "method": "whole-board-fast-v1",
                "validationLevel": "mechanical",
                "applied": True,
                "imageEditSucceeded": True,
                "generationAttempts": 1,
                "maxImageEditAttempts": 1,
                "editedImageFile": str(edited),
                "finalImageFile": str(final),
                "fastPrepareFile": str(evidence),
                "replaceProduct": True,
            })
            self.assertTrue(record["replacementVerified"])
            self.assertTrue(record["artifactIntegrityVerified"])
            final.write_bytes(b"tampered")
            self.assertFalse(
                generation_manifest.normalize_replacement_record({**record, "replacementVerified": False})[
                    "replacementVerified"
                ]
            )

    def test_fast_product_replacement_still_requires_registered_product(self):
        board = {
            "storyboardId": 1,
            "replacement": {
                "method": "whole-board-fast-v1",
                "applied": True,
                "replaceProduct": True,
            },
        }
        self.assertTrue(reference_audit.replacement_targets_product(board))
        with self.assertRaisesRegex(ValueError, "set-product-references"):
            reference_audit.validate_product_references(
                {"product": {"useBenchmarkProduct": True, "productImages": []}}, board
            )

    def test_fast_board_is_accepted_only_in_fast_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, edited, final = root / "original.png", root / "edited.png", root / "final.png"
            original.write_bytes(b"original")
            edited.write_bytes(b"edited")
            final.write_bytes(b"final")
            evidence = root / "final.fast.json"
            evidence.write_text(json.dumps({
                "method": "whole-board-fast-v1",
                "validationLevel": "mechanical",
                "layoutRestored": True,
                "labelsRestored": True,
                "originalFile": str(original),
                "originalSha256": generation_manifest.file_sha256(original),
                "editedSha256": generation_manifest.file_sha256(edited),
                "finalSha256": generation_manifest.file_sha256(final),
            }), encoding="utf-8")
            replacement = generation_manifest.normalize_replacement_record({
                "method": "whole-board-fast-v1",
                "validationLevel": "mechanical",
                "applied": True,
                "imageEditSucceeded": True,
                "generationAttempts": 1,
                "maxImageEditAttempts": 1,
                "editedImageFile": str(edited),
                "finalImageFile": str(final),
                "fastPrepareFile": str(evidence),
                "replaceProduct": False,
            })
            anchors = [
                {"personPresent": False, "personCount": 0, "creatorIds": []}
                for _ in range(9)
            ]
            manifest = {
                "skillVersion": "5.0",
                "userConfig": {"storyboardValidationMode": "fast"},
                "product": {"useBenchmarkProduct": True, "productImages": []},
                "creators": [],
                "storyboards": {
                    "original": [{"storyboardId": 1, "file": str(original), "anchors": anchors}],
                    "edited": [{
                        "storyboardId": 1,
                        "file": str(final),
                        "anchors": anchors,
                        "replacement": replacement,
                        "replacementVerified": True,
                    }],
                },
            }
            self.assertEqual(len(prepare_prompt_handoff._selected_boards(manifest)), 1)
            manifest["userConfig"]["storyboardValidationMode"] = "strict"
            with self.assertRaisesRegex(ValueError, "strict"):
                prepare_prompt_handoff._selected_boards(manifest)


if __name__ == "__main__":
    unittest.main()
