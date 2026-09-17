import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_prompt_texts.py"


class ExportPromptTextsTest(unittest.TestCase):
    def test_exports_prompt_body_without_display_wrappers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            task = Path(temp_dir)
            manifest = task / "manifest.json"
            first = "第一段正文\n第二行"
            second = "Second prompt with `markdown` characters."
            manifest.write_text(
                json.dumps(
                    {
                        "videoPrompts": {
                            "segments": [
                                {"segmentId": 1, "title": "标题一", "duration": 11, "prompt": first},
                                {"segmentId": 2, "title": "Title 2", "duration": 9, "prompt": second},
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(manifest)],
                check=True,
                capture_output=True,
                text=True,
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertEqual(
                (task / "prompts" / "segment-01-prompt.txt").read_text(encoding="utf-8"),
                first,
            )
            self.assertEqual(
                (task / "prompts" / "segment-02-prompt.txt").read_text(encoding="utf-8"),
                second,
            )
            self.assertNotIn("Segment 1", first)


if __name__ == "__main__":
    unittest.main()
