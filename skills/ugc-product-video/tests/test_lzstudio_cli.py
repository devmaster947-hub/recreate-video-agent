from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import lzstudio_cli


class ApiKeyTests(unittest.TestCase):
    def test_prompt_mentions_studio_url(self):
        prompts = []
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            with patch.dict(os.environ, {}, clear=True):
                value = lzstudio_cli.resolve_api_key(
                    config=config,
                    prompt=lambda message: prompts.append(message) or "secret",
                    save_user_input=False,
                )
        self.assertEqual(value, "secret")
        self.assertEqual(len(prompts), 1)
        self.assertIn("https://studio.lingzhiai.com.cn/", prompts[0])

    def test_missing_key_error_mentions_studio_url(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(lzstudio_cli.LzStudioError) as raised:
                    lzstudio_cli.resolve_api_key(config=config)
        self.assertIn(
            "https://studio.lingzhiai.com.cn/",
            str(raised.exception),
        )

    def test_priority_is_environment_config_then_user_input(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text('{"apiKey":"config-key"}', encoding="utf-8")
            with patch.dict(os.environ, {"LINGZHI_API_KEY": "env-key"}, clear=False):
                self.assertEqual(
                    lzstudio_cli.resolve_api_key(api_key="user-key", config=config),
                    "env-key",
                )
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    lzstudio_cli.resolve_api_key(api_key="user-key", config=config),
                    "config-key",
                )
                config.unlink()
                self.assertEqual(
                    lzstudio_cli.resolve_api_key(
                        api_key="user-key", config=config, save_user_input=False
                    ),
                    "user-key",
                )

    def test_user_key_is_saved_privately(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            with patch.dict(os.environ, {}, clear=True):
                value = lzstudio_cli.resolve_api_key(api_key="secret", config=config)
            self.assertEqual(value, "secret")
            self.assertEqual(json.loads(config.read_text()), {"apiKey": "secret"})
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)


class PlatformTests(unittest.TestCase):
    def test_selects_only_bundled_platform_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mac = root / "bin" / "macos" / "lzstudio"
            windows = root / "bin" / "windows" / "lzstudio.exe"
            mac.parent.mkdir(parents=True)
            windows.parent.mkdir(parents=True)
            mac.write_bytes(b"mac")
            windows.write_bytes(b"windows")
            self.assertEqual(
                lzstudio_cli.resolve_cli(
                    system="Darwin", machine="arm64", skill_root=root
                ),
                mac.resolve(),
            )
            self.assertEqual(
                lzstudio_cli.resolve_cli(
                    system="Windows", machine="AMD64", skill_root=root
                ),
                windows.resolve(),
            )

    def test_rejects_unsupported_platform(self):
        with self.assertRaises(lzstudio_cli.LzStudioError):
            lzstudio_cli.resolve_cli(system="Linux", machine="x86_64")


class CliTests(unittest.TestCase):
    def _fake_cli(self, directory: str) -> Path:
        cli = Path(directory) / "lzstudio"
        cli.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "print(json.dumps({'arguments': sys.argv[1:]}))\n",
            encoding="utf-8",
        )
        cli.chmod(0o700)
        return cli

    def test_subprocess_has_no_shell_and_parses_json(self):
        with tempfile.TemporaryDirectory() as directory:
            result = lzstudio_cli.run_cli(
                ["sales-video-prompt", "fetch", "--id", "task-1"],
                api_key="secret",
                config=Path(directory) / "config.json",
                cli_path=self._fake_cli(directory),
            )
        self.assertEqual(
            result["arguments"],
            [
                "sales-video-prompt",
                "fetch",
                "--api-key",
                "secret",
                "--id",
                "task-1",
            ],
        )

    def test_upload_uses_real_positional_file_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "product.png"
            file.write_bytes(b"image")
            with patch(
                "lzstudio_cli.run_cli",
                return_value={"url": "https://example.test/p.png", "mimeType": "image/png"},
            ) as mocked:
                result = lzstudio_cli.upload_file(file)
        self.assertEqual(result["mimeType"], "image/png")
        self.assertEqual(mocked.call_args.args[0], ["upload", str(file.resolve())])

    def test_credit_balance_uses_account_credits_contract(self):
        with patch(
            "lzstudio_cli.run_cli",
            return_value={"ledgerType": "Credits", "balance": 711},
        ) as mocked:
            self.assertEqual(lzstudio_cli.get_credit_balance(), 711)
        self.assertEqual(mocked.call_args.args[0], ["account", "--credits"])

    def test_credit_balance_rejects_invalid_response(self):
        for response in ({}, {"balance": True}, {"balance": -1}, {"balance": "711"}):
            with self.subTest(response=response):
                with patch("lzstudio_cli.run_cli", return_value=response):
                    with self.assertRaises(lzstudio_cli.LzStudioError):
                        lzstudio_cli.get_credit_balance()

    def test_errors_redact_api_key(self):
        completed = type(
            "Completed", (), {"returncode": 1, "stderr": "bad secret", "stdout": ""}
        )()
        with tempfile.TemporaryDirectory() as directory:
            cli = self._fake_cli(directory)
            with patch("lzstudio_cli.subprocess.run", return_value=completed):
                with self.assertRaises(lzstudio_cli.LzStudioError) as raised:
                    lzstudio_cli.run_cli(
                        ["upload", "x"],
                        api_key="secret",
                        config=Path(directory) / "config.json",
                        cli_path=cli,
                    )
        self.assertNotIn("secret", str(raised.exception))


