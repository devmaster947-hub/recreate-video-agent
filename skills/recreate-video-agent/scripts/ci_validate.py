"""Validate a public Skill source tree without contacting paid services."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NAME = ROOT.name
REQUIRED = ("SKILL.md", "README.md", "README.zh-CN.md", "LICENSE", ".gitignore", "scripts/lululab_cli.mjs")
FORBIDDEN_PARTS = {".venv", ".pytest_cache", "__pycache__", "jobs", "output", "outputs", "dist", "node_modules"}
FORBIDDEN_FILES = {".env", ".DS_Store"}
SECRET = re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bsk-[A-Za-z0-9_-]{20,}\b|\bAKIA[0-9A-Z]{16}\b")


def validate() -> None:
    for relative in REQUIRED:
        assert (ROOT / relative).is_file(), f"missing {relative}"
    source = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert source.startswith("---\n"), "SKILL.md needs YAML frontmatter"
    frontmatter = yaml.safe_load(source.split("---", 2)[1])
    assert isinstance(frontmatter, dict), "invalid frontmatter"
    assert frontmatter.get("name") == EXPECTED_NAME, "Skill name does not match root"
    assert isinstance(frontmatter.get("description"), str) and frontmatter["description"].strip(), "missing description"
    for relative in ("README.md", "README.zh-CN.md"):
        readme = (ROOT / relative).read_text(encoding="utf-8")
        assert "https://github.com/LuluLab-AI/" + EXPECTED_NAME in readme, f"wrong repository URL in {relative}"
        assert "SKILL.md" in readme, f"missing Skill entry in {relative}"
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(ROOT)
        assert not FORBIDDEN_PARTS.intersection(relative.parts), f"runtime file: {relative}"
        assert path.name not in FORBIDDEN_FILES, f"private file: {relative}"
        assert path.suffix.lower() not in {".mp4", ".mov", ".exe", ".pth", ".pyc"}, f"binary/media file: {relative}"
        if path.suffix.lower() in {".md", ".py", ".mjs", ".json", ".yaml", ".yml", ".sh", ".ps1"}:
            content = path.read_text(encoding="utf-8-sig")
            assert not SECRET.search(content), f"suspected credential in {relative}"
            assert "/Users/" + "matthew/" not in content and "C:/Users/" + "Administrator/" not in content, f"developer path in {relative}"
    for path in ROOT.rglob("*.mjs"):
        subprocess.run(["node", "--check", str(path)], check=True)
    print(f"validated {EXPECTED_NAME}")


if __name__ == "__main__":
    try:
        validate()
    except (AssertionError, subprocess.CalledProcessError) as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
