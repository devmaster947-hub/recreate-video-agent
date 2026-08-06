from __future__ import annotations

import argparse
import json
import io
import os
import stat
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.run_cli import (
    LzStudioError,
    TaskFailedError,
    _poll_prompt_manifest,
    _resolve_cached_upload,
    build_final_output,
    detect_video_providers,
    download_media,
    generate_video,
    generate_creator_image,
    get_remaining_credits,
    load_api_key,
    poll_image,
    poll_recreate_video_prompt,
    poll_task,
    query_api_key,
    resolve_cli,
    resolve_official_cli,
    resolve_video_provider,
    run_cli,
    run_recreate_video_prompt_workflow,
    save_api_key,
    submit_image,
    submit_video,
    submit_official_video,
    submit_recreate_video_prompt,
    upload_file,
)


class ConfigTests(unittest.TestCase):
    def test_api_key_priority_parameter_environment_config_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".recreate-video" / "config.json"
            save_api_key("config-key", config_path=path)
            with patch.dict(os.environ, {"RECREATE_VIDEO_API_KEY": "environment-key"}):
                self.assertEqual(
                    load_api_key(
                        api_key="parameter-key",
                        config_path=path,
                        prompt=lambda _: self.fail("parameter key should win"),
                    ),
                    "parameter-key",
                )
                self.assertEqual(
                    load_api_key(
                        config_path=path,
                        prompt=lambda _: self.fail("environment key should win"),
                    ),
                    "environment-key",
                )
            self.assertEqual(
                load_api_key(
                    config_path=path,
                    prompt=lambda _: self.fail("config key should win"),
                ),
                "config-key",
            )

    def test_environment_key_is_runtime_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".recreate-video" / "config.json"
            with patch.dict(os.environ, {"RECREATE_VIDEO_API_KEY": "runtime-key"}):
                key = load_api_key(
                    config_path=path,
                    prompt=lambda _: self.fail("environment key should not prompt"),
                )
            self.assertEqual(key, "runtime-key")
            self.assertFalse(path.exists())

    def test_chat_key_can_be_saved_once_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".recreate-video" / "config.json"
            saved = save_api_key("  lzs-chat-key  ", config_path=path)
            self.assertEqual(saved, "lzs-chat-key")
            self.assertEqual(json.loads(path.read_text()), {"apiKey": "lzs-chat-key"})
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            loaded = load_api_key(
                config_path=path,
                prompt=lambda _: self.fail("saved chat key should not prompt again"),
            )
            self.assertEqual(loaded, "lzs-chat-key")

    def test_saving_api_key_preserves_media_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "apiKey": "old",
                        "mediaCacheVersion": 1,
                        "mediaCache": {
                            "product:hash": {"url": "https://example.test/a.png"}
                        },
                    }
                ),
                encoding="utf-8",
            )
            save_api_key("new", config_path=path)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["apiKey"], "new")
        self.assertIn("product:hash", saved["mediaCache"])

    def test_first_run_prompts_and_saves_private_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".recreate-video" / "config.json"
            key = load_api_key(config_path=path, prompt=lambda _: "lzs-test-key")
            self.assertEqual(key, "lzs-test-key")
            self.assertEqual(json.loads(path.read_text()), {"apiKey": "lzs-test-key"})
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            loaded = load_api_key(
                config_path=path,
                prompt=lambda _: self.fail("saved key should not prompt again"),
            )
            self.assertEqual(loaded, key)

    def test_query_api_key_uses_environment_then_config_without_prompting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".recreate-video" / "config.json"
            save_api_key("config-key", config_path=path)
            self.assertEqual(
                query_api_key(config_path=path),
                {"apiKey": "config-key", "source": "函数参数 config_path"},
            )
            with patch.dict(os.environ, {"RECREATE_VIDEO_API_KEY": "runtime-key"}):
                self.assertEqual(
                    query_api_key(config_path=path),
                    {
                        "apiKey": "runtime-key",
                        "source": "环境变量 RECREATE_VIDEO_API_KEY",
                    },
                )

    def test_query_api_key_reports_when_not_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".recreate-video" / "config.json"
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(LzStudioError, "尚未配置"):
                    query_api_key(config_path=path)


