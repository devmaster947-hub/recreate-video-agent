from __future__ import annotations

import os
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import install_lululab as installer
from scripts.install_lululab import (
    PATH_BLOCK_END,
    PATH_BLOCK_START,
    default_install_dir,
    ensure_installed,
)


class InstallTests(unittest.TestCase):
    def _fake_skill(self, directory: str) -> Path:
        root = Path(directory) / "skill"
        binary = root / "cli" / "macos-arm64" / "lululab"
        binary.parent.mkdir(parents=True)
        binary.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then printf 'lululab 9.9.9\\n'; "
            "else printf '%s\\n' '--workflow-id --input'; fi\n",
            encoding="utf-8",
        )
        binary.chmod(0o755)
        installer.EXPECTED_SHA256[("Darwin", "arm64")] = hashlib.sha256(binary.read_bytes()).hexdigest()
        return root

    def test_default_user_application_directories(self):
        home = Path("/Users/tester")
        self.assertEqual(
            default_install_dir(system="Darwin", home=home),
            Path("/Users/tester/Applications/LuluLab/bin"),
        )
        self.assertEqual(
            default_install_dir(
                system="Windows",
                home=Path("C:/Users/tester"),
                environ={"LOCALAPPDATA": "C:/Users/tester/AppData/Local"},
            ),
            Path("C:/Users/tester/AppData/Local/Programs/LuluLab/bin").resolve(),
        )

    def test_installs_updates_path_and_accepts_by_command_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._fake_skill(directory)
            home = Path(directory) / "home"
            profile = home / ".zprofile"
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
                result = ensure_installed(
                    system="Darwin",
                    machine="arm64",
                    skill_root=root,
                    home=home,
                    profiles=[profile],
                )
                self.assertEqual(result["acceptanceCommand"], "lululab --version")
                self.assertEqual(result["version"], "lululab 9.9.9")
                self.assertTrue(str(result["installedPath"]).endswith(
                    "/Applications/LuluLab/bin/lululab"
                ))
                self.assertEqual(
                    os.environ["PATH"].split(os.pathsep)[0],
                    str((home / "Applications" / "LuluLab" / "bin").resolve()),
                )
            profile_text = profile.read_text(encoding="utf-8")
            self.assertIn(PATH_BLOCK_START, profile_text)
            self.assertIn(PATH_BLOCK_END, profile_text)

    def test_repeated_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._fake_skill(directory)
            home = Path(directory) / "home"
            profile = home / ".zprofile"
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
                first = ensure_installed(
                    system="Darwin",
                    machine="arm64",
                    skill_root=root,
                    home=home,
                    profiles=[profile],
                )
                second = ensure_installed(
                    system="Darwin",
                    machine="arm64",
                    skill_root=root,
                    home=home,
                    profiles=[profile],
                )
            self.assertEqual(first["installedPath"], second["installedPath"])
            self.assertEqual(profile.read_text(encoding="utf-8").count(PATH_BLOCK_START), 1)

    def test_bundled_windows_binary_installs_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            result = ensure_installed(
                system="Windows",
                machine="AMD64",
                skill_root=Path(__file__).resolve().parents[1],
                install_dir=Path(directory) / "bin",
                persist_path=False,
                verify=False,
            )
            installed = Path(str(result["installedPath"]))
            bundled = Path(__file__).resolve().parents[1] / "cli" / "windows-x64" / "lululab.exe"
            self.assertEqual(installed.read_bytes(), bundled.read_bytes())

    def test_rejects_tampered_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skill"
            binary = root / "cli" / "windows-x64" / "lululab.exe"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"MZtampered")
            with self.assertRaisesRegex(installer.InstallError, "校验失败"):
                installer.bundled_cli_path(system="Windows", machine="AMD64", skill_root=root)


if __name__ == "__main__":
    unittest.main()
