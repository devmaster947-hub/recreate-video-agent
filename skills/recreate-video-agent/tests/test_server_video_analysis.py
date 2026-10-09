from pathlib import Path
import json
import unittest
from unittest.mock import patch

from scripts.server_video_analysis import (
    AnalysisError,
    acquire_run_lock,
    build_input,
    bundled_cli_candidate,
    extract_blueprint,
    extract_replication_plan,
    load_submit_attempts,
    load_submit_receipt,
    media_from_url,
    merged_attempt_history,
    poll_task,
    persist_submit_receipt,
    raw_storyboard_specs,
    release_run_lock,
    reserve_submission,
    submit_attempt,
)


def manifest_with_raw_board(tmp_path: Path) -> dict:
    board = tmp_path / "segment-01-storyboard-3x3.png"
    board.write_bytes(b"png")
    return {
        "benchmarkVideo": {
            "analysis": {
                "recommendedSegments": [
                    {"segmentId": 1, "globalStart": 0, "globalEnd": 9, "duration": 9}
                ]
            }
        },
        "userConfig": {
            "videoModel": "seedance-2-fast",
            "durationMode": "source",
            "targetCountry": "跟原视频一致",
            "targetLanguage": "跟原视频一致",
        },
        "storyboards": {
            "original": [{"storyboardId": 1, "file": str(board)}]
        },
        "product": {"productBrief": {"name": "must not be sent"}},
        "creatorBrief": {"name": "must not be sent"},
    }


