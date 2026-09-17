from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import generation_manifest, local_video_cli, server_video_analysis, service_privacy  # noqa: E402


class ServicePrivacyTests(unittest.TestCase):
    def test_payload_sanitizer_removes_nested_metering_fields(self):
        value = {
            "credits": 5,
            "output": {
                "creditsConsumed": 3,
                "remainingCredits": 7,
                "video": {"url": "https://example.test/video.mp4"},
            },
        }
        self.assertEqual(
            service_privacy.sanitize_payload(value),
            {"output": {"video": {"url": "https://example.test/video.mp4"}}},
        )

    def test_authorization_exhaustion_uses_only_support_message(self):
        error = {"status": "failed", "errorCode": "INSUFFICIENT_CREDITS", "message": "Insufficient credits: 0 left"}
        self.assertTrue(service_privacy.is_authorization_unavailable(error))
        self.assertEqual(service_privacy.public_error(error), service_privacy.SUPPORT_MESSAGE)

    def test_unrelated_error_remains_diagnostic(self):
        self.assertEqual(service_privacy.public_error("network timeout"), "network timeout")

    def test_manifest_load_migrates_nested_fields_and_registered_raw_response(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = root / "analysis" / "task.json"
            raw.parent.mkdir(parents=True)
            raw.write_text(
                json.dumps({"output": {"credits": 5, "videoBlueprint": {"ok": True}}}),
                encoding="utf-8",
            )
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps({
                    "version": "4",
                    "schemaRevision": "5.3",
                    "videoBlueprint": {
                        "rawFile": str(raw),
                        "creditsConsumed": 5,
                    },
                    "videos": [{
                        "segmentId": 1,
                        "credits": 8,
                        "attempts": [{"error": "Insufficient credits", "remainingCredits": 0}],
                    }],
                }),
                encoding="utf-8",
            )
            loaded = generation_manifest.load_manifest(manifest)
            persisted = json.loads(manifest.read_text(encoding="utf-8"))
            raw_persisted = json.loads(raw.read_text(encoding="utf-8"))
            for value in (loaded, persisted, raw_persisted):
                serialized = json.dumps(value, ensure_ascii=False)
                self.assertNotIn('"credits"', serialized)
                self.assertNotIn("creditsConsumed", serialized)
                self.assertNotIn("remainingCredits", serialized)
            self.assertEqual(
                loaded["videos"][0]["attempts"][0]["error"],
                service_privacy.SUPPORT_MESSAGE,
            )

    def test_server_success_output_and_artifacts_drop_metering_fields(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = generation_manifest.command_init(
                type("Args", (), {"output_root": td, "task_id": "task", "reuse": False})()
            )
            manifest_path = Path(manifest)
            result = server_video_analysis.update_success(
                manifest_path,
                "remote-task",
                manifest_path.parent / "analysis" / "request.json",
                {"status": "Succeeded", "credits": 5, "output": {"creditsConsumed": 5}},
                {"基础信息": {}},
                {"schemaVersion": "1.0", "plannerVersion": "1", "policyVersion": "1.0.0", "segments": [
                    {"segmentId": 1, "globalStart": 0, "globalEnd": 9, "duration": 9, "layout": "3x3", "anchors": []}
                ]},
            )
            self.assertNotIn("creditsConsumed", result)
            saved = generation_manifest.load_manifest(manifest_path)
            self.assertNotIn("creditsConsumed", saved["videoBlueprint"])
            raw = json.loads(Path(saved["videoBlueprint"]["rawFile"]).read_text(encoding="utf-8"))
            self.assertNotIn("credits", json.dumps(raw))

            generation_manifest.set_video(manifest_path, 1, "success", credits=42, taskId="video-task")
            migrated = generation_manifest.load_manifest(manifest_path)
            self.assertNotIn("credits", json.dumps(migrated))

    def test_local_cli_exhaustion_raises_fixed_support_error(self):
        completed = subprocess.CompletedProcess(
            args=["fake"], returncode=1, stdout="", stderr="Insufficient credits: balance=0"
        )
        with patch.object(local_video_cli.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(
                service_privacy.AuthorizationUnavailableError,
                f"^{service_privacy.SUPPORT_MESSAGE}$",
            ):
                local_video_cli._run(Path("fake"), ["query"], provider="测试")

    def test_zero_exit_error_envelope_also_uses_support_message(self):
        completed = subprocess.CompletedProcess(
            args=["fake"],
            returncode=0,
            stdout='{"success":false,"errorCode":"CREDIT_EXHAUSTED"}',
            stderr="",
        )
        with patch.object(local_video_cli.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(
                service_privacy.AuthorizationUnavailableError,
                f"^{service_privacy.SUPPORT_MESSAGE}$",
            ):
                local_video_cli._run(Path("fake"), ["query"], provider="测试")

    def test_fetch_failures_use_support_message_for_both_workflows(self):
        failed = {"status": "failed", "errorMessage": "Insufficient credits"}
        with self.assertRaisesRegex(
            service_privacy.AuthorizationUnavailableError,
            f"^{service_privacy.SUPPORT_MESSAGE}$",
        ):
            local_video_cli.poll_task(lambda _task_id: failed, "video-task", interval=0, timeout=1)
        with self.assertRaisesRegex(
            service_privacy.AuthorizationUnavailableError,
            f"^{service_privacy.SUPPORT_MESSAGE}$",
        ):
            server_video_analysis.extract_blueprint({"success": False, **failed})


if __name__ == "__main__":
    unittest.main()
