from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.compress_benchmark_video import prepare_benchmark_video, prepare_upload_image


class Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class BenchmarkCompressionTests(unittest.TestCase):
    def test_file_at_or_below_limit_is_returned_without_ffmpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "benchmark.mp4"
            source.write_bytes(b"x" * 30)
            result = prepare_benchmark_video(
                source,
                max_bytes=30,
                runner=lambda *args, **kwargs: self.fail("FFmpeg must not run"),
            )
        self.assertFalse(result["compressed"])
        self.assertEqual(result["filePath"], str(source.resolve()))
        self.assertEqual(result["uploadBytes"], 30)

    def test_oversized_file_is_compressed_and_verified_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "benchmark.mov"
            source.write_bytes(b"o" * 100)
            ffmpeg = root / "ffmpeg"
            ffmpeg.write_bytes(b"binary")
            ffmpeg.chmod(0o700)

            def fake_run(command, **kwargs):
                if "-hide_banner" in command:
                    return Completed(returncode=1, stderr="Duration: 00:00:10.00")
                if command[command.index("-pass") + 1] == "2":
                    Path(command[-1]).write_bytes(b"c" * 80)
                return Completed()

            result = prepare_benchmark_video(
                source,
                output_dir=root / "compressed",
                max_bytes=90,
                ffmpeg_path=ffmpeg,
                runner=fake_run,
            )
            output = Path(result["filePath"])
            self.assertEqual(source.read_bytes(), b"o" * 100)
            self.assertEqual(output.stat().st_size, 80)
        self.assertTrue(result["compressed"])
        self.assertLessEqual(result["uploadBytes"], 90)

    def test_skill_requires_benchmark_aware_upload(self):
        skill = (Path(__file__).parents[1] / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("upload_file(path, benchmark=True)", skill)
        self.assertIn("20_000_000", skill)
        self.assertIn("不得回退上传原文件", skill)

    def test_oversized_image_is_compressed_and_verified_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "product.png"
            source.write_bytes(b"o" * 100)
            ffmpeg = root / "ffmpeg"
            ffmpeg.write_bytes(b"binary")
            ffmpeg.chmod(0o700)

            def fake_run(command, **kwargs):
                Path(command[-1]).write_bytes(b"c" * 70)
                return Completed()

            result = prepare_upload_image(
                source,
                output_dir=root / "compressed",
                max_bytes=80,
                ffmpeg_path=ffmpeg,
                runner=fake_run,
            )
            output = Path(result["filePath"])
            self.assertEqual(source.read_bytes(), b"o" * 100)
            self.assertEqual(output.suffix, ".jpg")
            self.assertEqual(output.stat().st_size, 70)
        self.assertTrue(result["compressed"])
        self.assertLessEqual(result["uploadBytes"], 80)


if __name__ == "__main__":
    unittest.main()
