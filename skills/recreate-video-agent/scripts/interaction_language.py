#!/usr/bin/env python3
"""Resolve an explicitly handed-off Codex response locale; never inspect OS locale."""

from __future__ import annotations

from typing import Any


def normalize_locale(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    return raw.split(":", 1)[0].split("@", 1)[0].split(".", 1)[0].replace("_", "-").lower()


def resolve_interaction_locale(
    locale: Any = None, *, configured_locale: Any = None, previous_locale: Any = None,
) -> str:
    """Explicit reply request/selection > supplied Codex setting > saved locale > English.

    Callers supply these values. User message text, filenames, target language,
    and terminal environment variables are deliberately not inputs.
    """
    return (normalize_locale(locale) or normalize_locale(configured_locale)
            or normalize_locale(previous_locale) or "en")


def resolve_interaction_language(locale: Any = None) -> str:
    """Preserve every language instead of coercing all non-Chinese locales to English."""
    return resolve_interaction_locale(locale).split("-", 1)[0]
