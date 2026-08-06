from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent


class GenerationRuleDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        cls.rules = (ROOT / "references" / "generation_rules.md").read_text(
            encoding="utf-8"
        )

    def test_successful_images_are_rendered_with_absolute_local_paths(self):
        for document in (self.skill, self.rules):
            self.assertIn("![Creator <creatorId> 达人图](<绝对本地路径>)", document)
            self.assertIn("![Segment <segmentId> 分镜图](<绝对本地路径>)", document)

    def test_rendering_does_not_enable_agent_visual_inspection(self):
        self.assertIn("不得调用 `view_image`", self.skill)
        self.assertIn("不调用 `view_image`", self.rules)
        self.assertIn("展示只供用户查看", self.rules)

    def test_cross_segment_retry_and_manual_recovery_are_explicit(self):
        self.assertIn("至少 2 个 Segment", self.rules)
        self.assertIn("最大尝试次数为 2", self.rules)
        self.assertIn("只有 `TaskFailedError`", self.rules)
        self.assertIn("不得替用户默认选择后者", self.rules)

    def test_prompts_lock_by_default_but_allow_explicit_user_edits(self):
        for document in (self.skill, self.rules):
            self.assertIn("默认锁定", document)
            self.assertIn("用户明确要求修改", document)
        self.assertNotIn("- 不修改 Prompt。", self.rules)

    def test_explicit_provider_and_segment_selection_are_supported(self):
        self.assertIn("--video-provider <auto|official_cli|lingzhi_cli>", self.skill)
        self.assertIn("--segment-id <ID>", self.skill)
        self.assertIn("只提交这些 Segment", self.rules)


if __name__ == "__main__":
    unittest.main()
