#!/usr/bin/env python3
"""Validate source-person to uploaded-Creator replacement mappings."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable


SOURCE_ID = re.compile(r"^source-creator-[1-9][0-9]*$")
TARGET_ID = re.compile(r"^creator-[1-9][0-9]*$")
ROLES = {"primary", "supporting", "background"}
ACTIONS = {"replace", "keep"}


def normalize_creator_replacement_map(
    value: Any,
    *,
    available_target_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Return a strict one-source-to-one-target mapping without identity cloning."""
    if not isinstance(value, list) or not value:
        raise ValueError("creatorReplacementMap必须是非空数组。")
    allowed_targets = None if available_target_ids is None else {str(item) for item in available_target_ids}
    normalized: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    target_ids: set[str] = set()
    primary_sources: list[str] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("creatorReplacementMap每项必须是对象。")
        source_id = str(raw.get("sourceCreatorId", "")).strip()
        role = str(raw.get("role", "")).strip().lower()
        action = str(raw.get("action", "")).strip().lower()
        target_id = str(raw.get("targetCreatorId", "")).strip()
        if not SOURCE_ID.fullmatch(source_id) or source_id in source_ids:
            raise ValueError("sourceCreatorId必须是唯一的source-creator-N。")
        if role not in ROLES:
            raise ValueError(f"{source_id}.role必须是primary、supporting或background。")
        if action not in ACTIONS:
            raise ValueError(f"{source_id}.action必须是replace或keep。")
        if role == "primary":
            primary_sources.append(source_id)
        if action == "replace":
            if not TARGET_ID.fullmatch(target_id):
                raise ValueError(f"{source_id}替换时必须提供creator-N格式的targetCreatorId。")
            if allowed_targets is not None and target_id not in allowed_targets:
                raise ValueError(f"{source_id}引用了未登记的目标达人：{target_id}")
            if target_id in target_ids:
                raise ValueError(f"同一目标达人图不得映射多个原片人物：{target_id}")
            target_ids.add(target_id)
        elif target_id:
            raise ValueError(f"{source_id}保留原人物时不得提供targetCreatorId。")
        source_ids.add(source_id)
        item = {"sourceCreatorId": source_id, "role": role, "action": action}
        if target_id:
            item["targetCreatorId"] = target_id
        if str(raw.get("description", "")).strip():
            item["description"] = str(raw["description"]).strip()
        normalized.append(item)
    if len(primary_sources) != 1:
        raise ValueError("多人视频必须且只能标记一个primary核心达人。")
    if len(target_ids) == 1 and len(normalized) > 1:
        replaced = [item for item in normalized if item["action"] == "replace"]
        if len(replaced) != 1 or replaced[0]["role"] != "primary":
            raise ValueError("只有一张达人图时只能替换primary核心达人，其他人物必须keep。")
    return normalized


def mapping_by_source(value: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["sourceCreatorId"]): item for item in value}


def public_creator_replacement_map(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only server-safe mapping fields."""
    keys = ("sourceCreatorId", "role", "action", "targetCreatorId", "description")
    return [{key: item[key] for key in keys if key in item} for item in value]


if __name__ == "__main__":
    raise SystemExit("请通过generation_manifest.py登记达人映射。")
