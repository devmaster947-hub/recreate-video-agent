#!/usr/bin/env python3
"""Install the bundled LZStudio CLI into the current user's application directory."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping, Sequence


SKILL_ROOT = Path(__file__).resolve().parent.parent
PATH_BLOCK_START = "# >>> lzstudio managed path >>>"
PATH_BLOCK_END = "# <<< lzstudio managed path <<<"


class InstallError(RuntimeError):
    """Raise when the bundled CLI cannot be installed or accepted safely."""


def bundled_cli_path(
    *,
    system: str | None = None,
    machine: str | None = None,
    skill_root: str | os.PathLike[str] | None = None,
) -> Path:
    system_name = system or platform.system()
    architecture = (machine or platform.machine()).lower()
    root = Path(skill_root).resolve() if skill_root else SKILL_ROOT
    if system_name == "Darwin" and architecture in {"arm64", "aarch64"}:
        candidate = root / "cli" / "macos-arm64" / "lzstudio"
    elif system_name == "Windows" and architecture in {"amd64", "x86_64", "x64"}:
        candidate = root / "cli" / "windows-x64" / "lzstudio.exe"
    else:
        raise InstallError("当前平台不支持，请使用 macOS Apple Silicon 或 Windows x64。")
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        raise InstallError(f"技能内置 LZStudio CLI 缺失：{candidate}")
    return candidate


def default_install_dir(
    *,
    system: str | None = None,
    home: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    system_name = system or platform.system()
    user_home = Path(home).expanduser() if home is not None else Path.home()
    environment = os.environ if environ is None else environ
    if system_name == "Darwin":
        return (user_home / "Applications" / "LZStudio" / "bin").resolve()
    if system_name == "Windows":
        local_app_data = environment.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data) if local_app_data else user_home / "AppData" / "Local"
        return (base / "Programs" / "LZStudio" / "bin").resolve()
    raise InstallError("当前平台不支持，请使用 macOS Apple Silicon 或 Windows x64。")


def _replace_managed_block(text: str, block: str) -> str:
    pattern_start = text.find(PATH_BLOCK_START)
    pattern_end = text.find(PATH_BLOCK_END)
    if pattern_start >= 0 and pattern_end >= pattern_start:
        pattern_end += len(PATH_BLOCK_END)
        before = text[:pattern_start].rstrip("\n")
        after = text[pattern_end:].lstrip("\n")
        parts = [part for part in (before, block, after) if part]
        return "\n\n".join(parts) + "\n"
    prefix = text.rstrip("\n")
    return (prefix + "\n\n" if prefix else "") + block + "\n"


def _persist_macos_path(bin_dir: Path, profiles: Sequence[Path]) -> list[str]:
    quoted = shlex.quote(str(bin_dir))
    block = (
        f"{PATH_BLOCK_START}\n"
        f"export PATH={quoted}:\"$PATH\"\n"
        f"{PATH_BLOCK_END}"
    )
    updated: list[str] = []
    for profile in profiles:
        profile.parent.mkdir(parents=True, exist_ok=True)
        existing = profile.read_text(encoding="utf-8") if profile.exists() else ""
        desired = _replace_managed_block(existing, block)
        if desired != existing:
            profile.write_text(desired, encoding="utf-8")
            updated.append(str(profile))
    return updated


def _persist_windows_path(bin_dir: Path) -> list[str]:
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError as exc:
        raise InstallError("无法加载 Windows 用户 PATH 配置模块。") from exc
    key_path = r"Environment"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        try:
            current, _ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current = ""
        entries = [item for item in str(current).split(";") if item]
        normalized = os.path.normcase(str(bin_dir))
        if normalized not in {os.path.normcase(item.rstrip("\\/")) for item in entries}:
            entries.append(str(bin_dir))
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(entries))
            return ["HKCU\\Environment\\Path"]
    return []


def persist_user_path(
    bin_dir: Path,
    *,
    system: str | None = None,
    home: str | os.PathLike[str] | None = None,
    profiles: Sequence[Path] | None = None,
) -> list[str]:
    system_name = system or platform.system()
    user_home = Path(home).expanduser() if home is not None else Path.home()
    current = [item for item in os.environ.get("PATH", "").split(os.pathsep) if item]
    if os.path.normcase(str(bin_dir)) not in {os.path.normcase(item) for item in current}:
        os.environ["PATH"] = os.pathsep.join([str(bin_dir), *current])
    if system_name == "Darwin":
        targets = list(profiles) if profiles is not None else [
            user_home / ".zprofile",
            user_home / ".bash_profile",
        ]
        return _persist_macos_path(bin_dir, targets)
    if system_name == "Windows":
        return _persist_windows_path(bin_dir)
    raise InstallError("当前平台不支持，无法配置 PATH。")


def _copy_atomic(source: Path, target: Path, *, system: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".lzstudio-install-", dir=target.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            with source.open("rb") as handle:
                shutil.copyfileobj(handle, temporary)
        if system != "Windows":
            temporary_path.chmod(0o755)
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def verify_version(*, timeout: float = 30.0) -> str:
    """Run the required acceptance command through PATH."""
    try:
        completed = subprocess.run(
            ["lzstudio", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError(f"验收命令 `lzstudio --version` 无法启动：{exc}") from None
    output = (completed.stdout.strip() or completed.stderr.strip()).strip()
    if completed.returncode != 0 or not output:
        raise InstallError(
            f"验收命令 `lzstudio --version` 失败（退出码 {completed.returncode}）："
            f"{output or '未返回版本信息'}"
        )
    return output


def ensure_installed(
    *,
    system: str | None = None,
    machine: str | None = None,
    skill_root: str | os.PathLike[str] | None = None,
    install_dir: str | os.PathLike[str] | None = None,
    home: str | os.PathLike[str] | None = None,
    profiles: Sequence[Path] | None = None,
    persist_path: bool = True,
    verify: bool = True,
) -> dict[str, object]:
    system_name = system or platform.system()
    source = bundled_cli_path(system=system_name, machine=machine, skill_root=skill_root)
    destination_dir = (
        Path(install_dir).expanduser().resolve()
        if install_dir is not None
        else default_install_dir(system=system_name, home=home)
    )
    executable_name = "lzstudio.exe" if system_name == "Windows" else "lzstudio"
    target = destination_dir / executable_name
    if not target.is_file() or target.read_bytes() != source.read_bytes():
        _copy_atomic(source, target, system=system_name)
    elif system_name != "Windows" and not os.access(target, os.X_OK):
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    path_updates = (
        persist_user_path(
            destination_dir,
            system=system_name,
            home=home,
            profiles=profiles,
        )
        if persist_path
        else []
    )
    if not persist_path:
        current = [item for item in os.environ.get("PATH", "").split(os.pathsep) if item]
        if os.path.normcase(str(destination_dir)) not in {
            os.path.normcase(item) for item in current
        }:
            os.environ["PATH"] = os.pathsep.join([str(destination_dir), *current])
    version = verify_version() if verify else ""
    return {
        "installedPath": str(target),
        "pathUpdates": path_updates,
        "acceptanceCommand": "lzstudio --version",
        "version": version,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", help="仅用于测试或受控部署的安装目录覆盖。")
    args = parser.parse_args()
    try:
        result = ensure_installed(install_dir=args.install_dir)
    except (InstallError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
