from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import local_transcribe  # noqa: E402


class LocalTranscribeTests(unittest.TestCase):
    def test_timed_speech_builds_transcript_ready_review(self):
        result = {
            "language": "en",
            "segments": [
                {
                    "start": 0.25,
                    "end": 1.75,
                    "text": "  Clean these shoes. ",
                    "words": [
                        {"start": 0.25, "end": 0.7, "word": " Clean"},
                        {"start": 0.7, "end": 1.75, "word": "these shoes."},
                    ],
                }
            ],
        }
        payload = local_transcribe.build_audio_review(
            result,
            video=Path("/tmp/source.mov"),
            backend="mlx",
            model="mlx-community/whisper-turbo",
        )["audioReview"]
        self.assertEqual(payload["status"], "transcript_ready")
        self.assertTrue(payload["speechDetected"])
        self.assertEqual(payload["segments"][0]["text"], "Clean these shoes.")
        self.assertEqual(payload["segments"][0]["words"][0]["start"], 0.25)
        self.assertFalse(payload["audioUploaded"])

    def test_empty_transcription_is_verified_no_speech(self):
        payload = local_transcribe.build_audio_review(
            {"language": "en", "segments": []},
            video=Path("/tmp/source.mov"),
            backend="mlx",
            model="mlx-community/whisper-turbo",
        )["audioReview"]
        self.assertEqual(payload["status"], "verified_no_speech")
        self.assertFalse(payload["speechDetected"])

    def test_auto_prefers_mlx_on_apple_silicon(self):
        with patch.object(local_transcribe, "available_backends", return_value={"mlx": True, "openai": True}):
            self.assertEqual(local_transcribe.resolve_backend("auto"), "mlx")

    def test_auto_falls_back_to_openai_whisper(self):
        with patch.object(local_transcribe, "available_backends", return_value={"mlx": False, "openai": True}):
            self.assertEqual(local_transcribe.resolve_backend("auto"), "openai")

    def test_no_backend_stops_with_setup_command(self):
        with patch.object(local_transcribe, "available_backends", return_value={"mlx": False, "openai": False}):
            with self.assertRaisesRegex(ValueError, "uv run --with mlx-whisper"):
                local_transcribe.resolve_backend("auto")

    def test_clean_segments_drops_invalid_entries(self):
        cleaned = local_transcribe.clean_segments(
            {
                "segments": [
                    {"start": 1, "end": 1, "text": "bad"},
                    {"start": 0, "end": 1, "text": ""},
                    {"start": 0, "end": 1.2, "text": "valid"},
                ]
            }
        )
        self.assertEqual(cleaned, [{"id": 1, "start": 0.0, "end": 1.2, "text": "valid"}])


if __name__ == "__main__":
    unittest.main()
