#!/usr/bin/env python3
"""Create and restore transactional snapshots for approved skill optimizations."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

try:
    from scripts import generation_manifest
except ModuleNotFoundError:
    import generation_manifest


def matching_record(manifest: str | Path, candidate_id: str, proposal: str | Path) -> tuple[dict, dict, Path, str]:
    manifest_path = Path(manifest).expanduser().resolve()
    proposal_path = Path(proposal).expanduser().resolve()
    digest = generation_manifest.file_sha256(proposal_path)
    data = generation_manifest.load_manifest(manifest_path)
    record = next((item for item in reversed(data.get("skillOptimizations", [])) if item.get("candidateId") == candidate_id and item.get("proposalSha256") == digest), None)
    if record is None or record.get("status") != "proposed":
        raise ValueError("没有可应用的匹配技能优化方案，或方案已失效。")
    return data, record, manifest_path, digest


def current_fingerprints(record: dict) -> dict[str, str]:
    paths = list(record.get("sourceFileFingerprints", {}).keys())
    return generation_manifest.skill_file_fingerprints(paths)


def snapshot(manifest: str | Path, candidate_id: str, proposal: str | Path) -> Path:
    data, record, manifest_path, digest = matching_record(manifest, candidate_id, proposal)
    expected = dict(record.get("sourceFileFingerprints", {}))
    if current_fingerprints(record) != expected:
        generation_manifest.set_skill_optimization_result(manifest_path, candidate_id, str(proposal), "stale")
        raise ValueError("目标技能文件已变化，旧的优化确认失效；方案已标记 stale。")
    transaction_root = manifest_path.parent / "review" / "skill-optimization" / str(record["proposalId"])
    backup_root = transaction_root / "backup"
    backup_root.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    for relative, original_digest in expected.items():
        source = generation_manifest.SKILL_ROOT / relative
        if original_digest == "missing":
            missing.append(relative)
            continue
        destination = backup_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    transaction = transaction_root / "transaction.json"
    transaction.write_text(json.dumps({
        "proposalSha256": digest,
        "candidateId": candidate_id,
        "skillRoot": str(generation_manifest.SKILL_ROOT),
        "sourceFileFingerprints": expected,
        "missingFiles": missing,
        "backupRoot": str(backup_root),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record["transactionFile"] = str(transaction)
    record["snapshotAt"] = generation_manifest.now()
    generation_manifest.save_manifest(manifest_path, data)
    return transaction


def rollback(manifest: str | Path, candidate_id: str, proposal: str | Path) -> Path:
    data, record, manifest_path, _digest = matching_record(manifest, candidate_id, proposal)
    transaction_value = record.get("transactionFile")
    if not transaction_value:
        raise ValueError("技能优化方案没有事务快照。")
    transaction = Path(str(transaction_value)).expanduser().resolve()
    details = json.loads(transaction.read_text(encoding="utf-8"))
    backup_root = Path(details["backupRoot"]).resolve()
    expected = dict(details["sourceFileFingerprints"])
    missing = set(details.get("missingFiles", []))
    for relative, original_digest in expected.items():
        target = generation_manifest.SKILL_ROOT / generation_manifest.optimization_relative_path(relative)
        if relative in missing or original_digest == "missing":
            if target.exists():
                if not target.is_file():
                    raise ValueError(f"无法安全回滚非文件路径：{target}")
                target.unlink()
            continue
        source = backup_root / relative
        if not source.is_file() or generation_manifest.file_sha256(source) != original_digest:
            raise ValueError(f"技能优化备份缺失或摘要错误：{relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    restored = generation_manifest.skill_file_fingerprints(list(expected.keys()))
    if restored != expected:
        raise ValueError("技能优化自动回滚后文件摘要仍不一致。")
    record["rollbackVerifiedAt"] = generation_manifest.now()
    generation_manifest.save_manifest(manifest_path, data)
    return transaction


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "rollback"):
        command = sub.add_parser(name)
        command.add_argument("--manifest", required=True)
        command.add_argument("--candidate-id", required=True)
        command.add_argument("--proposal", required=True)
    args = parser.parse_args()
    try:
        result = snapshot(args.manifest, args.candidate_id, args.proposal) if args.command == "snapshot" else rollback(args.manifest, args.candidate_id, args.proposal)
        print(json.dumps({"ok": True, "transaction": str(result)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
