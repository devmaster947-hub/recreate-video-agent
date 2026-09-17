from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from scripts import lingzhi_key_preflight
from scripts.server_video_analysis import AnalysisError


class LingzhiKeyPreflightTests(unittest.TestCase):
    @mock.patch("scripts.lingzhi_key_preflight.verify_key")
    @mock.patch("scripts.lingzhi_key_preflight.load_key", return_value="secret")
    @mock.patch("scripts.lingzhi_key_preflight.resolve_cli", return_value="/tmp/lzstudio")
    def test_success_requires_remote_account_call(self, resolve_cli, load_key, verify_key) -> None:
        output = io.StringIO()
        with mock.patch("sys.argv", ["lingzhi_key_preflight.py"]), redirect_stdout(output):
            self.assertEqual(lingzhi_key_preflight.main(), 0)
        self.assertEqual(output.getvalue().strip(), '{"ok": true, "authenticated": true}')
        verify_key.assert_called_once_with("/tmp/lzstudio", "secret")

    @mock.patch("scripts.lingzhi_key_preflight.load_key", side_effect=AnalysisError("missing"))
    @mock.patch("scripts.lingzhi_key_preflight.resolve_cli", return_value="/tmp/lzstudio")
    def test_missing_or_invalid_key_never_reports_success(self, resolve_cli, load_key) -> None:
        with mock.patch("sys.argv", ["lingzhi_key_preflight.py"]):
            with self.assertRaises(SystemExit) as raised:
                lingzhi_key_preflight.main()
        self.assertEqual(raised.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
