from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import generation_manifest  # noqa: E402


class ServerAudioSourceTests(unittest.TestCase):
    def _manifest(self, root: str) -> Path:
        return generation_manifest.command_init(
            type("Args", (), {"output_root": root, "task_id": "task", "reuse": False})()
        )

    def test_analysis_does_not_create_audio_review_state(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = self._manifest(td)
            analysis = Path(td) / "analysis.json"
            analysis.write_text(json.dumps({"media": {"hasAudio": True}}), encoding="utf-8")
            generation_manifest.set_benchmark_analysis(manifest, str(analysis))
            self.assertNotIn("audioReview", generation_manifest.load_manifest(manifest))

    def test_prompt_validation_uses_server_blueprint_not_local_audio_gate(self):
        data = {
            "benchmarkVideo": {"analysis": {"media": {"hasAudio": True}}},
            "storyboards": {"generation": [{"storyboardId": 1}]},
        }
        prompts = {
            "segments": [{
                "segmentId": 1,
                "duration": 10,
                "storyboardIds": [1],
                "prompt": "参考素材职责：声音由服务端蓝图提供",
            }]
        }
        generation_manifest.validate_prompt_record(prompts, data)


if __name__ == "__main__":
    unittest.main()