class CreditBalanceTests(unittest.TestCase):
    def test_reads_lingzhi_credits_balance(self):
        response = {
            "balances": [
                {"ledgerType": "Credits", "balance": 26},
                {"ledgerType": "Bonus", "balance": 5},
            ]
        }
        with patch("scripts.run_cli.run_cli", return_value=response) as mocked:
            self.assertEqual(get_remaining_credits("lingzhi_cli"), 26)
        mocked.assert_called_once_with(["account"], timeout=300)

    def test_reads_official_seedance_credit_balance(self):
        with patch(
            "scripts.run_cli.run_official_cli",
            return_value={"total_credit": 9780},
        ) as mocked:
            self.assertEqual(get_remaining_credits("official_cli"), 9780)
        mocked.assert_called_once_with(["user_credit"], timeout=300)

    def test_rejects_missing_or_unknown_credit_balance(self):
        with patch("scripts.run_cli.run_cli", return_value={"balances": []}):
            with self.assertRaisesRegex(LzStudioError, "缺少 Credits 余额"):
                get_remaining_credits("lingzhi_cli")
        with self.assertRaisesRegex(LzStudioError, "只支持"):
            get_remaining_credits("auto")


class PlatformTests(unittest.TestCase):
    def test_selects_macos_arm64_and_windows_x64_bundles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mac = root / "cli" / "macos-arm64" / "lzstudio"
            windows = root / "cli" / "windows-x64" / "lzstudio.exe"
            mac.parent.mkdir(parents=True)
            windows.parent.mkdir(parents=True)
            mac.write_bytes(b"mac")
            windows.write_bytes(b"windows")
            mac.chmod(0o700)
            self.assertEqual(
                resolve_cli(system="Darwin", machine="arm64", skill_root=root), mac.resolve()
            )
            self.assertEqual(
                resolve_cli(system="Darwin", machine="aarch64", skill_root=root), mac.resolve()
            )
            self.assertEqual(
                resolve_cli(system="Windows", machine="AMD64", skill_root=root), windows.resolve()
            )
            self.assertEqual(
                resolve_cli(system="Windows", machine="x86_64", skill_root=root), windows.resolve()
            )
            self.assertEqual(
                resolve_cli(system="Windows", machine="x64", skill_root=root), windows.resolve()
            )

    def test_windows_bundled_exe_does_not_require_posix_execute_bit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            windows = root / "cli" / "windows-x64" / "lzstudio.exe"
            windows.parent.mkdir(parents=True)
            windows.write_bytes(b"windows")
            windows.chmod(0o600)
            with patch("scripts.run_cli.os.access", return_value=False) as mocked_access:
                selected = resolve_cli(
                    system="Windows", machine="AMD64", skill_root=root
                )
            self.assertEqual(selected, windows.resolve())
            mocked_access.assert_not_called()

    def test_rejects_unsupported_platform(self):
        with self.assertRaisesRegex(
            LzStudioError,
            "当前平台不支持，请使用 macOS Apple Silicon 或 Windows x64。",
        ):
            resolve_cli(system="Linux", machine="x86_64")

    def test_windows_official_cli_checks_dreamina_exe_then_dreamina(self):
        with tempfile.TemporaryDirectory() as directory:
            official = Path(directory) / "dreamina.exe"
            official.write_bytes(b"official")
            official.chmod(0o600)

            def which(name):
                return str(official) if name == "dreamina.exe" else None

            with patch("scripts.run_cli.shutil.which", side_effect=which) as mocked_which:
                with patch("scripts.run_cli.os.access", return_value=False) as mocked_access:
                    selected = resolve_official_cli(system="Windows")
            self.assertEqual(selected, official.resolve())
            self.assertEqual(mocked_which.call_args_list[0].args, ("dreamina.exe",))
            mocked_access.assert_not_called()

    def test_detects_both_video_channels_without_running_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            official = root / "dreamina"
            bundled = root / "cli" / "macos-arm64" / "lzstudio"
            bundled.parent.mkdir(parents=True)
            official.write_bytes(b"official")
            bundled.write_bytes(b"lingzhi")
            official.chmod(0o700)
            bundled.chmod(0o700)
            result = detect_video_providers(
                official_cli_path=official,
                system="Darwin",
                machine="arm64",
                skill_root=root,
            )
        self.assertEqual(result, {"official_cli": True, "lingzhi_cli": True})

    def test_auto_prefers_official_and_falls_back_to_lingzhi(self):
        self.assertEqual(
            resolve_video_provider(
                "auto", availability={"official_cli": False, "lingzhi_cli": True}
            ),
            "lingzhi_cli",
        )
        self.assertEqual(
            resolve_video_provider(
                "auto", availability={"official_cli": True, "lingzhi_cli": True}
            ),
            "official_cli",
        )
        self.assertEqual(
            resolve_video_provider(
                "auto", availability={"official_cli": True, "lingzhi_cli": False}
            ),
            "official_cli",
        )

    def test_auto_rejects_when_no_video_channel_is_available(self):
        with self.assertRaisesRegex(LzStudioError, "未检测到可用的视频生成渠道"):
            resolve_video_provider(
                "auto", availability={"official_cli": False, "lingzhi_cli": False}
            )


