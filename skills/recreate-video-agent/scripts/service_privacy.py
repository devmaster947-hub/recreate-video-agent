#!/usr/bin/env python3
"""Keep provider authorization metering out of client-visible artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SUPPORT_MESSAGE = "The service is unavailable. Please contact the administrator on WeChat: marlon1102."

_EXHAUSTED_CODES = {
    "402",
    "balance_not_enough",
    "credit_exhausted",
    "credit_insufficient",
    "insufficient_balance",
    "insufficient_credit",
    "insufficient_credits",
    "quota_exceeded",
    "quota_exhausted",
}
_EXHAUSTED_PATTERNS = (
    re.compile(r"\binsufficient[\s_-]+credits?\b", re.IGNORECASE),
    re.compile(r"\bcredits?[\s_-]+(?:are[\s_-]+)?(?:insufficient|exhausted|depleted)\b", re.IGNORECASE),
    re.compile(r"\bnot[\s_-]+enough[\s_-]+credits?\b", re.IGNORECASE),
    re.compile(r"\bout[\s_-]+of[\s_-]+credits?\b", re.IGNORECASE),
    re.compile(r"\b(?:quota|balance)[\s_-]+(?:exceeded|exhausted|insufficient|depleted)\b", re.IGNORECASE),
    re.compile(r"(?:积分|余额|额度)(?:不足|已用完|用完|耗尽|超出|已超限|超限)"),
)
_METERING_TERMS = re.compile(r"credits?|credit balance|积分|余额|额度", re.IGNORECASE)


class AuthorizationUnavailableError(RuntimeError):
    """Provider-side authorization is unavailable and requires administrator action."""


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9一-鿿]", "", str(value).lower())


def is_metering_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    return (
        "credit" in normalized
        or "积分" in normalized
        or normalized in {
            "balance",
            "remainingbalance",
            "quota",
            "remainingquota",
            "usagequota",
            "ledger",
            "ledgertype",
        }
    )


def is_authorization_unavailable(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = _normalized_key(key)
            if normalized in {"code", "errorcode", "statuscode", "httpstatus"}:
                code = str(item).strip().lower()
                if code in _EXHAUSTED_CODES:
                    return True
            if is_authorization_unavailable(item):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(is_authorization_unavailable(item) for item in value)
    if not isinstance(value, str):
        return False
    text = value.strip()
    normalized = text.lower().replace("-", "_").replace(" ", "_")
    return normalized in _EXHAUSTED_CODES or any(pattern.search(text) for pattern in _EXHAUSTED_PATTERNS)


def public_error(value: Any, fallback: str = "Service request failed.") -> str:
    if is_authorization_unavailable(value):
        return SUPPORT_MESSAGE
    text = str(value).strip() if value is not None else ""
    if not text:
        return fallback
    if _METERING_TERMS.search(text):
        return fallback
    return text


def raise_if_authorization_unavailable(value: Any) -> None:
    if is_authorization_unavailable(value):
        raise AuthorizationUnavailableError(SUPPORT_MESSAGE)


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: sanitize_payload(item)
            for key, item in value.items()
            if not is_metering_key(key)
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str) and is_authorization_unavailable(value):
        return SUPPORT_MESSAGE
    return value


def sanitize_json_file(path: str | Path, *, root: str | Path) -> bool:
    target = Path(path).expanduser().resolve()
    allowed_root = Path(root).expanduser().resolve()
    if target != allowed_root and allowed_root not in target.parents:
        return False
    if target.suffix.lower() != ".json" or not target.is_file():
        return False
    try:
        original = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    sanitized = sanitize_payload(original)
    if sanitized == original:
        return False
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(sanitized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return True
