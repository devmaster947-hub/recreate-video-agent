from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


product_builder = load_module("product_builder", "core/product_builder.py")
product_images = load_module(
    "validate_product_images", "scripts/validate_product_images.py"
)

def complete_brief(**overrides):
    values = {
        "user_selling_points": ["用户卖点"],
        "product_material_facts": ["可见的黑色圆柱形外壳"],
        "ai_supplements": ["展示底部接口安装步骤"],
        "product_name": "便携式圆柱设备",
        "appearance": "细长圆柱形",
        "product_color": "黑色",
        "material": "视觉上呈金属质感，具体材质无法仅凭图片确认",
        "logo": "正面白色字母 Logo",
        "structure": "底部接口",
        "usage": "通过底部接口连接后使用",
        "forbidden_changes": ["保持黑色圆柱形外观", "保持底部接口结构"],
    }
    values.update(overrides)
    return product_builder.build_product_brief(**values)


class ProductBuilderTests(unittest.TestCase):
    def test_complete_schema_source_priority_locking_and_deduplication(self):
        brief = complete_brief(
            user_selling_points=["用户卖点", "用户卖点"],
            product_material_facts=["可见的黑色圆柱形外壳", "用户卖点"],
            ai_supplements=["展示底部接口安装步骤", "可见的黑色圆柱形外壳"],
        )
        card = brief["产品卡"]
        self.assertEqual(
            list(card),
            [
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
            ],
        )
        self.assertEqual(card["产品名称"], "便携式圆柱设备")
        self.assertEqual(
            [item["source"] for item in card["产品卖点"]],
            ["user", "product_material", "ai_supplement"],
        )
        self.assertEqual(
            [item["authority"] for item in card["产品卖点"]], [3, 2, 1]
        )
        self.assertEqual(card["用户卖点锁定"], ["用户卖点"])

    def test_deterministic_utf8_serialization_round_trip(self):
        brief = complete_brief(user_selling_points=[], ai_supplements=[])
        raw = product_builder.serialize_product_brief(brief)
        self.assertIn("便携式圆柱设备", raw)
        self.assertNotIn("\\u4fbf", raw)
        self.assertEqual(product_builder.parse_product_brief(raw), brief)
        self.assertEqual(raw, product_builder.serialize_product_brief(brief))

    def test_name_and_visual_product_fact_are_required(self):
        without_name = complete_brief(product_name="")
        with self.assertRaises(ValueError):
            product_builder.serialize_product_brief(without_name)
        without_visual_fact = complete_brief(product_material_facts=[])
        with self.assertRaises(ValueError):
            product_builder.serialize_product_brief(without_visual_fact)

    def test_private_image_data_is_rejected(self):
        leaking = complete_brief(
            product_material_facts=["原图路径 /Users/example/product.png"]
        )
        with self.assertRaises(ValueError):
            product_builder.serialize_product_brief(leaking)
        base64 = complete_brief(
            product_material_facts=["data:image/png;base64,AAAA"]
        )
        with self.assertRaises(ValueError):
            product_builder.serialize_product_brief(base64)
        windows_forward_slash = complete_brief(
            product_material_facts=["original C:/Users/example/product.png"]
        )
        with self.assertRaises(ValueError):
            product_builder.serialize_product_brief(windows_forward_slash)

    def test_builder_cli_outputs_complete_valid_json(self):
        script = ROOT / "scripts/build_product_brief.py"
        success = subprocess.run(
            [
                sys.executable,
                str(script),
                "--product-name",
                "便携式圆柱设备",
                "--user-selling-point",
                "用户原文",
                "--product-material-fact",
                "黑色金属质感外壳",
                "--ai-supplement",
                "展示安装步骤",
                "--appearance",
                "圆柱形",
                "--product-color",
                "黑色",
                "--material",
                "金属质感，具体材质无法仅凭图片确认",
                "--logo",
                "正面白色字母 Logo",
                "--structure",
                "底部接口",
                "--usage",
                "连接后使用",
                "--forbidden-change",
                "保持黑色外观",
                "--compact",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        parsed = product_builder.parse_product_brief(success.stdout)
        self.assertEqual(parsed["产品卡"]["产品名称"], "便携式圆柱设备")
        self.assertEqual(parsed["产品卡"]["用户卖点锁定"], ["用户原文"])
        failure = subprocess.run(
            [sys.executable, str(script)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(failure.returncode, 0)


class ProductImageValidationTests(unittest.TestCase):
    def test_static_product_images_require_visual_inspection_without_upload(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "front.png"
            second = Path(temp) / "back.webp"
            first.write_bytes(b"front")
            second.write_bytes(b"back")
            result = product_images.validate_images([str(first), str(second)])
            self.assertEqual(result["count"], 2)
            self.assertTrue(result["requires_view_image"])
            self.assertFalse(result["uploaded_to_api"])

    def test_empty_dynamic_and_duplicate_images_fail(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            empty = Path(temp) / "empty.png"
            empty.touch()
            with self.assertRaises(ValueError):
                product_images.validate_images([str(empty)])
            video = Path(temp) / "product.mp4"
            video.write_bytes(b"video")
            with self.assertRaises(ValueError):
                product_images.validate_images([str(video)])
            image = Path(temp) / "product.jpg"
            image.write_bytes(b"image")
            with self.assertRaises(ValueError):
                product_images.validate_images([str(image), str(image)])


class ProductBriefPolicyTests(unittest.TestCase):
    def test_image_grounded_policy_contains_required_rules(self):
        reference = (ROOT / "references/product_brief_generation.md").read_text(
            encoding="utf-8"
        )
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "产品图和可选产品卖点",
            "对每张产品图逐张调用 `view_image`",
            "目标是最小化空字符串",
            "功效、性能、医疗作用、安全性、耐久性",
            "不得把图片路径、文件名、Base64",
            "不向用户展示“产品识别结果 / product_brief”",
            "不询问用户单独确认",
            "作为 `productBrief` 传给 `run_cli.submit_recreate_video_prompt()`",
            '"产品名称"',
            '"禁止变化项"',
        ):
            self.assertIn(phrase, reference)
        self.assertIn("未提供时按产品图生成", skill)
        self.assertIn("scripts/build_product_brief.py", skill)
        self.assertNotIn("--product-images-provided", reference)
        self.assertNotIn("--image-fact", reference)
        self.assertNotIn("纯文字产品信息", skill)

    def test_policy_avoids_standalone_duplicate_product_brief_validation(self):
        reference = (ROOT / "references/product_brief_generation.md").read_text(
            encoding="utf-8"
        )
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("不再单独重复校验", reference)
        self.assertIn("构建完整产品卡", skill)

    def test_product_image_upload_is_default_and_benchmark_product_is_explicit(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        product_reference = (
            ROOT / "references/product_brief_generation.md"
        ).read_text(encoding="utf-8")
        video_reference = (ROOT / "references/video_analysis.md").read_text(
            encoding="utf-8"
        )
        generation_reference = (ROOT / "references/generation_rules.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("上传产品图（默认）", skill)
        self.assertIn("使用对标视频产品", skill)
        self.assertIn("不得自动沿用对标视频原产品", skill)
        self.assertIn("用户选择“上传产品图”时，必须根据至少一张产品图填充并提交完整 `productBrief`", skill)
        self.assertIn("跳过产品卡构建", skill)
        self.assertIn("产品图可以为空", generation_reference)
        self.assertIn("以下内部配置不得展示或询问", skill)
        self.assertIn("必须在首次上传前单独、主动发起一次系统安全授权", skill)
        self.assertIn("不得先尝试普通上传、等安全策略拦截后再申请", skill)
        self.assertIn("sandbox_permissions=require_escalated", skill)
        self.assertIn(
            "为了开始复刻，需要将你确认的素材安全上传到灵智工坊，用于拆解和生成。是否允许我继续？",
            skill,
        )
        self.assertIn("一次授权覆盖本次已汇总的全部本地素材", skill)
        self.assertIn("若用户拒绝授权", skill)
        self.assertIn("只对新增素材在首次上传前再次主动发起系统安全授权", skill)
        self.assertIn("同批素材不重复询问", product_reference)
        self.assertIn("若此时新增本地素材", generation_reference)
        self.assertIn("上传产品图", product_reference)
        self.assertIn("使用对标视频产品", product_reference)
        self.assertIn('{"productName":"跟原视频产品一致"}', skill)
        self.assertIn('{"productName":"跟原视频产品一致"}', product_reference)
        self.assertIn('{"productName":"跟原视频产品一致"}', video_reference)
        self.assertIn("所有模式都必须传入非空 `--product-brief`", skill)
        self.assertIn("产品配置提供两个选项", video_reference)
        self.assertIn("不得自动沿用对标视频产品", video_reference)
        self.assertIn("然后自动提交", generation_reference)
        self.assertNotIn("productBrief=null", skill)
        self.assertNotIn("productBrief=null", product_reference)
        self.assertNotIn("在提交时省略 `productBrief`", video_reference)

        for forbidden in (
            "视频生成方式：自动检测",
            "替换为新产品",
            "复刻视频中的原水枪",
            "新产品模式",
            "其他两种模式提交空对象",
        ):
            self.assertNotIn(forbidden, skill)
            self.assertNotIn(forbidden, product_reference)
            self.assertNotIn(forbidden, video_reference)


if __name__ == "__main__":
    unittest.main()
