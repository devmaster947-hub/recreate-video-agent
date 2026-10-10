from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import task_status


class SavedTaskStatusTests(unittest.TestCase):
    def test_finished_file_wins_over_stale_pending_message(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "final.mp4"
            video.write_bytes(b"video")
            report = root / "quality.json"
            report.write_text(json.dumps({
                "technicalPassed": True,
                "semanticStatus": "not_reviewed",
                "deliveryBlocked": False,
            }))
            state = task_status.summarize({
                "workflowStatus": "querying",
                "finalVideo": {"file": str(video), "qualityReport": str(report)},
                "videos": [{"segmentId": 1, "status": "querying", "taskId": "saved-id"}],
            })
            self.assertEqual(state["status"], "video_ready")
            self.assertTrue(state["deliverableReady"])
            self.assertEqual(state["quality"]["technicalPassed"], True)
            self.assertEqual(state["quality"]["semanticStatus"], "not_reviewed")

    def test_saved_task_id_is_reported_without_reupload(self):
        state = task_status.summarize({
            "videos": [{"segmentId": 1, "status": "querying", "taskId": "existing-id"}],
        })
        self.assertEqual(state["status"], "video_task_pending")
        self.assertEqual(state["segments"][0]["taskId"], "existing-id")
        self.assertFalse(state["deliverableReady"])

    def test_missing_final_file_is_not_reported_as_deliverable(self):
        state = task_status.summarize({
            "workflowStatus": "complete",
            "finalVideo": {"file": "/not/a/real/final.mp4"},
        })
        self.assertEqual(state["status"], "final_file_missing")
        self.assertFalse(state["deliverableReady"])

    def test_completed_analysis_is_reused(self):
        with tempfile.TemporaryDirectory() as td:
            result = Path(td) / "blueprint.json"
            result.write_text("{}")
            state = task_status.summarize({
                "videoBlueprint": {"status": "succeeded", "taskId": "analysis-id", "file": str(result)},
            })
            self.assertEqual(state["status"], "analysis_ready")
            self.assertEqual(state["analysis"]["taskId"], "analysis-id")


if __name__ == "__main__":
    unittest.main()
