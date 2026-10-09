from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import generation_manifest, video_cli_preflight

class VideoCliPreflightTests(unittest.TestCase):
    def check(self, available):
        with tempfile.TemporaryDirectory() as td:
            manifest = generation_manifest.command_init(type('Args', (), {'output_root':td,'task_id':'task','reuse':False})())
            with patch.object(video_cli_preflight.local_video_cli, 'lululab_cli_available', return_value=available), patch.object(video_cli_preflight.local_video_cli, 'resolve_lululab_cli', return_value=Path('/fake/lululab')), patch.object(video_cli_preflight.local_video_cli, 'libtv_cli_available', side_effect=AssertionError('must not check another platform')):
                return video_cli_preflight.inspect(Path(manifest))
    def test_lululab_is_the_only_default_provider(self):
        result = self.check(True)
        self.assertEqual(result['priority'], ['lululab_cli'])
        self.assertEqual(result['selected'], 'lululab_cli')
        self.assertEqual(result['model'], 'seedance-2-mini')
    def test_unavailable_lululab_does_not_fall_back(self):
        result = self.check(False)
        self.assertIsNone(result['selected'])
        self.assertEqual(result['availability'], {'lululab_cli':False})
