from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import generation_manifest, interaction_language, service_privacy


class InteractionLanguageTests(unittest.TestCase):
    def test_codex_setting_overrides_saved_task_language(self):
        self.assertEqual(interaction_language.resolve_interaction_locale(
            configured_locale="en-US", previous_locale="zh-CN"), "en-us")

    def test_explicit_reply_request_overrides_codex_setting(self):
        self.assertEqual(interaction_language.resolve_interaction_locale(
            "zh-CN", configured_locale="en", previous_locale="fr"), "zh-cn")

    def test_resume_retains_language_without_new_preference(self):
        self.assertEqual(interaction_language.resolve_interaction_locale(
            configured_locale="", previous_locale="pt-BR"), "pt-br")

    def test_os_locale_does_not_select_chat_language(self):
        with patch.dict(os.environ, {"LANG": "zh_CN.UTF-8", "LC_ALL": "zh_CN.UTF-8"}):
            self.assertEqual(interaction_language.resolve_interaction_locale(), "en")

    def test_languages_are_not_coerced_to_english(self):
        for locale, language in [("fr-FR", "fr"), ("pt_BR", "pt"), ("ja", "ja"), ("zh-Hant", "zh")]:
            with self.subTest(locale=locale):
                self.assertEqual(interaction_language.resolve_interaction_language(locale), language)

    def test_manifest_initialization_uses_explicit_codex_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            path = generation_manifest.command_init(argparse.Namespace(
                output_root=td, task_id="language-test", reuse=False,
                codex_locale="fr-FR", interaction_locale=None, target_language="zh",
            ))
            config = json.loads(Path(path).read_text())["userConfig"]
            self.assertEqual(config["interactionLocale"], "fr-fr")
            self.assertEqual(config["interactionLanguage"], "fr")
            self.assertEqual(config["targetLanguage"], "zh")

    def test_resume_command_preserves_generation_data(self):
        with tempfile.TemporaryDirectory() as td:
            path = generation_manifest.command_init(argparse.Namespace(
                output_root=td, task_id="language-test", reuse=False,
                interaction_locale="zh-CN", target_language="zh",
            ))
            generation_manifest.update(path, lambda data: data.update({
                "videoPrompts": {"segments": [{"prompt": "保留原始对白。"}]},
                "analysisTask": {"taskId": "existing-server-task"},
            }))
            before = generation_manifest.load_manifest(path)
            result = subprocess.run([sys.executable, str(ROOT / "scripts/generation_manifest.py"),
                "set-interaction-locale", "--manifest", str(path), "--interaction-locale", "en-US"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            after = generation_manifest.load_manifest(path)
            self.assertEqual(after["userConfig"]["interactionLocale"], "en-us")
            self.assertEqual(after["userConfig"]["interactionLanguage"], "en")
            for key in before:
                if key not in {"userConfig", "updatedAt"}:
                    self.assertEqual(after[key], before[key], key)
            for key in before["userConfig"]:
                if key not in {"interactionLocale", "interactionLanguage"}:
                    self.assertEqual(after["userConfig"][key], before["userConfig"][key], key)

    def test_empty_resume_locale_does_not_modify_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            path = generation_manifest.command_init(argparse.Namespace(
                output_root=td, task_id="language-test", reuse=False, interaction_locale="fr"))
            before = Path(path).read_bytes()
            with self.assertRaises(ValueError):
                generation_manifest.set_interaction_locale(path, " ")
            self.assertEqual(Path(path).read_bytes(), before)

    def test_public_support_message_has_no_unexpected_chinese(self):
        message = service_privacy.public_error({"code": "INSUFFICIENT_CREDITS"})
        self.assertFalse(any("\u4e00" <= character <= "\u9fff" for character in message))
        self.assertIn("marlon1102", message)


if __name__ == "__main__":
    unittest.main()