class SubprocessTests(unittest.TestCase):
    def _fake_cli(self, directory: str) -> Path:
        script = Path(directory) / "lzstudio"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "payload = sys.stdin.read()\n"
            "print(json.dumps({'arguments': sys.argv[1:], 'payload': json.loads(payload) if payload else None}))\n",
            encoding="utf-8",
            )
        script.chmod(0o700)
        return script

    def test_runs_without_shell_and_parses_json(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = self._fake_cli(directory)
            result = run_cli(
                ["recreate-video-prompt", "submit"],
                input_json={"benchmarkVideoUrl": "https://example.test/video.mp4"},
                api_key="lzs-secret",
                cli_path=cli,
            )
            self.assertEqual(result["payload"]["benchmarkVideoUrl"], "https://example.test/video.mp4")
            self.assertEqual(
                result["arguments"],
                ["recreate-video-prompt", "submit", "--api-key", "lzs-secret"],
            )

    def test_error_redacts_api_key(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 1, "stderr": "bad lzs-secret", "stdout": ""},
        )()
        with tempfile.NamedTemporaryFile() as cli:
            with patch("scripts.run_cli.subprocess.run", return_value=completed):
                with self.assertRaises(LzStudioError) as raised:
                    run_cli(["upload", "x"], api_key="lzs-secret", cli_path=cli.name)
        self.assertNotIn("lzs-secret", str(raised.exception))


