#!/usr/bin/env python3
"""Build, validate, and serialize a sourced product_brief product card."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable


SOURCE_AUTHORITY = {
    "user": 3,
    "product_material": 2,
    "ai_supplement": 1,
}
LOCAL_PATH_PATTERN = re.compile(r"(?:file://|/Users/|/home/|[A-Za-z]:[\\/])")
PRODUCT_CARD_FIELDS = (
    "产品名称",
    "外观",
    "产品颜色",
    "材质",
    "Logo",
    "结构",
    "使用方式",
    "产品卖点",
    "用户卖点锁定",
    "禁止变化项",
)
SCALAR_FIELDS = PRODUCT_CARD_FIELDS[:7]


def _clean_values(values: Iterable[str] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value).strip()
        if text and text not in seen:
            cleaned.append(text)
            seen.add(text)
    return cleaned


def _clean_scalar(value: str | None) -> str:
    return "" if value is None else str(value).strip()


def _items(values: Iterable[str] | None, source: str) -> list[dict[str, Any]]:
    return [
        {
            "text": value,
            "source": source,
            "authority": SOURCE_AUTHORITY[source],
        }
        for value in _clean_values(values)
    ]


def build_product_brief(
    user_selling_points: Iterable[str] | None = None,
    product_material_facts: Iterable[str] | None = None,
    ai_supplements: Iterable[str] | None = None,
    *,
    product_name: str = "",
    appearance: str = "",
    product_color: str = "",
    material: str = "",
    logo: str = "",
    structure: str = "",
    usage: str = "",
    forbidden_changes: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build the exact product_brief schema while preserving claim authority."""
    user = _items(user_selling_points, "user")
    image = _items(product_material_facts, "product_material")
    ai = _items(ai_supplements, "ai_supplement")
    retained: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in user + image + ai:
        if item["text"] not in seen:
            retained.append(item)
            seen.add(item["text"])
    return {
        "产品卡": {
            "产品名称": _clean_scalar(product_name),
            "外观": _clean_scalar(appearance),
            "产品颜色": _clean_scalar(product_color),
            "材质": _clean_scalar(material),
            "Logo": _clean_scalar(logo),
            "结构": _clean_scalar(structure),
            "使用方式": _clean_scalar(usage),
            "产品卖点": retained,
            "用户卖点锁定": [item["text"] for item in user],
            "禁止变化项": _clean_values(forbidden_changes),
        }
    }


def _contains_private_data(text: str) -> bool:
    return "data:image" in text.lower() or bool(LOCAL_PATH_PATTERN.search(text))


def validate_product_brief(brief: Any) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(brief, dict):
        return False, ["product_brief must be a JSON object."]
    if set(brief) != {"产品卡"}:
        return False, ["product_brief must contain only the 产品卡 object."]

    card = brief.get("产品卡")
    if not isinstance(card, dict):
        return False, ["product_brief.产品卡 must be a JSON object."]
    if tuple(card) != PRODUCT_CARD_FIELDS:
        errors.append(
            "产品卡 keys and order must be 产品名称, 外观, 产品颜色, 材质, Logo, 结构, 使用方式, 产品卖点, 用户卖点锁定, 禁止变化项."
        )

    for field in SCALAR_FIELDS:
        value = card.get(field)
        if not isinstance(value, str):
            errors.append(f"产品卡.{field} must be a string.")
        elif field == "产品名称" and not value.strip():
            errors.append("产品卡.产品名称 must be a non-empty image-grounded name or conservative category name.")
        elif value != value.strip():
            errors.append(f"产品卡.{field} must be trimmed.")
        elif _contains_private_data(value):
            errors.append(f"产品卡.{field} must not contain image data or local paths.")

    selling_points = card.get("产品卖点")
    if not isinstance(selling_points, list) or not selling_points:
        errors.append("产品卡.产品卖点 must be a non-empty list.")
        selling_points = []

    seen: set[str] = set()
    user_items: list[str] = []
    product_material_item_count = 0
    previous_authority = 4
    for index, item in enumerate(selling_points):
        prefix = f"产品卡.产品卖点[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        if set(item) != {"text", "source", "authority"}:
            errors.append(f"{prefix} keys must be text, source, and authority.")
        text = item.get("text")
        source = item.get("source")
        authority = item.get("authority")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{prefix}.text must be non-empty text.")
            continue
        if text != text.strip():
            errors.append(f"{prefix}.text must be trimmed.")
        if text in seen:
            errors.append(f"Duplicate product_brief text: {text}")
        seen.add(text)
        if _contains_private_data(text):
            errors.append(f"{prefix} must not contain image data or local paths.")
        if source not in SOURCE_AUTHORITY:
            errors.append(f"{prefix}.source is unsupported: {source}")
            continue
        expected = SOURCE_AUTHORITY[source]
        if authority != expected:
            errors.append(f"{prefix}.authority must be {expected} for {source}.")
        if isinstance(authority, int) and authority > previous_authority:
            errors.append("产品卖点 must follow descending source authority.")
        if isinstance(authority, int):
            previous_authority = authority
        if source == "user":
            user_items.append(text)
        if source == "product_material":
            product_material_item_count += 1

    if selling_points and product_material_item_count == 0:
        errors.append(
            "product_brief requires at least one visible product-material fact from the required product images."
        )

    locked = card.get("用户卖点锁定")
    if not isinstance(locked, list) or not all(
        isinstance(value, str) and value.strip() for value in locked
    ):
        errors.append("产品卡.用户卖点锁定 must be a list of non-empty strings.")
    elif locked != user_items:
        errors.append("产品卡.用户卖点锁定 must exactly match ordered user-source claims.")

    forbidden = card.get("禁止变化项")
    if not isinstance(forbidden, list) or not all(
        isinstance(value, str) and value.strip() for value in forbidden
    ):
        errors.append("产品卡.禁止变化项 must be a list of non-empty strings.")
    elif forbidden != _clean_values(forbidden):
        errors.append("产品卡.禁止变化项 must be trimmed and deduplicated.")
    elif any(_contains_private_data(value) for value in forbidden):
        errors.append("产品卡.禁止变化项 must not contain image data or local paths.")
    return not errors, errors


def serialize_product_brief(brief: dict[str, Any], *, pretty: bool = False) -> str:
    ok, errors = validate_product_brief(brief)
    if not ok:
        raise ValueError("; ".join(errors))
    if pretty:
        return json.dumps(brief, ensure_ascii=False, indent=2)
    return json.dumps(brief, ensure_ascii=False, separators=(",", ":"))


def parse_product_brief(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("product_brief string must be non-empty.")
    try:
        brief = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"product_brief is not valid JSON: {exc}") from exc
    ok, errors = validate_product_brief(brief)
    if not ok:
        raise ValueError("; ".join(errors))
    return brief


def build_product_brief_string(
    user_selling_points: Iterable[str] | None = None,
    product_material_facts: Iterable[str] | None = None,
    ai_supplements: Iterable[str] | None = None,
    *,
    product_name: str = "",
    appearance: str = "",
    product_color: str = "",
    material: str = "",
    logo: str = "",
    structure: str = "",
    usage: str = "",
    forbidden_changes: Iterable[str] | None = None,
    pretty: bool = False,
) -> str:
    return serialize_product_brief(
        build_product_brief(
            user_selling_points,
            product_material_facts,
            ai_supplements,
            product_name=product_name,
            appearance=appearance,
            product_color=product_color,
            material=material,
            logo=logo,
            structure=structure,
            usage=usage,
            forbidden_changes=forbidden_changes,
        ),
        pretty=pretty,
    )
