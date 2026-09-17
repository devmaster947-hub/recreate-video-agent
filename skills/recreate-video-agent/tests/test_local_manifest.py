from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import generation_manifest  # noqa: E402


class LocalManifestTests(unittest.TestCase):
    def test_new_manifest_uses_local_agent_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": td, "task_id": "task", "reuse": False})()
            )
            data = generation_manifest.load_manifest(manifest)
            self.assertEqual(data["userConfig"]["imageProvider"], "agent_local")
            self.assertEqual(data["videoBlueprint"]["source"], "local_agent")
            self.assertEqual(data["videoGeneration"]["status"], "not_started")

    def test_set_video_blueprint_registers_local_file(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": td, "task_id": "task", "reuse": False})()
            )
            source = Path(td) / "blueprint.json"
            source.write_text(json.dumps({"videoBlueprint": {"基础信息": {"原视频时长": "10秒"}}}, ensure_ascii=False), encoding="utf-8")
            generation_manifest.set_video_blueprint(manifest, str(source))
            data = generation_manifest.load_manifest(manifest)
            self.assertEqual(data["videoBlueprint"], {"source": "local_agent", "file": str(source.resolve())})

    def test_set_product_references_switches_from_benchmark_and_registers_files(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": td, "task_id": "task", "reuse": False})()
            )
            image = Path(td) / "product.png"
            image.write_bytes(b"product")
            generation_manifest.set_product_references(manifest, [str(image), str(image)])
            product = generation_manifest.load_manifest(manifest)["product"]
            self.assertFalse(product["useBenchmarkProduct"])
            self.assertEqual(product["mode"], "replacement")
            self.assertEqual(product["productImages"], [{"file": str(image.resolve())}])

    def test_creator_registration_rejects_source_frame_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": td, "task_id": "task", "reuse": False})()
            )
            image = Path(td) / "creator.png"
            image.write_bytes(b"creator")
            with self.assertRaisesRegex(ValueError, "禁止登记原视频抽帧"):
                generation_manifest.add_creator(manifest, str(image), "person-a", "source_video_frame")

    def test_generated_creator_requires_complete_product_free_multiview(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": td, "task_id": "task", "reuse": False})()
            )
            image = Path(td) / "creator.png"
            image.write_bytes(b"creator")
            with self.assertRaisesRegex(ValueError, "缺少角度"):
                generation_manifest.add_creator(
                    manifest, str(image), "person-a", "generated_multiview",
                    ["front", "back"], True,
                )
            generation_manifest.add_creator(
                manifest, str(image), "person-a", "generated_multiview",
                ["front", "side_profile", "back"], True,
            )
            creator = generation_manifest.load_manifest(manifest)["creators"][0]
            self.assertEqual(creator["sourceType"], "generated_multiview")
            self.assertEqual(creator["views"], ["back", "front", "side_profile"])
            self.assertTrue(creator["productFreeVerified"])

    def test_generated_creator_accepts_one_legacy_named_side_profile(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": td, "task_id": "task", "reuse": False})()
            )
            image = Path(td) / "creator.png"
            image.write_bytes(b"creator")
            generation_manifest.add_creator(
                manifest, str(image), "person-a", "generated_multiview",
                ["front", "left_profile", "back"], True,
            )


if __name__ == "__main__":
    unittest.main()
