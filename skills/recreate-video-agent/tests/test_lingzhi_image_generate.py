from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import lingzhi_image_generate, local_video_cli  # noqa: E402


class LingzhiImageGenerateTests(unittest.TestCase):
    def args(self, root: Path, **overrides):
        values = {
            "prompt": "make an image",
            "prompt_file": None,
            "reference_image": [],
            "aspect_ratio": "9:16",
            "output": str(root / "image.png"),
            "report": str(root / "report.json"),
            "resume_task_id": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_submit_saves_task_id_and_success_result(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / "image.png"
            with patch.object(local_video_cli, "submit_lingzhi_image", return_value="task-1"), patch.object(
                local_video_cli, "poll_task", return_value={"status": "success", "url": "https://example.com/image.png"}
            ), patch.object(local_video_cli, "download_media", return_value=output):
                code, result = lingzhi_image_generate.execute(self.args(root))
            self.assertEqual(code, 0)
            self.assertEqual(result["taskId"], "task-1")
            self.assertEqual(result["model"], "gpt-image-2")
            self.assertEqual(result["resolution"], "1K")
            saved = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "submitted")
            self.assertEqual(saved["taskId"], "task-1")

    def test_resume_does_not_submit_again(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report = root / "report.json"
            report.write_text(json.dumps({"status": "submitted", "taskId": "task-1"}), encoding="utf-8")
            output = root / "image.png"
            with patch.object(local_video_cli, "submit_lingzhi_image", side_effect=AssertionError("must not submit")), patch.object(
                local_video_cli, "poll_task", return_value={"status": "success", "url": "https://example.com/image.png"}
            ), patch.object(local_video_cli, "download_media", return_value=output):
                code, result = lingzhi_image_generate.execute(
                    self.args(root, resume_task_id="task-1")
                )
            self.assertEqual(code, 0)
            self.assertEqual(result["taskId"], "task-1")

    def test_ambiguous_submit_is_not_marked_retryable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(
                local_video_cli, "submit_lingzhi_image",
                side_effect=local_video_cli.LocalVideoCliError("unknown"),
            ):
                code, result = lingzhi_image_generate.execute(self.args(root))
            self.assertEqual(code, 3)
            self.assertEqual(result["status"], "submit_outcome_unknown")


if __name__ == "__main__":
    unittest.main()
