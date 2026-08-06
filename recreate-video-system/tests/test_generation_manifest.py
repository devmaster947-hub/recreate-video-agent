from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import generation_manifest as manifest


class PublicMediaReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.product = self._file("product.png")
        self.creator = self._file("creator.png")
        self.storyboard_1 = self._file("storyboard-1.png")
        self.storyboard_2 = self._file("storyboard-2.png")
        self.at = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temporary.cleanup()

    def _file(self, name: str) -> str:
        path = self.root / name
        path.write_bytes(b"image")
        return str(path.resolve())

    def _data(self) -> dict:
        return {
            "schema_version": 1,
            "assets": {
                "product_images": [self.product],
                "creators": {
                    "1": {
                        "image": self.creator,
                        "appears_in_segments": [1, 2],
                    }
                },
                "storyboards": {
                    "1": {"image": self.storyboard_1, "grid": "3x3"},
                    "2": {"image": self.storyboard_2, "grid": "3x3"},
                },
            },
            "public_media": {},
        }

    def test_reuses_unexpired_urls_in_reference_order(self):
        data = self._data()
        data["public_media"] = {
            self.product: {
                "url": "https://cdn.example/product.png",
                "mimeType": "image/png",
                "expiredAt": "",
            },
            self.creator: {
                "url": "https://cdn.example/creator.png",
                "mimeType": "image/png",
                "expiredAt": (self.at + timedelta(minutes=20)).isoformat(),
            },
            self.storyboard_1: {
                "url": "https://cdn.example/storyboard-1.png",
                "mimeType": "image/png",
                "expiredAt": (self.at + timedelta(minutes=11)).isoformat(),
            },
        }

        selected = manifest.select_segment_references(data, 1, at=self.at)

        self.assertEqual(
            selected["reference_urls"],
            [
                "https://cdn.example/product.png",
                "https://cdn.example/creator.png",
                "https://cdn.example/storyboard-1.png",
            ],
        )
        self.assertEqual(selected["upload_required"], [])
        self.assertEqual(
            [asset["file"] for asset in selected["ordered_assets"]],
            [self.product, self.creator, self.storyboard_1],
        )

    def test_near_expiry_expired_missing_and_invalid_urls_require_upload(self):
        data = self._data()
        data["public_media"] = {
            self.product: {
                "url": "https://cdn.example/product.png",
                "expiredAt": (self.at + timedelta(minutes=9, seconds=59)).isoformat(),
            },
            self.creator: {
                "url": "not-a-public-url",
                "expiredAt": "",
            },
            self.storyboard_1: {
                "url": "https://cdn.example/storyboard-1.png",
                "expiredAt": (self.at - timedelta(seconds=1)).isoformat(),
            },
        }

        selected = manifest.select_segment_references(data, 1, at=self.at)

        self.assertEqual(selected["reference_urls"], [])
        self.assertEqual(
            selected["upload_required"],
            [self.product, self.creator, self.storyboard_1],
        )

    def test_segment_uses_only_its_storyboard(self):
        data = self._data()

        selected = manifest.select_segment_references(data, 2, at=self.at)

        self.assertEqual(
            selected["ordered_images"],
            [self.product, self.creator, self.storyboard_2],
        )
        self.assertNotIn(self.storyboard_1, selected["ordered_images"])

    def test_set_public_media_refreshes_existing_url(self):
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(
            json.dumps({"schema_version": 1, "public_media": {}}),
            encoding="utf-8",
        )
        base = {
            "manifest": str(manifest_path),
            "file": self.product,
            "mime_type": "image/png",
            "expired_at": "",
        }
        manifest.command_set_public_media(
            argparse.Namespace(**base, url="https://cdn.example/old.png")
        )
        manifest.command_set_public_media(
            argparse.Namespace(**base, url="https://cdn.example/new.png")
        )

        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            saved["public_media"][self.product]["url"],
            "https://cdn.example/new.png",
        )

    def test_legacy_manifest_without_public_media_falls_back_to_upload(self):
        manifest_path = self.root / "legacy.json"
        data = self._data()
        data.pop("public_media")
        manifest_path.write_text(json.dumps(data), encoding="utf-8")

        loaded = manifest.load_manifest(manifest_path)
        selected = manifest.select_segment_references(loaded, 1, at=self.at)

        self.assertEqual(loaded["public_media"], {})
        self.assertEqual(
            selected["upload_required"],
            [self.product, self.creator, self.storyboard_1],
        )

    def test_seven_digit_fractional_expiration_is_supported(self):
        media = {
            "url": "https://cdn.example/product.png",
            "expiredAt": "2026-07-19T12:20:00.1234567Z",
        }
        self.assertTrue(manifest.public_media_is_usable(media, at=self.at))

    def test_legacy_manifest_gets_prompt_task_defaults(self):
        path = self.root / "legacy-prompt.json"
        path.write_text(
            json.dumps({"schema_version": 1}),
            encoding="utf-8",
        )
        loaded = manifest.load_manifest(path)
        self.assertEqual(loaded["prompt_task"]["submit_id"], "")
        self.assertIsNone(loaded["prompt_task"]["remaining_credits"])
        self.assertEqual(loaded["prompt_overrides"], manifest.prompt_override_defaults())

    def test_prompts_are_locked_by_default_and_explicit_override_is_scoped(self):
        path = self.root / "prompt-override.json"
        path.write_text(
            json.dumps({"schema_version": 1, "generation_tasks": {}, "segments": {}}),
            encoding="utf-8",
        )
        text_file = self.root / "instruction.txt"
        text_file.write_text("只把头脸改为铅笔素描。", encoding="utf-8")
        data = manifest.load_manifest(path)
        self.assertEqual(
            manifest.apply_prompt_override(data, "storyboards", 1, "original"),
            "original",
        )

        manifest.command_set_prompt_override(
            argparse.Namespace(
                manifest=str(path), target="storyboards", id=["1"], mode="append",
                text_file=str(text_file),
            )
        )
        data = manifest.load_manifest(path)
        self.assertEqual(
            manifest.apply_prompt_override(data, "storyboards", 1, "original"),
            "original\n只把头脸改为铅笔素描。",
        )
        self.assertEqual(
            manifest.apply_prompt_override(data, "videos", 1, "original"),
            "original",
        )

    def test_submitted_generation_prompt_cannot_be_changed(self):
        path = self.root / "submitted.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "generation_tasks": {
                        "storyboards": {"1": {"status": "submitted", "submit_id": "image-1"}}
                    },
                    "segments": {},
                }
            ),
            encoding="utf-8",
        )
        text_file = self.root / "instruction.txt"
        text_file.write_text("change", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "禁止中途修改"):
            manifest.command_set_prompt_override(
                argparse.Namespace(
                    manifest=str(path), target="storyboards", id=["1"], mode="append",
                    text_file=str(text_file),
                )
            )

    def test_prompt_task_id_cannot_be_replaced(self):
        path = self.root / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "prompt_task": {
                        **manifest.prompt_task_defaults(),
                        "submit_id": "task-1",
                        "status": "running",
                    },
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "禁止替换"):
            manifest.update_prompt_task(
                path,
                submit_id="task-2",
                status="running",
            )


if __name__ == "__main__":
    unittest.main()