class WorkflowTests(unittest.TestCase):
    def test_submit_preserves_all_input_fields(self):
        with patch("lzstudio_cli.run_cli", return_value={"id": "task-1"}) as mocked:
            task_id = lzstudio_cli.submit_sales_video_prompt(
                {"videoStyle": "ugcProductDemo"},
                {"productName": "Cup"},
                {"creatorMode": "auto"},
                "Keep the opening hook",
            )
        self.assertEqual(task_id, "task-1")
        arguments = mocked.call_args.args[0]
        self.assertEqual(
            json.loads(arguments[arguments.index("--user-config") + 1]),
            {"videoStyle": "ugcProductDemo"},
        )
        self.assertEqual(
            json.loads(arguments[arguments.index("--product-brief") + 1]),
            {"productName": "Cup"},
        )
        self.assertEqual(
            json.loads(arguments[arguments.index("--creator-brief") + 1]),
            {"creatorMode": "auto"},
        )
        self.assertEqual(
            arguments[arguments.index("--creative-requirement") + 1],
            "Keep the opening hook",
        )

    def test_poll_keeps_same_id_and_stable_output_schema(self):
        responses = iter(
            [
                {"status": "Processing"},
                {
                    "status": "Succeeded",
                    "result": {
                        "videoPrompts": {
                            "summary": "Summary",
                            "segments": [{"segmentId": 1, "prompt": "Prompt"}],
                        }
                    },
                },
            ]
        )
        seen = []

        def fetch(task_id, **_):
            seen.append(task_id)
            return next(responses)

        with patch("lzstudio_cli.fetch_sales_video_prompt", side_effect=fetch):
            result = lzstudio_cli.poll_sales_video_prompt(
                "task-1", interval=0, sleep=lambda _: None
            )
        self.assertEqual(seen, ["task-1", "task-1"])
        self.assertEqual(
            set(result),
            {"videoPrompts", "segments", "summary", "success", "errorMessage"},
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["summary"], "Summary")

    def test_both_video_styles_pass_through_unchanged(self):
        for style in ("ugcProductDemo", "storyDrivenAd"):
            with self.subTest(style=style):
                with patch("lzstudio_cli.run_cli", return_value={"taskId": "t"}) as mocked:
                    lzstudio_cli.submit_sales_video_prompt(
                        {"videoStyle": style}, {"productName": "Cup"}
                    )
                args = mocked.call_args.args[0]
                self.assertEqual(
                    json.loads(args[args.index("--user-config") + 1])["videoStyle"],
                    style,
                )

    def test_image_and_video_use_submit_fetch_commands(self):
        with patch("lzstudio_cli.run_cli", return_value={"id": "i1"}) as image_run:
            self.assertEqual(lzstudio_cli.submit_image("portrait"), "i1")
        self.assertEqual(image_run.call_args.args[0][:2], ["image", "submit"])
        with patch("lzstudio_cli.run_cli", return_value={"id": "v1"}) as video_run:
            self.assertEqual(
                lzstudio_cli.submit_video("Seedance 2 fast", "video", 15), "v1"
            )
        args = video_run.call_args.args[0]
        self.assertEqual(args[:2], ["video", "submit"])
        self.assertEqual(args[args.index("--model") + 1], "seedance-2-fast")

    def test_grok_imagine_video_model_is_normalized(self):
        with patch("lzstudio_cli.run_cli", return_value={"id": "v1"}) as video_run:
            self.assertEqual(
                lzstudio_cli.submit_video("Grok Imagine 1.5 Preview", "video", 15),
                "v1",
            )
        args = video_run.call_args.args[0]
        self.assertEqual(
            args[args.index("--model") + 1],
            "grok-imagine-1-5-preview",
        )


if __name__ == "__main__":
    unittest.main()