class TaskTests(unittest.TestCase):
    def test_benchmark_upload_uses_verified_compressed_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "benchmark.mp4"
            compressed = Path(directory) / "benchmark-under-20mb.mp4"
            source.write_bytes(b"o" * 31)
            compressed.write_bytes(b"c" * 20)
            prepared = {
                "filePath": str(compressed),
                "compressed": True,
                "originalBytes": 31,
                "uploadBytes": 20,
                "maxBytes": 30,
            }
            with patch(
                "scripts.run_cli.prepare_benchmark_video_for_upload",
                return_value=prepared,
            ):
                with patch(
                    "scripts.run_cli.run_cli",
                    return_value={"url": "https://example.test/benchmark.mp4"},
                ) as mocked:
                    media = upload_file(
                        source,
                        benchmark=True,
                        max_benchmark_bytes=30,
                        api_key="key",
                    )
        self.assertEqual(media["url"], "https://example.test/benchmark.mp4")
        self.assertEqual(mocked.call_args.args[0], ["upload", str(compressed.resolve())])

    def test_oversized_image_upload_uses_verified_compressed_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "product.png"
            compressed = Path(directory) / "product-under-20mb.jpg"
            source.write_bytes(b"o" * 31)
            compressed.write_bytes(b"c" * 20)
            prepared = {
                "filePath": str(compressed),
                "compressed": True,
                "originalBytes": 31,
                "uploadBytes": 20,
                "maxBytes": 30,
            }
            with patch("scripts.run_cli.prepare_image_for_upload", return_value=prepared):
                with patch(
                    "scripts.run_cli.run_cli",
                    return_value={"url": "https://example.test/product.jpg"},
                ) as mocked:
                    media = upload_file(source, max_upload_bytes=30, api_key="key")
        self.assertEqual(media["url"], "https://example.test/product.jpg")
        self.assertEqual(mocked.call_args.args[0], ["upload", str(compressed.resolve())])

    def test_submit_keeps_required_fields_and_omits_removed_configuration(self):
        with patch("scripts.run_cli.run_cli", return_value={"id": "task-1"}) as mocked:
            task_id = submit_recreate_video_prompt(
                "https://example.test/video.mp4",
                {
                    "videoModel": "seedance-2-fast",
                    "duration": 0,
                    "targetCountry": "美国",
                    "otherRequirements": "",
                    "videoStyle": "ugcProductDemo",
                    "videoProvider": "auto",
                    "targetLanguage": "English",
                    "resolution": "720p",
                    "aspectRatio": "9:16",
                },
                {"productName": "Product"},
                {"creatorMode": "auto"},
            )
        self.assertEqual(task_id, "task-1")
        arguments = mocked.call_args.args[0]
        self.assertEqual(
            arguments[arguments.index("--benchmark-video-url") + 1],
            "https://example.test/video.mp4",
        )
        self.assertEqual(
            json.loads(arguments[arguments.index("--user-config") + 1]),
            {
                "videoModel": "seedance-2-fast",
                "duration": 0,
                "targetCountry": "美国",
                "otherRequirements": "",
            },
        )
        self.assertEqual(
            json.loads(arguments[arguments.index("--product-brief") + 1]),
            {"productName": "Product"},
        )
        self.assertEqual(
            json.loads(arguments[arguments.index("--creator-brief") + 1]),
            {"creatorMode": "auto"},
        )
        self.assertNotIn("input_json", mocked.call_args.kwargs)

    def test_submit_rejects_empty_product_brief(self):
        for product_brief in (None, {}):
            with self.subTest(product_brief=product_brief):
                with patch("scripts.run_cli.run_cli") as mocked:
                    with self.assertRaisesRegex(
                        LzStudioError,
                        "productBrief 必须是非空 JSON 对象",
                    ):
                        submit_recreate_video_prompt(
                            "https://example.test/video.mp4",
                            {"duration": 0},
                            product_brief,
                            {"creatorMode": "auto"},
                        )
                mocked.assert_not_called()

    def test_poll_uses_same_id_until_succeeded(self):
        responses = iter(
            [
                {"status": "Processing"},
                {"status": "Succeeded", "result": {"url": "https://example.test/out.mp4"}},
            ]
        )
        seen = []

        def fetch(task_id):
            seen.append(task_id)
            return next(responses)

        result = poll_task(fetch, "task-1", interval=0, sleep=lambda _: None)
        self.assertEqual(result["status"], "Succeeded")
        self.assertEqual(seen, ["task-1", "task-1"])

    def test_created_state_keeps_polling_same_id(self):
        responses = iter(
            [
                {"status": "Created"},
                {"status": "Succeeded", "result": {"url": "https://example.test/out.mp4"}},
            ]
        )
        seen = []

        def fetch(task_id):
            seen.append(task_id)
            return next(responses)

        poll_task(fetch, "task-1", interval=0, sleep=lambda _: None)
        self.assertEqual(seen, ["task-1", "task-1"])

    def test_official_querying_state_keeps_polling_nested_response(self):
        responses = iter(
            [
                {"data": {"gen_status": "querying"}},
                {
                    "data": {
                        "gen_status": "success",
                        "video_url": "https://example.test/official.mp4",
                    }
                },
            ]
        )
        result = poll_task(
            lambda _: next(responses), "official-1", interval=0, sleep=lambda _: None
        )
        self.assertEqual(result["data"]["gen_status"], "success")

    def test_image_submission_always_uses_gpt_image_2(self):
        with patch(
            "scripts.run_cli.run_cli",
            return_value={"id": "image-1"},
        ) as mocked:
            task_id = submit_image("Storyboard prompt")

        self.assertEqual(task_id, "image-1")
        arguments = mocked.call_args.args[0]
        self.assertEqual(arguments[arguments.index("--model") + 1], "gpt-image-2")

    def test_image_model_override_is_rejected(self):
        with self.assertRaises(TypeError):
            submit_image("Storyboard prompt", model="other-image-model")

        with self.assertRaises(TypeError):
            generate_creator_image(
                "Creator prompt",
                [1],
                model="other-image-model",
            )

        with self.assertRaisesRegex(
            LzStudioError,
            "图片生成模型固定为 gpt-image-2",
        ):
            run_cli(
                [
                    "image",
                    "submit",
                    "--model",
                    "other-image-model",
                    "--prompt",
                    "Storyboard prompt",
                ]
            )

    def test_terminal_failure_raises_distinct_error(self):
        with self.assertRaises(TaskFailedError) as raised:
            poll_task(
                lambda _: {"status": "Failed", "errorMessage": "provider failed"},
                "task-1",
                interval=0,
                sleep=lambda _: None,
            )
        self.assertEqual(raised.exception.task_id, "task-1")
        self.assertEqual(raised.exception.state, "failed")

    def test_cross_segment_creator_retries_one_terminal_failure(self):
        submitted = []
        recorded = []

        def fake_submit(prompt, **kwargs):
            submitted.append((prompt, kwargs))
            return f"image-{len(submitted)}"

        with patch("scripts.run_cli.submit_image", side_effect=fake_submit):
            with patch(
                "scripts.run_cli.poll_image",
                side_effect=[
                    TaskFailedError("image-1", "failed", "first failed"),
                    {"url": "https://example.test/creator.png", "mimeType": "image/png", "expiredAt": ""},
                ],
            ):
                result = generate_creator_image(
                    "Creator prompt",
                    [1, 2, 2],
                    reference_images=["https://example.test/reference.png"],
                    on_submit=lambda attempt, task_id: recorded.append((attempt, task_id)),
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["taskIds"], ["image-1", "image-2"])
        self.assertEqual(result["affectedSegments"], [1, 2])
        self.assertEqual(recorded, [(1, "image-1"), (2, "image-2")])
        self.assertEqual(submitted[0], submitted[1])

    def test_single_segment_creator_does_not_retry(self):
        with patch("scripts.run_cli.submit_image", return_value="image-1") as submit:
            with patch(
                "scripts.run_cli.poll_image",
                side_effect=TaskFailedError("image-1", "failed", "failed"),
            ):
                result = generate_creator_image("Creator prompt", [1])

        self.assertEqual(submit.call_count, 1)
        self.assertFalse(result["manualRequired"])
        self.assertEqual(result["taskIds"], ["image-1"])

    def test_cross_segment_second_failure_requires_manual_action(self):
        with patch("scripts.run_cli.submit_image", side_effect=["image-1", "image-2"]):
            with patch(
                "scripts.run_cli.poll_image",
                side_effect=[
                    TaskFailedError("image-1", "failed", "first failed"),
                    TaskFailedError("image-2", "cancelled", "second failed"),
                ],
            ):
                result = generate_creator_image("Creator prompt", [1, 3])

        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["manualRequired"])
        self.assertEqual(result["affectedSegments"], [1, 3])
        self.assertEqual(result["taskIds"], ["image-1", "image-2"])

    def test_non_terminal_poll_errors_do_not_resubmit(self):
        for message in (
            "network unavailable",
            "未知任务状态：provider-created-v2",
            "任务 image-1 轮询超时。",
        ):
            with self.subTest(message=message):
                with patch("scripts.run_cli.submit_image", return_value="image-1") as submit:
                    with patch(
                        "scripts.run_cli.poll_image",
                        side_effect=LzStudioError(message),
                    ):
                        with self.assertRaises(LzStudioError):
                            generate_creator_image("Creator prompt", [1, 2])
                self.assertEqual(submit.call_count, 1)

    def test_recreate_result_preserves_public_output_fields(self):
        response = {
            "status": "Succeeded",
            "result": {
                "videoPrompts": {
                    "segments": [
                        {
                            "segmentId": 1,
                            "title": "Hook",
                            "duration": 8,
                            "prompt": "Prompt",
                            "referenceImages": [],
                        }
                    ]
                },
                "creatorReferencePlan": {"creators": []},
            },
        }
        with patch("scripts.run_cli.fetch_recreate_video_prompt", return_value=response):
            result = poll_recreate_video_prompt("task-1", interval=0, sleep=lambda _: None)
        self.assertEqual(set(result), {"videoPrompts", "creatorReferencePlan"})

    def test_image_fetch_accepts_output_envelope(self):
        response = {
            "status": "Succeeded",
            "output": {
                "image": {
                    "url": "https://example.test/image.png",
                    "mimeType": "image/png",
                    "expiredAt": "soon",
                }
            },
        }
        with patch("scripts.run_cli.fetch_image", return_value=response):
            result = poll_image("image-1", interval=0, sleep=lambda _: None)
        self.assertEqual(result["url"], "https://example.test/image.png")

    def test_video_display_name_maps_to_cli_model_id(self):
        with patch("scripts.run_cli.run_cli", return_value={"id": "video-1"}) as mocked:
            task_id = submit_video("Google Omni", "Prompt", 8)
        self.assertEqual(task_id, "video-1")
        arguments = mocked.call_args.args[0]
        self.assertEqual(arguments[arguments.index("--model") + 1], "google-omni")

    def test_grok_display_name_maps_to_cli_model_id(self):
        with patch("scripts.run_cli.run_cli", return_value={"id": "video-1"}) as mocked:
            task_id = submit_video("Grok Imagine 1.5 Preview", "Prompt", 8)
        self.assertEqual(task_id, "video-1")
        arguments = mocked.call_args.args[0]
        self.assertEqual(
            arguments[arguments.index("--model") + 1],
            "grok-imagine-1-5-preview",
        )

    def test_grok_cli_model_id_is_preserved(self):
        with patch("scripts.run_cli.run_cli", return_value={"id": "video-1"}) as mocked:
            submit_video("grok-imagine-1-5-preview", "Prompt", 8)
        arguments = mocked.call_args.args[0]
        self.assertEqual(
            arguments[arguments.index("--model") + 1],
            "grok-imagine-1-5-preview",
        )

    def test_official_video_uses_multimodal_cli_with_local_references(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            reference.write_bytes(b"image")
            with patch(
                "scripts.run_cli.run_official_cli", return_value={"submit_id": "official-1"}
            ) as mocked:
                task_id = submit_official_video(
                    "Seedance 2 Mini",
                    "Prompt",
                    8,
                    reference_files=[reference],
                )
        self.assertEqual(task_id, "official-1")
        arguments = mocked.call_args.args[0]
        self.assertEqual(arguments[0], "multimodal2video")
        self.assertEqual(
            arguments[arguments.index("--model_version") + 1], "seedance2.0mini"
        )
        self.assertEqual(arguments[arguments.index("--image") + 1], str(reference.resolve()))

    def test_generate_video_returns_same_shape_for_both_providers(self):
        succeeded = {
            "status": "Succeeded",
            "result": {
                "video": {"url": "https://example.test/video.mp4"},
                "creditsConsumed": 7,
            },
        }
        with patch("scripts.run_cli.submit_video", return_value="lz-1"):
            with patch("scripts.run_cli.fetch_video", return_value=succeeded):
                lingzhi = generate_video(
                    "Seedance 2 Mini",
                    "Prompt",
                    8,
                    video_provider="lingzhi_cli",
                    availability={"official_cli": True, "lingzhi_cli": True},
                    poll_interval=0,
                    sleep=lambda _: None,
                )
        with patch("scripts.run_cli.submit_official_video", return_value="official-1"):
            with patch("scripts.run_cli.fetch_official_video", return_value=succeeded):
                official = generate_video(
                    "Seedance 2 Mini",
                    "Prompt",
                    8,
                    video_provider="official_cli",
                    availability={"official_cli": True, "lingzhi_cli": True},
                    poll_interval=0,
                    sleep=lambda _: None,
                )
        self.assertEqual(set(lingzhi), {"video", "credits", "errorMessage"})
        self.assertEqual(set(official), set(lingzhi))
        self.assertEqual(lingzhi["credits"], 7)
        self.assertEqual(official["video"]["url"], "https://example.test/video.mp4")

    def test_auto_google_omni_uses_lingzhi_even_when_both_clis_exist(self):
        succeeded = {
            "status": "Succeeded",
            "video": {"url": "https://example.test/omni.mp4"},
        }
        with patch("scripts.run_cli.submit_video", return_value="lz-1") as submit:
            with patch("scripts.run_cli.fetch_video", return_value=succeeded):
                result = generate_video(
                    "Google Omni",
                    "Prompt",
                    8,
                    availability={"official_cli": True, "lingzhi_cli": True},
                    poll_interval=0,
                    sleep=lambda _: None,
                )
        self.assertEqual(result["errorMessage"], "")
        self.assertEqual(submit.call_count, 1)

    def test_auto_grok_uses_lingzhi_even_when_both_clis_exist(self):
        succeeded = {
            "status": "Succeeded",
            "video": {"url": "https://example.test/grok.mp4"},
        }
        with patch("scripts.run_cli.submit_video", return_value="lz-1") as submit:
            with patch("scripts.run_cli.fetch_video", return_value=succeeded):
                result = generate_video(
                    "Grok Imagine 1.5 Preview",
                    "Prompt",
                    8,
                    availability={"official_cli": True, "lingzhi_cli": True},
                    poll_interval=0,
                    sleep=lambda _: None,
                )
        self.assertEqual(result["errorMessage"], "")
        self.assertEqual(result["video"]["url"], "https://example.test/grok.mp4")
        self.assertEqual(set(result), {"video", "credits", "errorMessage"})
        self.assertEqual(submit.call_count, 1)

    def test_build_final_output_keeps_public_fields(self):
        result = build_final_output(
            videos=[{"segmentId": 1, "url": "https://example.test/video.mp4"}],
            creator_images=[{"creatorId": "c1", "url": "https://example.test/c1.png"}],
            video_prompts={"segments": []},
            creator_reference_plan={"creators": []},
            credits_consumed=12,
        )
        self.assertEqual(
            set(result["result"]),
            {"videos", "creatorImages", "videoPrompts", "creatorReferencePlan"},
        )
        self.assertEqual(result["creditsConsumed"], 12)

    def test_download_media_uses_version_suffix_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "segment-01.mp4"
            first.write_bytes(b"old")
            with patch("scripts.run_cli.urlopen", return_value=io.BytesIO(b"new")):
                saved = download_media(
                    "https://example.test/segment.mp4",
                    first,
                )
            self.assertEqual(saved.name, "segment-01-v2.mp4")
            self.assertEqual(first.read_bytes(), b"old")
            self.assertEqual(saved.read_bytes(), b"new")


class MediaCacheTests(unittest.TestCase):
    def test_reuses_same_content_after_file_is_moved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "product-a.png"
            second = root / "product-b.png"
            first.write_bytes(b"same-image")
            second.write_bytes(b"same-image")
            config = root / "config.json"
            calls = []

            def upload(path, *, benchmark=False):
                calls.append((Path(path), benchmark))
                return {
                    "url": "https://example.test/product.png",
                    "mimeType": "image/png",
                    "expiredAt": "2026-08-03T03:27:45.3179917Z",
                }

            at = datetime(2026, 8, 1, tzinfo=timezone.utc)
            first_media = _resolve_cached_upload(
                first,
                kind="product",
                config_path=config,
                uploader=upload,
                at=at,
            )
            second_media = _resolve_cached_upload(
                second,
                kind="product",
                config_path=config,
                uploader=upload,
                at=at,
            )

        self.assertFalse(first_media["cacheHit"])
        self.assertTrue(second_media["cacheHit"])
        self.assertEqual(len(calls), 1)

    def test_near_expiry_uploads_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = root / "product.png"
            product.write_bytes(b"image")
            config = root / "config.json"
            at = datetime(2026, 8, 1, tzinfo=timezone.utc)
            expirations = [
                at + timedelta(minutes=20),
                at + timedelta(hours=1),
            ]
            calls = []

            def upload(path, *, benchmark=False):
                calls.append(Path(path))
                expires = expirations[len(calls) - 1]
                return {
                    "url": f"https://example.test/{len(calls)}.png",
                    "mimeType": "image/png",
                    "expiredAt": expires.isoformat(),
                }

            _resolve_cached_upload(
                product,
                kind="product",
                config_path=config,
                uploader=upload,
                at=at,
            )
            refreshed = _resolve_cached_upload(
                product,
                kind="product",
                config_path=config,
                uploader=upload,
                at=at + timedelta(minutes=11),
            )

        self.assertEqual(len(calls), 2)
        self.assertFalse(refreshed["cacheHit"])
        self.assertEqual(refreshed["url"], "https://example.test/2.png")


class RecreateWorkflowTests(unittest.TestCase):
    @staticmethod
    def _success_response():
        return {
            "status": "Succeeded",
            "output": {
                "videoPrompts": {
                    "summary": "summary",
                    "segments": [
                        {
                            "segmentId": 1,
                            "title": "Hook",
                            "duration": 8,
                            "prompt": "Prompt",
                        }
                    ],
                },
                "creatorPrompts": {
                    "summary": "creators",
                    "creators": [
                        {
                            "creatorId": 1,
                            "role": "Presenter",
                            "appearsInSegments": [1],
                            "consistencyReason": "Keep one person",
                            "prompt": "Creator prompt",
                        }
                    ],
                },
            },
            "credits": 6,
        }

    def test_run_parallelizes_product_and_media_and_records_before_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark = root / "benchmark.mp4"
            product = root / "product.png"
            benchmark.write_bytes(b"video")
            product.write_bytes(b"image")
            input_path = root / "input.json"
            input_path.write_text(
                json.dumps(
                    {
                        "benchmarkVideo": {"filePath": str(benchmark), "url": ""},
                        "productBrief": {
                            "productImages": [
                                {
                                    "fileName": product.name,
                                    "filePath": str(product),
                                    "url": "",
                                }
                            ]
                        },
                        "productAnalysis": {
                            "productName": "Product",
                            "productMaterialFacts": ["Visible fact"],
                        },
                        "userConfig": {
                            "videoModel": "seedance-2-mini",
                            "duration": 0,
                            "videoStyle": "ugcProductDemo",
                            "videoProvider": "auto",
                            "targetLanguage": "English",
                            "resolution": "720p",
                            "aspectRatio": "9:16",
                            "otherRequirements": "",
                        },
                        "creatorBrief": {
                            "creatorMode": "auto",
                            "creatorImages": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            product_started = threading.Event()
            media_started = threading.Event()
            submit_calls = []
            fetched_ids = []

            def build(analysis):
                product_started.set()
                self.assertTrue(media_started.wait(1))
                return {"产品卡": {"产品名称": analysis["productName"]}}

            def prepare(value, **kwargs):
                media_started.set()
                self.assertTrue(product_started.wait(1))
                return {
                    "benchmark_path": benchmark,
                    "benchmark_media": {
                        "url": "https://example.test/benchmark.mp4",
                        "mimeType": "video/mp4",
                        "expiredAt": "",
                    },
                    "product_assets": [
                        {
                            "path": product,
                            "media": {
                                "url": "https://example.test/product.png",
                                "mimeType": "image/png",
                                "expiredAt": "",
                            },
                        }
                    ],
                    "creator_assets": [],
                    "creator_brief": {"creatorMode": "auto", "creatorImages": []},
                }

            def submit(benchmark_url, user_config, product_brief, creator_brief):
                submit_calls.append(
                    (benchmark_url, user_config, product_brief, creator_brief)
                )
                return "task-1"

            output_root = root / "output"

            def fetch(task_id):
                fetched_ids.append(task_id)
                manifest = output_root / task_id / "manifest.json"
                self.assertTrue(manifest.is_file())
                saved = json.loads(manifest.read_text(encoding="utf-8"))
                self.assertEqual(saved["prompt_task"]["submit_id"], task_id)
                return self._success_response()

            result = run_recreate_video_prompt_workflow(
                input_path,
                output_root=output_root,
                product_builder=build,
                media_preparer=prepare,
                submitter=submit,
                fetcher=fetch,
                balance_reader=lambda _: 534,
                interval=0,
                sleep=lambda _: None,
            )

            resumed = _poll_prompt_manifest(
                result["manifestPath"],
                fetcher=lambda _: self.fail("completed resume must not fetch"),
                balance_reader=lambda _: self.fail("completed resume must not query balance"),
                interval=0,
                sleep=lambda _: None,
            )

        self.assertEqual(len(submit_calls), 1)
        self.assertEqual(
            submit_calls[0][1],
            {
                "videoModel": "seedance-2-mini",
                "duration": 0,
                "otherRequirements": "",
            },
        )
        self.assertEqual(fetched_ids, ["task-1"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["remainingCredits"], 534)
        self.assertIn("creatorPrompts", result["result"])
        self.assertEqual(resumed["taskId"], "task-1")

    def test_run_without_product_images_submits_benchmark_product_brief(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark = root / "benchmark.mp4"
            benchmark.write_bytes(b"video")
            input_path = root / "input.json"
            input_path.write_text(
                json.dumps(
                    {
                        "benchmarkVideo": {"filePath": str(benchmark), "url": ""},
                        "productBrief": {"productImages": []},
                        "productAnalysis": {},
                        "userConfig": {
                            "videoModel": "seedance-2-mini",
                            "duration": 0,
                            "otherRequirements": "",
                        },
                        "creatorBrief": {
                            "creatorMode": "auto",
                            "creatorImages": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            submit_calls = []

            def prepare(value, **kwargs):
                self.assertEqual(value["productBrief"]["productImages"], [])
                self.assertEqual(
                    value["productBrief"]["productName"],
                    "跟原视频产品一致",
                )
                return {
                    "benchmark_path": benchmark,
                    "benchmark_media": {
                        "url": "https://example.test/benchmark.mp4",
                        "mimeType": "video/mp4",
                        "expiredAt": "",
                    },
                    "product_assets": [],
                    "creator_assets": [],
                    "creator_brief": {"creatorMode": "auto", "creatorImages": []},
                }

            def submit(benchmark_url, user_config, product_brief, creator_brief):
                submit_calls.append(product_brief)
                return "task-no-product"

            output_root = root / "output"
            result = run_recreate_video_prompt_workflow(
                input_path,
                output_root=output_root,
                product_builder=lambda _: self.fail(
                    "product builder must not run without product images"
                ),
                media_preparer=prepare,
                submitter=submit,
                fetcher=lambda _: self._success_response(),
                balance_reader=lambda _: 500,
                interval=0,
                sleep=lambda _: None,
            )
            manifest = json.loads(
                Path(result["manifestPath"]).read_text(encoding="utf-8")
            )

        self.assertEqual(
            submit_calls,
            [{"productName": "跟原视频产品一致"}],
        )
        self.assertEqual(manifest["assets"]["product_images"], [])

    def test_poll_retries_transient_error_and_balance_failure_does_not_fail_result(self):
        from scripts import generation_manifest

        with tempfile.TemporaryDirectory() as directory:
            manifest = generation_manifest.command_init(
                argparse.Namespace(
                    task_id="task-2",
                    model="seedance-2-mini",
                    output_root=directory,
                    reuse=False,
                )
            )
            generation_manifest.update_prompt_task(
                manifest,
                submit_id="task-2",
                status="submitted",
            )
            responses = iter(
                [
                    LzStudioError("temporary network error"),
                    {"status": "Running"},
                    self._success_response(),
                ]
            )
            seen = []

            def fetch(task_id):
                seen.append(task_id)
                value = next(responses)
                if isinstance(value, Exception):
                    raise value
                return value

            result = _poll_prompt_manifest(
                manifest,
                fetcher=fetch,
                balance_reader=lambda _: (_ for _ in ()).throw(
                    LzStudioError("balance unavailable")
                ),
                interval=0,
                sleep=lambda _: None,
            )
            saved = generation_manifest.load_manifest(manifest)

        self.assertEqual(seen, ["task-2", "task-2", "task-2"])
        self.assertEqual(result["status"], "succeeded")
        self.assertIsNone(result["remainingCredits"])
        self.assertIn("balance unavailable", saved["prompt_task"]["balance_error"])


if __name__ == "__main__":
    unittest.main()
