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

    def test_generation_provider_order_excludes_unverified_lululab_video(self):
        self.assertEqual(
            local_video_cli.resolve_generation_provider(availability={
                "libtv_cli": True, "xiaoyunque_cli": True, "dreamina_cli": True, "lululab_cli": True,
            }),
            "libtv_cli",
        )
        self.assertEqual(
            local_video_cli.resolve_generation_provider(availability={
                "libtv_cli": False, "xiaoyunque_cli": True, "dreamina_cli": True, "lululab_cli": True,
            }),
            "xiaoyunque_cli",
        )
        self.assertEqual(
            local_video_cli.resolve_generation_provider(availability={
                "libtv_cli": False, "xiaoyunque_cli": False, "dreamina_cli": True, "lululab_cli": True,
            }),
            "dreamina_cli",
        )
        self.assertEqual(
            local_video_cli.resolve_generation_provider(availability={
                "libtv_cli": False, "xiaoyunque_cli": False,
                "dreamina_cli": False, "lululab_cli": True,
            }),
            None,
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

    def test_lululab_detection_checks_task_interface(self):
        import subprocess
        responses=[subprocess.CompletedProcess([],0,"--workflow-id --input", ""),subprocess.CompletedProcess([],0,"--id", "")]
        with patch.object(local_video_cli, "resolve_lululab_cli", return_value=Path("lululab")), patch.object(local_video_cli.subprocess, "run", side_effect=responses) as run:
            self.assertTrue(local_video_cli.lululab_cli_available())
        self.assertEqual([call.args[0][1:3] for call in run.call_args_list], [["task","submit"],["task","fetch"]])

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

    def test_lululab_video_uses_mini_model_and_original_workflow(self):
        import json
        with patch.dict(os.environ,{"LULULAB_VIDEO_WORKFLOW_ID":""}), patch.object(local_video_cli,"run_lululab_cli",return_value={"id":"v1"}) as run:
            self.assertEqual(local_video_cli.submit_video("lululab_cli","seedance-2-mini","prompt",11),"v1")
        args=run.call_args.args[0]
        self.assertEqual(args[:4],["task","submit","--workflow-id","VideoGenV2"])
        data=json.loads(args[5]);self.assertEqual(data["model"],"seedance-2-mini")
        self.assertEqual(data["duration"],11);self.assertEqual(data["referenceImages"],[])

    def test_lululab_image_uses_documented_task_schema_and_uploaded_references(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            reference = Path(td) / "reference.png"
            reference.write_bytes(b"reference")
            with patch.dict(os.environ, {"LULULAB_IMAGE_WORKFLOW_ID": "configured-image"}), patch.object(
                local_video_cli, "run_lululab_cli", side_effect=[
                    {"url": "https://asset.example/image.png", "mimeType": "image/png", "expiresAt": "expiry"},
                    {"id": "image-1"},
                ],
            ) as submit:
                task_id = local_video_cli.submit_lululab_image("edit prompt", reference_files=[reference], aspect_ratio="3:4")
            self.assertEqual(task_id, "image-1")
            self.assertEqual(submit.call_args_list[0].args[0], ["upload", str(reference.resolve())])
            arguments = submit.call_args_list[1].args[0]
            self.assertEqual(arguments[:4], ["task", "submit", "--workflow-id", "configured-image"])
            payload = json.loads(arguments[5])
            self.assertEqual(payload["model"], "gpt-image-2-5-sunburst")
            self.assertEqual(payload["resolution"], "1K")
            self.assertEqual(payload["referenceImages"], [{"url": "https://asset.example/image.png", "mimeType": "image/png", "expiredAt": "expiry"}])

    def test_builtin_image_workflow_does_not_require_configuration(self):
        with patch.dict(os.environ,{"LULULAB_IMAGE_WORKFLOW_ID":""}),patch.object(local_video_cli,"run_lululab_cli",return_value={"id":"image-1"}) as run:
            self.assertEqual(local_video_cli.submit_lululab_image("prompt"),"image-1")
        self.assertEqual(run.call_args.args[0][:4],["task","submit","--workflow-id","ImageGenV2"])

    def test_image_result_uses_output_instead_of_input_reference(self):
        value = {"status": "Succeeded", "input": {"referenceImages": [{"url": "https://example.com/reference"}]},
                 "output": {"image": {"url": "https://example.com/generated", "mimeType": "image/png", "expiredAt": "expiry"}}}
        self.assertEqual(local_video_cli.media(value)["url"], "https://example.com/generated")
        value["output"] = {}
        with self.assertRaises(local_video_cli.LocalVideoCliError):
            local_video_cli.media(value)



if __name__ == "__main__":
    unittest.main()
