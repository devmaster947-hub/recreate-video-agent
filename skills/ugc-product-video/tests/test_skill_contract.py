from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL_TEXT = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
OPENAI_YAML = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")


class BatchSkillContractTests(unittest.TestCase):
    def test_api_key_request_mentions_studio_url(self):
        self.assertIn(
            "https://studio.lingzhiai.com.cn/",
            SKILL_TEXT,
        )
        self.assertIn("请求用户提供 API Key 时，必须同时提醒用户", SKILL_TEXT)

    def test_confirmation_includes_default_video_count(self):
        self.assertIn(
            "生成视频数量：1 条（默认，可选 1–10 条）",
            SKILL_TEXT,
        )
        self.assertIn("生成视频数量必须是 1～10 的整数", SKILL_TEXT)

    def test_video_count_is_not_added_to_user_config(self):
        match = re.search(
            r"### `userConfig`.*?```json\s*(\{.*?\})\s*```",
            SKILL_TEXT,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        user_config = json.loads(match.group(1))
        self.assertEqual(
            set(user_config),
            {"videoModel", "duration", "videoStyle"},
        )
        self.assertIn("不得将数量写入 `userConfig`", SKILL_TEXT)

    def test_batch_execution_and_outputs_are_defined(self):
        for text in (
            "N 个独立视频工作流",
            "最多同时运行 3 条完整视频工作流",
            "video_01/final_video.mp4",
            "不得跨视频拼接",
            "继续完成其他视频",
            "不得自动重试",
        ):
            with self.subTest(text=text):
                self.assertIn(text, SKILL_TEXT)

    def test_single_video_behavior_is_preserved(self):
        self.assertIn(
            "生成 1 条视频时，最终文件保持为 `final_video.mp4`",
            SKILL_TEXT,
        )

    def test_ui_metadata_mentions_batch_generation(self):
        self.assertIn("multiple", OPENAI_YAML.lower())

    def test_credit_usage_is_mandatory_in_final_output(self):
        for text in (
            "get_credit_balance()",
            "本次消费：{consumedCredits} 积分",
            "剩余积分：{endBalance} 积分",
            "无论全部成功、部分失败或全部失败",
            "查询余额失败不得阻止",
        ):
            with self.subTest(text=text):
                self.assertIn(text, SKILL_TEXT)


if __name__ == "__main__":
    unittest.main()
