#!/usr/bin/env python3
"""Build a validated product_brief 产品卡 JSON string."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from core.product_builder import build_product_brief_string  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-selling-point", action="append", default=[])
    parser.add_argument("--product-material-fact", action="append", default=[])
    parser.add_argument("--ai-supplement", action="append", default=[])
    parser.add_argument("--product-name", default="")
    parser.add_argument("--appearance", default="")
    parser.add_argument("--product-color", default="")
    parser.add_argument("--material", default="")
    parser.add_argument("--logo", default="")
    parser.add_argument("--structure", default="")
    parser.add_argument("--usage", default="")
    parser.add_argument("--forbidden-change", action="append", default=[])
    parser.add_argument("--compact", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        output = build_product_brief_string(
            user_selling_points=args.user_selling_point,
            product_material_facts=args.product_material_fact,
            ai_supplements=args.ai_supplement,
            product_name=args.product_name,
            appearance=args.appearance,
            product_color=args.product_color,
            material=args.material,
            logo=args.logo,
            structure=args.structure,
            usage=args.usage,
            forbidden_changes=args.forbidden_change,
            pretty=not args.compact,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