class ServerVideoAnalysisTests(unittest.TestCase):
    def test_blueprint_is_accepted_without_schema_or_speaker_validation(self) -> None:
        blueprint = {
            "逐镜头拆解": [],
            "声音结构": {"voiceProfiles": [{"speakerId": "voice-only"}]},
            "视频元素": {"人物": []},
        }
        plan = {"schemaVersion": "1.0", "plannerVersion": "1", "segments": [{"segmentId": 1, "globalStart": 0, "globalEnd": 9, "duration": 9, "anchors": [{"timestamp": 0}]}]}
        response = {"status": "succeeded", "videoBlueprint": blueprint, "replicationPlan": plan}
        self.assertEqual(extract_blueprint(response), blueprint)
        self.assertEqual(extract_replication_plan(response), plan)

    def test_direct_benchmark_url_media_shape(self) -> None:
        media = media_from_url("https://asset.example.com/video.mp4")
        self.assertEqual(media["url"], "https://asset.example.com/video.mp4")
        self.assertEqual(media["mimeType"], "video/mp4")
        self.assertIsNone(media["expiredAt"])
        with self.assertRaisesRegex(AnalysisError, "HTTP"):
            media_from_url("file:///tmp/video.mp4")

    def test_windows_x64_uses_bundled_cli(self) -> None:
        path = bundled_cli_candidate("Windows", "AMD64")
        self.assertIsNotNone(path)
        self.assertEqual(path.name, "lululab.exe")
        self.assertTrue(path.is_file())

    def test_run_lock_rejects_second_process_for_same_manifest(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as td:
            manifest_path = Path(td) / "manifest.json"
            manifest_path.write_text('{"version": 4}\n', encoding="utf-8")
            first = acquire_run_lock(manifest_path)
            try:
                with self.assertRaisesRegex(AnalysisError, "禁止启动第二个"):
                    acquire_run_lock(manifest_path)
            finally:
                release_run_lock(first)
            second = acquire_run_lock(manifest_path)
            release_run_lock(second)

    def test_reservation_and_submit_receipt_fail_closed(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as td:
            manifest_path = Path(td) / "manifest.json"
            manifest_path.write_text('{"version": 4}\n', encoding="utf-8")
            request_path = Path(td) / "analysis" / "request.json"
            reserve_submission(manifest_path, request_path)
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["videoBlueprint"]["status"], "preparing_submission")
            with self.assertRaisesRegex(AnalysisError, "禁止创建第二个"):
                reserve_submission(manifest_path, request_path)
            persist_submit_receipt(manifest_path, "task-once")
            self.assertEqual(load_submit_receipt(manifest_path), "task-once")

    def test_submit_receipt_keeps_exactly_two_attempts(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as td:
            manifest_path = Path(td) / "manifest.json"
            manifest_path.write_text('{"version": 4}\n', encoding="utf-8")
            persist_submit_receipt(manifest_path, "task-first")
            persist_submit_receipt(manifest_path, "task-retry")
            self.assertEqual(
                [item["taskId"] for item in load_submit_attempts(manifest_path)],
                ["task-first", "task-retry"],
            )
            self.assertEqual(load_submit_receipt(manifest_path), "task-retry")
            with self.assertRaisesRegex(AnalysisError, "最大尝试次数"):
                persist_submit_receipt(manifest_path, "task-third")

    def test_legacy_manifest_recovers_both_attempts_from_receipt(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as td:
            manifest_path = Path(td) / "manifest.json"
            manifest_path.write_text('{"version": 4}\n', encoding="utf-8")
            persist_submit_receipt(manifest_path, "task-first")
            persist_submit_receipt(manifest_path, "task-retry")
            history = merged_attempt_history(manifest_path, [])
            self.assertEqual(
                [item["taskId"] for item in history],
                ["task-first", "task-retry"],
            )

    def test_terminal_failure_returns_to_caller_for_one_retry(self) -> None:
        failed = {"status": "Failed", "message": "Task timed out."}
        with patch("scripts.server_video_analysis.run_cli", return_value=failed):
            self.assertEqual(
                poll_task("cli", "key", "task-first", poll_interval=0, poll_timeout=1),
                failed,
            )

    def test_submit_attempt_stops_before_third_remote_submit(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as td:
            manifest_path = Path(td) / "manifest.json"
            manifest_path.write_text('{"version": 4}\n', encoding="utf-8")
            request_path = Path(td) / "analysis" / "request.json"
            payload = {"benchmarkVideo": {"url": "https://example.test/video.mp4"}}
            media = {"url": "https://example.test/video.mp4", "mimeType": "video/mp4"}
            with patch(
                "scripts.server_video_analysis.run_cli",
                side_effect=[{"id": "task-first"}, {"id": "task-retry"}],
            ) as mocked:
                submit_attempt("cli", "key", manifest_path, request_path, payload, media, [])
                submit_attempt("cli", "key", manifest_path, request_path, payload, media, [])
                with self.assertRaisesRegex(AnalysisError, "最大尝试次数"):
                    submit_attempt("cli", "key", manifest_path, request_path, payload, media, [])
                self.assertEqual(mocked.call_count, 2)
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [item["taskId"] for item in saved["videoBlueprint"]["attempts"]],
                ["task-first", "task-retry"],
            )

    def test_build_input_nests_raw_storyboards_and_omits_briefs(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as td:
            manifest = manifest_with_raw_board(Path(td))
            specs = raw_storyboard_specs(manifest)
            raw = [{key: value for key, value in specs[0].items() if key != "filePath"}]
            raw[0].update({"url": "https://example.com/raw.png", "expiredAt": None})
            payload = build_input(
                manifest,
                {"url": "https://example.com/video.mp4", "mimeType": "video/mp4", "expiredAt": None},
                raw,
            )
            self.assertNotIn("rawStoryboards", payload["userConfig"])
            self.assertEqual(payload["plannerVersion"], "1")
            self.assertEqual(payload["userConfig"]["blueprintSchemaVersion"], "6.0")
            self.assertIn("sourceDuration", payload["userConfig"])
            self.assertIn("targetDurationSource", payload["userConfig"])
            manifest["storyboards"]["original"] = []
            self.assertIn("benchmarkVideo", build_input(manifest, {"url":"https://example.com/video.mp4"}))
            self.assertNotIn("productBrief", payload)
            self.assertNotIn("creatorBrief", payload)

    def test_raw_storyboards_are_required(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as td:
            manifest = manifest_with_raw_board(Path(td))
            manifest["storyboards"]["original"] = []
            with self.assertRaisesRegex(AnalysisError, "raw Storyboard"):
                raw_storyboard_specs(manifest)


if __name__ == "__main__":
    unittest.main()
