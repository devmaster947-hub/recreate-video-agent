from __future__ import annotations

import unittest
from pathlib import Path
import sys
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import concat_videos


class FfmpegPathTests(unittest.TestCase):
    def test_windows_backslash_path_is_not_searched_on_path(self):
        ffmpeg = r"C:\ffmpeg\bin\ffmpeg.exe"
        with patch("concat_videos.shutil.which") as mocked_which:
            with patch(
                "concat_videos.generation_manifest.load_manifest",
                return_value={"segments": {"1": {"status": "success"}}},
            ):
                with patch(
                    "concat_videos.ordered_segments",
                    return_value=[(1, Path("segment.mp4"))],
                ):
                    with patch("concat_videos.render") as mocked_render:
                        with patch(
                            "concat_videos.generation_manifest.command_set_final"
                        ):
                            with patch.object(
                                sys,
                                "argv",
                                [
                                    "concat_videos.py",
                                    "--manifest",
                                    "manifest.json",
                                    "--output",
                                    "final.mp4",
                                    "--ffmpeg",
                                    ffmpeg,
                                ],
                            ):
                                self.assertEqual(concat_videos.main(), 0)
        mocked_which.assert_not_called()
        self.assertEqual(mocked_render.call_args.args[2], ffmpeg)


if __name__ == "__main__":
    unittest.main()
