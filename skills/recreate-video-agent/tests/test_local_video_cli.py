from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import local_video_cli  # noqa: E402


class LocalVideoCliTests(unittest.TestCase):
    def _fake_cli(self, root: Path, name: str, model_id: str) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        path.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"multimodal2video\" ] && [ \"$2\" = \"--help\" ]; then\n"
            f"  echo '--image --prompt --duration --ratio --video_resolution --model_version {model_id}'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1\" = \"query_result\" ] && [ \"$2\" = \"--help\" ]; then\n"
            "  echo '--submit_id'\n"
            "  exit 0\n"
            "fi\n"
            "echo '{\"id\":\"task-1\"}'\n",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_auto_returns_none_when_no_local_cli(self):
        with patch.object(local_video_cli, "detect_video_providers", return_value={"dreamina_cli": False, "xiaoyunque_cli": False}):
            self.assertIsNone(local_video_cli.resolve_video_provider("auto"))

    def test_dreamina_detection_requires_exact_model_contract(self):
        with tempfile.TemporaryDirectory() as td:
            cli = self._fake_cli(Path(td), "dreamina", "seedance2.0fast_vip")
            with patch.dict(os.environ, {"DREAMINA_CLI": str(cli)}):
                self.assertTrue(local_video_cli.dreamina_cli_supports_video_model("Seedance 2 Fast"))
                self.assertFalse(local_video_cli.dreamina_cli_supports_video_model("Seedance 2 Mini"))

    def test_auto_prefers_xiaoyunque_then_dreamina(self):
        self.assertEqual(
            local_video_cli.resolve_video_provider("auto", availability={"dreamina_cli": True, "xiaoyunque_cli": True}),
            "xiaoyunque_cli",
        )
        self.assertEqual(
            local_video_cli.resolve_video_provider("auto", availability={"dreamina_cli": True, "xiaoyunque_cli": False}),
            "dreamina_cli",
        )

    def test_generation_provider_order_ends_with_lingzhi(self):
        self.assertEqual(
            local_video_cli.resolve_generation_provider(availability={
                "libtv_cli": True, "xiaoyunque_cli": True, "dreamina_cli": True, "lingzhi_cli": True,
            }),
            "libtv_cli",
        )
        self.assertEqual(
            local_video_cli.resolve_generation_provider(availability={
                "libtv_cli": False, "xiaoyunque_cli": True, "dreamina_cli": True, "lingzhi_cli": True,
            }),
            "xiaoyunque_cli",
        )
        self.assertEqual(
            local_video_cli.resolve_generation_provider(availability={
                "libtv_cli": False, "xiaoyunque_cli": False, "dreamina_cli": True, "lingzhi_cli": True,
            }),
            "dreamina_cli",
        )
        self.assertEqual(
            local_video_cli.resolve_generation_provider(availability={
                "libtv_cli": False, "xiaoyunque_cli": False,
                "dreamina_cli": False, "lingzhi_cli": True,
            }),
            "lingzhi_cli",
        )

    def test_libtv_detection_uses_official_home_install_when_not_on_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            executable = self._fake_cli(root / ".libtv", "libtv", "unused")
            with patch.object(local_video_cli.Path, "home", return_value=root), patch.object(
                local_video_cli.shutil, "which", return_value=None
            ), patch.dict(os.environ, {"LIBTV_CLI": ""}):
                self.assertEqual(local_video_cli.resolve_libtv_cli(), executable.resolve())
                self.assertTrue(local_video_cli.libtv_cli_available())

    def test_lingzhi_detection_requires_submit_and_fetch_contracts(self):
        with tempfile.TemporaryDirectory() as td:
            cli = Path(td) / "lzstudio"
            cli.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"video\" ] && [ \"$2\" = \"submit\" ] && [ \"$3\" = \"--help\" ]; then\n"
                "  echo '--model --prompt --duration --aspect-ratio --resolution --reference-images'\n"
                "  exit 0\n"
                "fi\n"
                "if [ \"$1\" = \"video\" ] && [ \"$2\" = \"fetch\" ] && [ \"$3\" = \"--help\" ]; then\n"
                "  echo '--id'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            self.assertTrue(local_video_cli.lingzhi_cli_available(cli_path=cli))

    def test_submit_video_passes_every_reference_as_repeated_image_argument(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storyboard = root / "storyboard.png"
            product = root / "product.png"
            storyboard.write_bytes(b"storyboard")
            product.write_bytes(b"product")
            with patch.object(local_video_cli, "run_dreamina_cli", return_value={"id": "task-1"}) as submit:
                task_id = local_video_cli.submit_video(
                    "dreamina_cli",
                    "seedance-2-fast",
                    "prompt",
                    10,
                    reference_files=[str(storyboard), str(product)],
                )
            self.assertEqual(task_id, "task-1")
            arguments = submit.call_args.args[0]
            submitted_images = [arguments[index + 1] for index, value in enumerate(arguments) if value == "--image"]
            self.assertEqual(submitted_images, [str(storyboard.resolve()), str(product.resolve())])

    def test_lingzhi_video_uses_manifest_model_and_ordered_local_references(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storyboard = root / "storyboard.png"
            product = root / "product.png"
            storyboard.write_bytes(b"storyboard")
            product.write_bytes(b"product")
            with patch.object(local_video_cli, "run_lingzhi_cli", return_value={"id": "lz-1"}) as submit:
                task_id = local_video_cli.submit_video(
                    "lingzhi_cli", "seedance-2-fast", "prompt", 10,
                    reference_files=[storyboard, product], aspect_ratio="9:16", resolution="720p",
                )
            self.assertEqual(task_id, "lz-1")
            arguments = submit.call_args.args[0]
            self.assertEqual(arguments[:6], [
                "video", "submit", "--model", "seedance-2-fast", "--prompt", "prompt",
            ])
            submitted = [arguments[index + 1] for index, value in enumerate(arguments) if value == "--reference-images"]
            self.assertEqual(submitted, [str(storyboard.resolve()), str(product.resolve())])

    def test_lingzhi_image_is_fixed_to_gpt_image_2_and_1k(self):
        with tempfile.TemporaryDirectory() as td:
            reference = Path(td) / "reference.png"
            reference.write_bytes(b"reference")
            with patch.object(local_video_cli, "run_lingzhi_cli", return_value={"id": "image-1"}) as submit:
                task_id = local_video_cli.submit_lingzhi_image(
                    "edit prompt", reference_files=[reference], aspect_ratio="3:4",
                )
            self.assertEqual(task_id, "image-1")
            arguments = submit.call_args.args[0]
            self.assertIn("gpt-image-2", arguments)
            self.assertEqual(arguments[arguments.index("--resolution") + 1], "1K")
            self.assertEqual(arguments[arguments.index("--reference-images") + 1], str(reference.resolve()))


if __name__ == "__main__":
    unittest.main()
