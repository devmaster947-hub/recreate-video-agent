from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from scripts import server_video_analysis as analysis
from scripts import local_video_cli


class LuluLabContractTests(unittest.TestCase):
    def test_upload_expiration_maps_to_existing_workflow_contract(self):
        value = analysis.extract_media({"url": "https://asset.example/video.mp4", "mimeType": "video/mp4", "expiresAt": "expiry"}, "video/mp4")
        self.assertEqual(value["expiredAt"], "expiry")

    def test_remote_auth_uses_documented_user_command(self):
        with patch.object(analysis, "run_cli", return_value={"ledgerType": "Credits", "balance": 0}) as run:
            analysis.verify_key("lululab", "test-key")
        run.assert_called_once_with("lululab", "test-key", ["user", "--credits"], 30)

    def test_unknown_auth_json_cannot_pass_preflight(self):
        for response in ({}, {"ledgerType": "Credits", "balance": True}, {"errorMessage": "failed"}):
            with self.subTest(response=response), patch.object(analysis, "run_cli", return_value=response):
                with self.assertRaises(analysis.AnalysisError):
                    analysis.verify_key("lululab", "test-key")

    def test_authentication_failure_is_redacted(self):
        completed = CompletedProcess([], 1, "", "Invalid key: test-key")
        with patch.object(analysis.subprocess, "run", return_value=completed):
            with self.assertRaises(analysis.AnalysisError) as raised:
                analysis.run_cli("lululab", "test-key", ["user", "--credits"], 30)
        self.assertNotIn("test-key", str(raised.exception))

    def test_image_recovery_uses_task_fetch_without_workflow_or_upload(self):
        with patch.object(local_video_cli, "run_lululab_cli", return_value={"id": "task-1"}) as run:
            local_video_cli.fetch_lululab_image("task-1")
        run.assert_called_once_with(["task", "fetch", "--id", "task-1"], timeout=300)

    def test_task_submission_uses_documented_command_and_flag_position(self):
        response = CompletedProcess([], 0, json.dumps({"id": "task-1", "status": "Created"}), "")
        with patch.object(analysis.subprocess, "run", return_value=response) as run:
            result = analysis.run_cli("lululab", "test-key", ["task", "submit", "--workflow-id", "RecreateVideoPromptV3", "--input", "{}"], 30)
        self.assertEqual(result["id"], "task-1")
        self.assertEqual(run.call_args.args[0][1:], ["lululab", "task", "submit", "--workflow-id", "RecreateVideoPromptV3", "--input", "{}"])
        self.assertEqual(run.call_args.kwargs["env"]["LULULAB_API_KEY"], "test-key")

    def test_lululab_does_not_reuse_old_service_key(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old_config = root / ".recreate-video" / "config.json"
            old_config.parent.mkdir()
            old_config.write_text('{"apiKey":"old-key"}')
            with patch.object(analysis.Path, "home", return_value=root), patch.dict(os.environ, {"LULULAB_API_KEY": "", "LZSTUDIO_API_KEY": "old-env", "RECREATE_VIDEO_API_KEY": "old-generic"}):
                with self.assertRaises(analysis.AnalysisError):
                    analysis.load_key()


if __name__ == "__main__":
    unittest.main()
