from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import generation_manifest, libtv_batch_generate, reference_audit, storyboard  # noqa: E402


class FakeModelClient:
    def call(self, *arguments: str):
        if arguments[:2] != ("model", "search"):
            raise AssertionError(arguments)
        return {
            "matches": [{
                "modelKey": "star-video2-mini",
                "modelName": "Seedance 2.0 Mini",
            }]
        }


class V102RegressionTests(unittest.TestCase):
    def test_skill_version_falls_back_to_frontmatter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "SKILL.md").write_text(
                "---\nname: recreate-video-agent\nversion: 1.0.2\n---\n# 复刻爆款视频\n",
                encoding="utf-8",
            )
            self.assertEqual(generation_manifest.skill_version(root), "1.0.2")

    def test_skillhub_versions_and_unknown_use_current_policy(self):
        for value in ("", "unknown", "1.0.1", "1.0.2"):
            with self.subTest(value=value):
                self.assertTrue(reference_audit.current_or_legacy_at_least(value, 6, 5))
        self.assertFalse(reference_audit.current_or_legacy_at_least("4.2", 6, 5))

    def test_plain_text_anchor_file_is_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "anchors.txt"
            path.write_text("0\n1.25\n2.5\n", encoding="utf-8")
            payload = storyboard.load_anchor_payload(path)
            self.assertEqual([item["timestamp"] for item in payload], [0.0, 1.25, 2.5])

    def test_model_ids_and_keys_map_to_libtv_display_names(self):
        cases = {
            "seedance-2-mini": "Seedance 2.0 Mini",
            "star-video2-mini": "Seedance 2.0 Mini",
            "seedance-2-fast": "Seedance 2.0 Fast VIP",
            "star-video2-fast": "Seedance 2.0 Fast VIP",
            "seedance-2": "Seedance 2.0 VIP",
            "seedance-2-5": "Seedance 2.5",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(libtv_batch_generate.default_libtv_model(value), expected)

    def test_model_search_resolves_by_model_key(self):
        self.assertEqual(
            libtv_batch_generate.resolve_libtv_model(FakeModelClient(), "seedance-2-mini"),
            "Seedance 2.0 Mini",
        )

    def test_workspace_and_nested_project_ids_are_supported(self):
        client = libtv_batch_generate.LibTVClient(Path("libtv"), Path("."))
        calls = []

        def fake_call(*arguments: str):
            calls.append(arguments)
            if arguments[:2] == ("workspace", "create"):
                return {"workspace": {"id": 42}}
            if arguments[:2] == ("project", "create"):
                return {"projectMeta": {"uuid": "project-uuid"}}
            raise AssertionError(arguments)

        client.call = fake_call  # type: ignore[method-assign]
        self.assertEqual(client.create_workspace("task", "created by test"), 42)
        self.assertEqual(client.create_project("task", 42), "project-uuid")
        self.assertEqual(calls[1][-2:], ("--workspace", "42"))


if __name__ == "__main__":
    unittest.main()
