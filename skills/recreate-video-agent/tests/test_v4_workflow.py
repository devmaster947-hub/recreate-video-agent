from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))

from scripts import benchmark_analysis, creator_mapping, generation_manifest, image_edit_workflow, prompt_preflight, quality_review, reference_audit, reference_board, run_cli, run_generation, segment_storyboards, server_prompt_workflow, skill_optimization, storyboard, storyboard_cells  # noqa: E402
from scripts.model_capabilities import minimum_segment_count, plan_segment_windows, validate_generation  # noqa: E402


FFMPEG = shutil.which("ffmpeg") or str(Path.home() / ".local" / "bin" / "ffmpeg")


def ffmpeg(*arguments: str) -> None:
    result = subprocess.run([FFMPEG, "-y", *arguments], text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])


def anchors(start: float, end: float, *, first: str, product: bool = False, creators: list[str] | None = None) -> list[dict]:
    values = []
    for index in range(16):
        values.append({
            "timestamp": round(start + (end - start) * index / 16, 3),
            "shotId": f"shot-{int(start)}-{index // 4 + 1}",
            "anchorRole": "hard" if index in (0, 4, 8, 12) else "soft",
            "eventType": first if index == 0 else ("cut" if index in (4, 8, 12) else "action_process"),
            "importance": 5,
            "productPresent": product,
            "creatorIds": creators or [],
            "interactionState": "show" if product else "",
        })
    return values


def segment_plan(windows: list[tuple[float, float]], *, product: bool = False, creators: list[str] | None = None) -> dict:
    return {
        "segments": [
            {
                "segmentId": index,
                "globalStart": start,
                "globalEnd": end,
                "anchors": anchors(start, end, first="first_frame" if index == 1 else "continuity_start", product=product, creators=creators),
            }
            for index, (start, end) in enumerate(windows, 1)
        ]
    }


def replacement_map(editable: set[int]) -> dict:
    return {
        "segmentId": 1,
        "replaceProduct": True,
        "replaceCreator": False,
        "cells": [
            {
                "index": index,
                "product": {"state": "partial" if index in editable else "none", "count": 1 if index in editable else 0, "replace": index in editable},
                "creator": {"state": "none", "count": 0, "replace": False},
                "interactionState": "held" if index in editable else "",
            }
            for index in range(1, 17)
        ],
    }


def valid_prompt(duration: int, *, creator: bool = False, product: bool = False) -> str:
    one = max(.2, round(duration * .25, 2))
    two = max(one + .1, round(duration * .7, 2))
    creator_line = "达人参考图仅负责锁定核心人物身份。\n" if creator else ""
    product_duty = "产品参考图仅负责锁定产品身份与外观。\n" if product else ""
    product_constraints = (
        "产品身份与外观严格以产品参考图为准。\n"
        "画面只出现产品参考图中的目标产品，不出现被替换产品的外观。\n"
        "不得增加资料未确认的产品功能、效果或营销承诺。\n"
        if product else ""
    )
    return (
        "参考素材职责：\n最终Segment故事板是本段最高优先级视觉结构参考。\n"
        + product_duty
        + creator_line
        + "前0.2秒严格从第一格开始。\n视觉和实体不变量：保持人物与产品数量。\n"
        + f"[0s-{one}s] 画面仅人物A，无产品，完成开始动作。\n[{one}s-{two}s] 画面仅人物A，无产品，完成动作过程。\n[{two}s-{duration}s] 画面仅人物A，无产品，到达结束状态。\n"
        + "禁止字幕、自动字幕、caption、UI文字、文字叠加和模型自行生成的水印。\n"
        + "生成单画面连续视频，不呈现故事板网格、分屏、画中画或拼贴。\n"
        + product_constraints
    )


def blueprint() -> dict:
    return {
        "videoBlueprint": {
            "基础信息": {}, "爆款逻辑": {}, "声音结构": {}, "逐镜头拆解": [],
            "原产品信息": {}, "视频元素": {"人物": [], "场景": [], "道具": []}, "爆款资产": [],
        }
    }


def optimization_proposal(candidate_id: str = "candidate-01") -> dict:
    return {
        "proposalVersion": "1.0",
        "candidateId": candidate_id,
        "targetSkill": "recreate-video-agent",
        "summary": "收紧可通用的首帧约束。",
        "issues": [
            {"issueId": "issue-first-frame", "sourceFindingIndices": [0], "hardFailureCodes": ["first_frame_structure_mismatch"], "classification": "skill_actionable", "evidence": "首帧结构偏移。", "reason": "现有参考职责缺少独立首帧输入。"},
            {"issueId": "issue-watermark", "sourceFindingIndices": [1], "hardFailureCodes": ["watermark_present"], "classification": "provider_limited", "evidence": "成片带渠道水印。", "reason": "水印由生成渠道输出。"},
        ],
        "changes": [
            {"changeId": "change-first-frame", "path": "SKILL.md", "section": "Step 5", "change": "增加独立首帧参考规则。", "expectedEffect": "提高首帧结构一致性。", "risks": ["占用一个参考图槽位"], "tests": ["验证首帧引用顺序"], "addresses": ["issue-first-frame"]},
        ],
        "exclusions": [{"issueId": "issue-watermark", "reason": "渠道限制不能通过技能保证消除。"}],
        "acceptanceTests": ["标准技能校验通过", "完整单元测试通过"],
        "confirmationPhrase": "确认优化 skill",
    }


class StoryboardDrivenWorkflowTest(unittest.TestCase):
    def test_skill_identity_and_ui_metadata(self):
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("name: recreate-video-agent\n", skill)
        self.assertIn("# recreate-video-agent v5.13", skill)
        self.assertIn('display_name: "recreate-video-agent v5.13"', metadata)
        self.assertIn("$recreate-video-agent ", metadata)
        self.assertNotIn("$recreate-product-video-v4", metadata)

    def test_compact_confirmation_contract(self):
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        confirmation = skill.split("确认单固定使用以下结构和措辞", 1)[1].split("确认单不得展示", 1)[0]
        for label in (
            "上传产品图（默认，请附至少一张）", "使用对标视频中的原产品",
            "替换为新达人", "沿用对标视频达人（默认）",
            "Seedance 2 Fast（默认）", "Seedance 2 Mini", "Seedance 2", "Seedance 2.5",
            "Minimax H3", "Grok Imagine 1.5 Preview", "与原视频一致（默认）",
            "仅开场10秒", "自定义时长", "自定义语言", "其他复刻要求",
        ):
            self.assertIn(label, confirmation)
        for removed in ("待选择", "候选产品图", "候选达人图", "产品卖点", "自定义复刻要求"):
            self.assertNotIn(removed, confirmation)

    def test_segment_plans_enforce_minimum_count_and_windows(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            path.write_text(json.dumps(segment_plan([(0, 15)])), encoding="utf-8")
            one = storyboard.parse_segment_plan(path)
            storyboard.validate_model_windows(one, "seedance-2-fast-vip")
            path.write_text(json.dumps(segment_plan([(0, 15), (15, 30)])), encoding="utf-8")
            two = storyboard.parse_segment_plan(path)
            storyboard.validate_model_windows(two, "seedance-2-fast-vip")
            with self.assertRaisesRegex(ValueError, "最少1"):
                storyboard.validate_model_windows(two, "seedance-2-5")
            path.write_text(json.dumps(segment_plan([(0, 30)])), encoding="utf-8")
            storyboard.validate_model_windows(storyboard.parse_segment_plan(path), "seedance-2-5")
            invalid = segment_plan([(0, 15), (16, 30)])
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "连续"):
                storyboard.parse_segment_plan(path)

    def test_segment_requires_exactly_16_anchors_and_valid_first_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            invalid = segment_plan([(0, 15)])
            invalid["segments"][0]["anchors"] = invalid["segments"][0]["anchors"][:15]
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "16"):
                storyboard.parse_segment_plan(path)
            invalid = segment_plan([(0, 15)])
            invalid["segments"][0]["anchors"][0]["timestamp"] = .1
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "第一格"):
                storyboard.parse_segment_plan(path)

    def test_storyboard_png_name_dimensions_and_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "benchmark.mp4"
            ffmpeg("-f", "lavfi", "-i", "testsrc2=size=180x320:rate=12", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video))
            plan = root / "plan.json"
            plan.write_text(json.dumps(segment_plan([(0, 4)])), encoding="utf-8")
            output = root / "boards"
            result = subprocess.run([
                sys.executable, str(SKILL_ROOT / "scripts" / "storyboard.py"), "--video", str(video),
                "--timestamps-file", str(plan), "--output-dir", str(output), "--ffmpeg", FFMPEG,
                "--model", "seedance-2-mini", "--cell-width", "120", "--cell-height", "240",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            board = output / "segment-01-storyboard-4x4.png"
            self.assertTrue(board.is_file())
            self.assertEqual(storyboard_cells.dimensions(None, board, FFMPEG), (480, 960))
            metadata = json.loads((output / "storyboard-metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["schemaRevision"], "5.1")
            self.assertEqual(len(metadata["boards"][0]["anchors"]), 16)

    def test_whole_board_quality_gate_and_retry_limit(self):
        passing = {"attempt": 1, "cells": [{"index": 3, "productPresent": True, "editedProductPresent": True, "personPresent": True, "editedPersonPresent": True, "personExtent": "partial", "editedPersonExtent": "partial", "identityMatch": True}]}
        self.assertTrue(storyboard_cells.validate_plan(passing)["passed"])
        failing = {"attempt": 1, "cells": [{"index": 3, "productPresent": False, "editedProductPresent": True, "personPresent": True, "editedPersonPresent": True, "personExtent": "partial", "editedPersonExtent": "full", "layoutChanged": True}]}
        result = storyboard_cells.validate_plan(failing)
        self.assertFalse(result["passed"])
        self.assertIn("product_presence_mismatch", result["failures"][0]["reasons"])
        self.assertIn("person_extent_changed", result["failures"][0]["reasons"])
        with self.assertRaisesRegex(ValueError, "最多"):
            storyboard_cells.validate_plan({"attempt": 2, "cells": []})

    def test_anchor_partial_metadata_is_preserved(self):
        item = storyboard.normalize_anchor({
            "timestamp": 0,
            "productPresent": True,
            "productVisibility": "partial",
            "productCount": 1,
            "personPresent": True,
            "personExtent": "partial",
            "personCount": 1,
            "creatorIds": ["creator-1"],
            "interactionState": "hand occludes bottle",
        }, 0)
        self.assertEqual(item["productVisibility"], "partial")
        self.assertEqual(item["personExtent"], "partial")
        self.assertEqual(item["productCount"], 1)
        self.assertEqual(item["personCount"], 1)

    def test_lock_merge_uses_original_frozen_and_edited_editable_cells(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, edited, output = root / "original.png", root / "edited.png", root / "final.png"
            ffmpeg("-f", "lavfi", "-i", "color=c=red:s=400x800", "-frames:v", "1", str(original))
            ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=420x780", "-frames:v", "1", str(edited))
            plan = root / "segment-01-replacement-map.json"
            plan.write_text(json.dumps(replacement_map({3, 7})), encoding="utf-8")
            result = storyboard_cells.lock_merge(FFMPEG, None, original, edited, plan, output)
            self.assertEqual(result["editableCells"], [3, 7])
            self.assertEqual(result["cellCount"], 16)
            self.assertEqual(storyboard_cells.dimensions(None, output, FFMPEG), (400, 800))

            original_cells = storyboard_cells.split_board(FFMPEG, None, original, root / "original-cells")
            final_cells = storyboard_cells.split_board(FFMPEG, None, output, root / "final-cells")
            original_hashes = {item["index"]: item["sha256"] for item in original_cells["cells"]}
            final_hashes = {item["index"]: item["sha256"] for item in final_cells["cells"]}
            for index in set(range(1, 17)) - {3, 7}:
                self.assertEqual(final_hashes[index], original_hashes[index], f"frozen cell {index}")
            self.assertNotEqual(final_hashes[3], original_hashes[3])
            self.assertNotEqual(final_hashes[7], original_hashes[7])

    def test_validate_plan_rejects_replacement_drift(self):
        cases = (
            ({"productCount": 1, "editedProductCount": 2}, "product_count_changed"),
            ({"personExtent": "partial", "editedPersonExtent": "full"}, "person_extent_changed"),
            ({"productPresent": True, "editedProductPresent": False}, "product_presence_mismatch"),
            ({"oldProductResidual": True}, "old_product_residual"),
            ({"identityMatch": False}, "identity_mismatch"),
            ({"oldCreatorResidual": True}, "old_creator_residual"),
        )
        for fields, reason in cases:
            with self.subTest(reason=reason):
                result = storyboard_cells.validate_plan({"attempt": 1, "cells": [{"index": 4, **fields}]})
                self.assertFalse(result["passed"])
                self.assertIn(reason, result["failures"][0]["reasons"])

    def test_restore_layout_keeps_original_dimensions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, edited, output = root / "original.png", root / "edited.png", root / "final.png"
            ffmpeg("-f", "lavfi", "-i", "color=c=red:s=400x800", "-frames:v", "1", str(original))
            ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=420x780", "-frames:v", "1", str(edited))
            storyboard_cells.restore_board_layout(FFMPEG, None, edited, original, output)
            self.assertEqual(storyboard_cells.dimensions(None, output, FFMPEG), (400, 800))

    def test_gemini_result_is_accepted_directly_without_schema_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = generation_manifest.command_init(argparse.Namespace(output_root=str(root), task_id="blueprint", reuse=False))
            source = manifest.parent / "analysis" / "gemini-result.json"
            source.write_text("```json\n" + json.dumps(blueprint(), ensure_ascii=False) + "\n```\n", encoding="utf-8")
            generation_manifest.set_blueprint(manifest, str(source))
            loaded = generation_manifest.load_manifest(manifest)
            self.assertTrue(Path(loaded["videoBlueprint"]["rawFile"]).is_file())
            self.assertTrue(loaded["videoBlueprint"]["acceptedDirectly"])
            unvalidated = {"videoBlueprint": {"逐镜头拆解": []}, "原产品信息": {}}
            source.write_text(json.dumps(unvalidated, ensure_ascii=False), encoding="utf-8")
            generation_manifest.set_blueprint(manifest, str(source))
            accepted = generation_manifest.load_manifest(manifest)
            self.assertEqual(Path(accepted["videoBlueprint"]["file"]).read_text(encoding="utf-8").strip(), json.dumps(unvalidated, ensure_ascii=False))

    def test_prompt_preflight_validates_storyboard_interface_and_minimum_segments(self):
        windows = {1: {"segmentId": 1, "globalStart": 0, "globalEnd": 15, "localStart": 0, "localEnd": 15}}
        segment = {"segmentId": 1, "title": "a", "duration": 15, "globalStart": 0, "globalEnd": 15, "storyboardIds": [1], "creatorIds": [], "prompt": valid_prompt(15)}
        self.assertTrue(prompt_preflight.validate_segments([segment], "seedance-2-fast-vip", target_duration=15, available_storyboards={1}, storyboard_windows=windows)["ok"])
        forbidden = dict(segment, renderUnitId="unit-1")
        with self.assertRaisesRegex(ValueError, "renderUnit"):
            prompt_preflight.validate_segments([forbidden], "seedance-2-fast-vip", target_duration=15, available_storyboards={1}, storyboard_windows=windows)
        two_boards = dict(segment, storyboardIds=[1, 2])
        with self.assertRaisesRegex(ValueError, "一张"):
            prompt_preflight.validate_segments([two_boards], "seedance-2-fast-vip", target_duration=15, available_storyboards={1, 2}, storyboard_windows=windows)

        product_segment = dict(segment, prompt=valid_prompt(15, product=True))
        self.assertTrue(prompt_preflight.validate_segments([product_segment], "seedance-2-fast-vip", target_duration=15, available_storyboards={1}, storyboard_windows=windows)["ok"])
        missing_product_lock = dict(product_segment, prompt=product_segment["prompt"].replace("产品身份与外观严格以产品参考图为准。\n", ""))
        with self.assertRaisesRegex(ValueError, "产品外观"):
            prompt_preflight.validate_segments([missing_product_lock], "seedance-2-fast-vip", target_duration=15, available_storyboards={1}, storyboard_windows=windows)

        legacy = dict(segment, prompt=segment["prompt"] + "\n禁止新增人物、产品、配件或其他实体。")
        with self.assertRaisesRegex(ValueError, "已废弃"):
            prompt_preflight.validate_segments([legacy], "seedance-2-fast-vip", target_duration=15, available_storyboards={1}, storyboard_windows=windows)

        missing_single_frame = dict(segment, prompt=segment["prompt"].replace("生成单画面连续视频", "生成连续视频"))
        with self.assertRaisesRegex(ValueError, "单画面"):
            prompt_preflight.validate_segments([missing_single_frame], "seedance-2-fast-vip", target_duration=15, available_storyboards={1}, storyboard_windows=windows)

        missing_subtitle_rule = dict(segment, prompt=segment["prompt"].replace("禁止字幕", "不需要字幕"))
        with self.assertRaisesRegex(ValueError, "禁止字幕"):
            prompt_preflight.validate_segments([missing_subtitle_rule], "seedance-2-fast-vip", target_duration=15, available_storyboards={1}, storyboard_windows=windows)

    def test_prompt_minimum_segments_fast_and_seedance_25(self):
        fast_segments, fast_windows = [], {}
        for identifier, start in ((1, 0), (2, 15)):
            fast_segments.append({"segmentId": identifier, "title": "x", "duration": 15, "globalStart": start, "globalEnd": start + 15, "storyboardIds": [identifier], "creatorIds": [], "prompt": valid_prompt(15)})
            fast_windows[identifier] = {"segmentId": identifier, "globalStart": start, "globalEnd": start + 15, "localStart": 0, "localEnd": 15}
        self.assertEqual(prompt_preflight.validate_segments(fast_segments, "seedance-2-fast-vip", target_duration=30, available_storyboards={1, 2}, storyboard_windows=fast_windows)["segmentCount"], 2)
        one = {"segmentId": 1, "title": "x", "duration": 30, "globalStart": 0, "globalEnd": 30, "storyboardIds": [1], "creatorIds": [], "prompt": valid_prompt(30)}
        window = {1: {"segmentId": 1, "globalStart": 0, "globalEnd": 30, "localStart": 0, "localEnd": 30}}
        self.assertEqual(prompt_preflight.validate_segments([one], "seedance-2-5", target_duration=30, available_storyboards={1}, storyboard_windows=window)["segmentCount"], 1)

    def test_schema_50_migrates_old_assets_out_of_active_interface(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "manifest.json"
            manifest.write_text(json.dumps({"version": "4", "schemaRevision": "5.0", "shotContracts": {"shotContracts": [{"shotId": "old"}]}, "renderUnits": [{"renderUnitId": "old"}], "userConfig": {}, "videos": []}), encoding="utf-8")
            loaded = generation_manifest.load_manifest(manifest)
            self.assertEqual(loaded["schemaRevision"], "5.4")
            self.assertNotIn("shotContracts", loaded)
            self.assertNotIn("renderUnits", loaded)
            self.assertIn("shotContracts", loaded["migrationArchive"])
            self.assertIn("renderUnits", loaded["migrationArchive"])

    def test_reference_audit_scopes_product_and_creator_by_segment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            board = root / "board.png"
            ffmpeg("-f", "lavfi", "-i", "color=c=red:s=400x800", "-frames:v", "1", str(board))
            data = {
                "product": {"productImages": [str(root / "product.png")]},
                "creators": [{"creatorId": "creator-1", "file": str(root / "creator.png")}],
                "storyboards": {"generation": [{"storyboardId": 1, "segmentId": 1, "file": str(board), "replacementVerified": True, "anchors": [{"productPresent": True, "creatorIds": ["creator-1"]} for _ in range(16)]}]},
                "videoPrompts": {"segments": [{"segmentId": 1, "storyboardIds": [1], "creatorIds": ["creator-1"]}]},
            }
            result = reference_audit.audit(data)
            self.assertTrue(result["segments"][0]["productReferenceRequired"])
            data["storyboards"]["generation"][0]["replacementVerified"] = False
            with self.assertRaisesRegex(ValueError, "replacementVerified"):
                reference_audit.audit(data)
            data["storyboards"]["generation"][0]["replacementVerified"] = True
            data["storyboards"]["generation"][0]["replacement"] = {"method": "legacy-compose", "replacementVerified": True}
            with self.assertRaisesRegex(ValueError, "whole-board-lock-merge"):
                reference_audit.audit(data)

    def test_manifest_computes_replacement_verified_from_all_conditions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            edited = root / "edited.png"
            edited.write_bytes(b"image-result")
            editable = [3]
            frozen = [index for index in range(1, 17) if index != 3]
            plan = root / "map.json"
            plan.write_text(json.dumps(replacement_map({3})), encoding="utf-8")
            lock = root / "lock.json"
            lock.write_text(json.dumps({"method": "whole-board-lock-merge", "mapValid": True, "lockMergeSucceeded": True, "frozenCellsRestored": True, "editableCells": editable, "frozenCells": frozen}), encoding="utf-8")
            audit = root / "audit.json"
            audit.write_text(json.dumps({"cells": [{"index": 3, "productIdentityMatch": True}]}), encoding="utf-8")
            validation = root / "validation.json"
            validation.write_text(json.dumps({"passed": True, "failures": []}), encoding="utf-8")
            valid = {
                "applied": True,
                "method": "whole-board-lock-merge",
                "generationAttempts": 1,
                "imageEditSucceeded": True,
                "mapValid": True,
                "lockMergeSucceeded": True,
                "frozenCellsRestored": True,
                "editableCellsAudited": True,
                "validationPassed": True,
                "editedImageFile": str(edited),
                "mapFile": str(plan),
                "lockMergeFile": str(lock),
                "auditFile": str(audit),
                "validationFile": str(validation),
                "editableCells": editable,
                "frozenCells": frozen,
                "replacementVerified": True,
            }
            self.assertTrue(generation_manifest.normalize_replacement_record(valid)["replacementVerified"])
            validation.write_text(json.dumps({"passed": False, "failures": [{"index": 3}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "replacementVerified"):
                generation_manifest.normalize_replacement_record(valid)

    def test_segment_storyboard_local_time_and_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cell = root / "cell.png"
            ffmpeg("-f", "lavfi", "-i", "color=c=red:s=120x240", "-frames:v", "1", str(cell))
            cells = [{"file": str(cell), "globalTimestamp": index * .25, "anchorRole": "hard" if index == 0 else "soft", "eventType": "first_frame" if index == 0 else "action_process", "productPresent": True, "productVisibility": "partial", "productCount": 1, "personPresent": True, "personExtent": "partial", "personCount": 1, "creatorIds": ["creator-1"], "interactionState": "held"} for index in range(16)]
            replacement = {"method": "whole-board-lock-merge", "replacementVerified": True}
            result = segment_storyboards.build({"segments": [{"segmentId": 1, "globalStart": 0, "globalEnd": 4, "replacementVerified": True, "replacement": replacement, "cells": cells}]}, root / "boards", FFMPEG, None)
            board = result["boards"][0]
            self.assertEqual(Path(board["file"]).name, "segment-01-storyboard-4x4.png")
            self.assertEqual(board["localStart"], 0)
            self.assertEqual(board["localEnd"], 4)
            self.assertEqual(board["anchors"][0]["productVisibility"], "partial")
            self.assertEqual(board["anchors"][0]["personExtent"], "partial")
            self.assertEqual(board["anchors"][0]["interactionState"], "held")
            self.assertEqual(board["replacement"]["method"], "whole-board-lock-merge")

    def test_product_identity_board_and_creator_empty_semantics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            images = []
            for index, color in enumerate(("red", "green", "blue"), 1):
                image = root / f"product-{index}.png"
                ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=128x128", "-frames:v", "1", str(image))
                images.append(image)
            output = reference_board.render(images, root / "identity.jpg", FFMPEG)
            self.assertTrue(output.is_file())
            data = {"product": {"images": ["/tmp/product.png"]}, "creators": [{"creatorId": "creator-1", "file": "/tmp/creator.png"}]}
            self.assertEqual(run_generation.product_files(data), ["/tmp/product.png"])
            self.assertEqual(run_generation.creator_files(data, {"creatorIds": []}), [])

    def test_quality_score_never_auto_regenerates(self):
        report = {"technical": {"failures": []}, "mayAutoRegenerate": False}
        assessment = {"scores": {"firstFrameHook": 10, "shotOrderCuts": 15, "productIdentityGeometryInteraction": 20, "keyStatesRewardDensity": 15, "motionContinuity": 8, "audioVoiceRhythm": 8}, "hardFailures": ["missing_proof"], "findings": ["末段缺Proof"]}
        scored = quality_review.finalize(report, assessment)
        self.assertFalse(scored["passed"])
        self.assertFalse(scored["mayAutoRegenerate"])
        self.assertTrue(scored["requiresUserApprovalForNewCandidate"])

    def test_failed_quality_requires_skill_proposal_but_unscored_and_passed_do_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = generation_manifest.command_init(argparse.Namespace(output_root=temporary, task_id="optimization-trigger", reuse=False))
            failed = Path(temporary) / "failed.json"
            failed.write_text(json.dumps({"status": "failed", "passed": False, "findings": [], "hardFailures": []}), encoding="utf-8")
            generation_manifest.set_quality_report(manifest, "candidate-01", str(failed))
            unscored = Path(temporary) / "unscored.json"
            unscored.write_text(json.dumps({"status": "needs_visual_review", "passed": False}), encoding="utf-8")
            generation_manifest.set_quality_report(manifest, "candidate-02", str(unscored))
            passed = Path(temporary) / "passed.json"
            passed.write_text(json.dumps({"status": "passed", "passed": True}), encoding="utf-8")
            generation_manifest.set_quality_report(manifest, "candidate-03", str(passed))
            loaded = generation_manifest.load_manifest(manifest)
            self.assertEqual(loaded["schemaRevision"], "5.4")
            self.assertEqual([(item["candidateId"], item["status"]) for item in loaded["skillOptimizations"]], [("candidate-01", "proposal_required")])

    def test_skill_proposal_requires_complete_classification_and_safe_paths(self):
        report = {"status": "failed", "passed": False, "findings": ["首帧偏移", "水印"], "hardFailures": ["first_frame_structure_mismatch", "watermark_present"]}
        self.assertEqual(generation_manifest.validate_skill_optimization_proposal(optimization_proposal(), report, "candidate-01")["status"], "proposed")
        for legacy_name in ("recreate-video-system", "recreate-product-video", "recreate-product-video-v4"):
            with self.subTest(target_skill=legacy_name):
                legacy = optimization_proposal()
                legacy["targetSkill"] = legacy_name
                self.assertEqual(generation_manifest.validate_skill_optimization_proposal(legacy, report, "candidate-01")["status"], "proposed")
        unknown_target = optimization_proposal()
        unknown_target["targetSkill"] = "unrelated-video-skill"
        with self.assertRaisesRegex(ValueError, "targetSkill"):
            generation_manifest.validate_skill_optimization_proposal(unknown_target, report, "candidate-01")
        no_change = optimization_proposal()
        no_change["issues"][0]["classification"] = "insufficient_evidence"
        no_change["changes"] = []
        no_change["exclusions"] = [
            {"issueId": "issue-first-frame", "reason": "单次失败不足以支持修改。"},
            {"issueId": "issue-watermark", "reason": "渠道限制不能通过技能保证消除。"},
        ]
        self.assertEqual(generation_manifest.validate_skill_optimization_proposal(no_change, report, "candidate-01")["status"], "no_change")
        incomplete = optimization_proposal()
        incomplete["issues"] = incomplete["issues"][:1]
        incomplete["exclusions"] = []
        with self.assertRaisesRegex(ValueError, "每条 finding"):
            generation_manifest.validate_skill_optimization_proposal(incomplete, report, "candidate-01")
        unsafe = optimization_proposal()
        unsafe["changes"][0]["path"] = "../other-skill/SKILL.md"
        with self.assertRaisesRegex(ValueError, "相对路径"):
            generation_manifest.validate_skill_optimization_proposal(unsafe, report, "candidate-01")
        wrong_target = optimization_proposal()
        wrong_target["changes"][0]["addresses"] = ["issue-watermark"]
        with self.assertRaisesRegex(ValueError, "skill_actionable"):
            generation_manifest.validate_skill_optimization_proposal(wrong_target, report, "candidate-01")

    def test_skill_proposal_fingerprint_change_marks_confirmation_stale(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill_root = root / "skill"
            skill_root.mkdir()
            skill_file = skill_root / "SKILL.md"
            skill_file.write_text("# recreate-video-agent v5.8\n", encoding="utf-8")
            manifest = generation_manifest.command_init(argparse.Namespace(output_root=str(root), task_id="stale", reuse=False))
            report = root / "quality.json"
            report.write_text(json.dumps({"status": "failed", "passed": False, "findings": ["首帧偏移", "水印"], "hardFailures": ["first_frame_structure_mismatch", "watermark_present"]}), encoding="utf-8")
            proposal = root / "proposal.json"
            proposal.write_text(json.dumps(optimization_proposal(), ensure_ascii=False), encoding="utf-8")
            with patch.object(generation_manifest, "SKILL_ROOT", skill_root):
                generation_manifest.set_quality_report(manifest, "candidate-01", str(report))
                generation_manifest.set_skill_optimization_proposal(manifest, "candidate-01", str(proposal))
                skill_file.write_text("# recreate-video-agent v5.8\nchanged\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "确认失效"):
                    skill_optimization.snapshot(manifest, "candidate-01", proposal)
            loaded = generation_manifest.load_manifest(manifest)
            self.assertEqual(loaded["skillOptimizations"][0]["status"], "stale")

    def test_skill_optimization_snapshot_rolls_back_failed_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill_root = root / "skill"
            skill_root.mkdir()
            skill_file = skill_root / "SKILL.md"
            original = "# recreate-video-agent v5.8\n"
            skill_file.write_text(original, encoding="utf-8")
            manifest = generation_manifest.command_init(argparse.Namespace(output_root=str(root), task_id="rollback", reuse=False))
            report = root / "quality.json"
            report.write_text(json.dumps({"status": "failed", "passed": False, "findings": ["首帧偏移", "水印"], "hardFailures": ["first_frame_structure_mismatch", "watermark_present"]}), encoding="utf-8")
            proposal = root / "proposal.json"
            proposal.write_text(json.dumps(optimization_proposal(), ensure_ascii=False), encoding="utf-8")
            validation = root / "validation.json"
            validation.write_text(json.dumps({"passed": False, "quickValidate": "failed", "unitTests": "not-run"}), encoding="utf-8")
            with patch.object(generation_manifest, "SKILL_ROOT", skill_root):
                generation_manifest.set_quality_report(manifest, "candidate-01", str(report))
                generation_manifest.set_skill_optimization_proposal(manifest, "candidate-01", str(proposal))
                skill_optimization.snapshot(manifest, "candidate-01", proposal)
                skill_file.write_text("# recreate-video-agent v5.9\nbroken\n", encoding="utf-8")
                skill_optimization.rollback(manifest, "candidate-01", proposal)
                generation_manifest.set_skill_optimization_result(manifest, "candidate-01", str(proposal), "rolled_back", str(validation))
            self.assertEqual(skill_file.read_text(encoding="utf-8"), original)
            loaded = generation_manifest.load_manifest(manifest)
            self.assertEqual(loaded["skillOptimizations"][0]["status"], "rolled_back")

    def test_representative_no_paid_end_to_end(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            board = root / "final-board.png"
            creator = root / "creator.png"
            ffmpeg("-f", "lavfi", "-i", "color=c=red:s=400x800", "-frames:v", "1", str(board))
            ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=128x128", "-frames:v", "1", str(creator))
            manifest = generation_manifest.command_init(argparse.Namespace(output_root=str(root), task_id="flow", reuse=False))
            data = generation_manifest.load_manifest(manifest)
            data["skillVersion"] = "5.10"
            data["product"] = {"useBenchmarkProduct": True, "productImages": []}
            data["userConfig"].update({"videoModel": "seedance-2-mini", "duration": 4})
            replacement = {
                "applied": True, "method": "whole-board-lock-merge", "generationAttempts": 1,
                "imageEditSucceeded": True, "mapValid": True, "lockMergeSucceeded": True,
                "frozenCellsRestored": True, "editableCellsAudited": True, "validationPassed": True,
                "replacementVerified": True,
            }
            data["storyboards"]["generation"] = [{"storyboardId": 1, "segmentId": 1, "file": str(board), "globalStart": 0, "globalEnd": 4, "localStart": 0, "localEnd": 4, "replacementVerified": True, "replacement": replacement, "anchors": [{"productPresent": False, "creatorIds": ["creator-1"]} for _ in range(16)]}]
            data["creators"] = [{"creatorId": "creator-1", "file": str(creator)}]
            prompt_file = manifest.parent / "prompts" / "video-prompts.json"
            prompt_data = {"videoPrompts": {"summary": "test", "adaptationPlan": {}, "qualitySpec": {"hardAnchors": [], "requiredCuts": [], "proof": [], "cta": []}, "segments": [{"segmentId": 1, "title": "test", "duration": 4, "globalStart": 0, "globalEnd": 4, "storyboardIds": [1], "creatorIds": ["creator-1"], "prompt": valid_prompt(4, creator=True)}]}}
            prompt_file.write_text(json.dumps(prompt_data, ensure_ascii=False), encoding="utf-8")
            data["videoPrompts"] = {"file": str(prompt_file), "segments": prompt_data["videoPrompts"]["segments"]}
            generation_manifest.save_manifest(manifest, data)
            generated = root / "generated.mp4"
            ffmpeg("-f", "lavfi", "-i", "testsrc2=size=360x640:rate=12", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(generated))

            def fake_generate(*_args, on_submit=None, **_kwargs):
                if on_submit:
                    on_submit("task-1")
                return {"video": {"url": "https://example.test/video.mp4", "mimeType": "video/mp4"}, "credits": 0}

            def fake_download(_media, output):
                destination = Path(output)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(generated, destination)
                return destination

            blocked_argv = ["run_generation.py", "--manifest", str(manifest), "--video-provider", "official_cli", "--skip-concat"]
            with patch.object(sys, "argv", blocked_argv):
                with self.assertRaisesRegex(SystemExit, "确认生成"):
                    run_generation.main()

            argv = ["run_generation.py", "--manifest", str(manifest), "--video-provider", "official_cli", "--skip-concat", "--generation-approved"]
            with patch.object(sys, "argv", argv), patch.object(run_generation.run_cli, "detect_video_providers", return_value={"official_cli": True}), patch.object(run_generation.run_cli, "video_provider_availability", return_value={"official_cli": True}), patch.object(run_generation.run_cli, "resolve_video_provider", return_value="official_cli"), patch.object(run_generation.run_cli, "generate_video", side_effect=fake_generate), patch.object(run_generation.run_cli, "download_media", side_effect=fake_download):
                self.assertEqual(run_generation.main(), 0)
            loaded = generation_manifest.load_manifest(manifest)
            self.assertEqual(loaded["schemaRevision"], "5.4")
            self.assertEqual(loaded["videos"][0]["status"], "success")
            self.assertEqual(loaded["referenceAudit"]["segments"][0]["orderedFiles"], [str(board), str(creator)])

    def test_scripts_exclude_removed_runtime_interface(self):
        generation_source = (SKILL_ROOT / "scripts" / "run_generation.py").read_text(encoding="utf-8")
        manifest_source = (SKILL_ROOT / "scripts" / "generation_manifest.py").read_text(encoding="utf-8")
        self.assertNotIn("renderUnitId", generation_source)
        self.assertNotIn("set-shot-contracts", manifest_source)
        self.assertNotIn("set-render-units", manifest_source)
        self.assertNotIn("recreate-video-prompt", generation_source)

    def test_server_core_prompts_are_not_shipped_in_client_skill(self):
        self.assertFalse((SKILL_ROOT / "references" / "gemini_video_analysis_prompt.txt").exists())
        self.assertFalse((SKILL_ROOT / "references" / "video_prompt_generation.md").exists())
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("userConfig.visualContext.segments", skill)
        self.assertIn("scripts/server_prompt_workflow.py", skill)

    def test_server_payload_keeps_cli_second_level_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            benchmark = root / "benchmark.mp4"
            board = root / "segment.png"
            product = root / "product.png"
            ffmpeg("-f", "lavfi", "-i", "testsrc2=size=360x640:rate=12", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(benchmark))
            ffmpeg("-f", "lavfi", "-i", "color=c=red:s=400x800", "-frames:v", "1", str(board))
            ffmpeg("-f", "lavfi", "-i", "color=c=yellow:s=400x800", "-frames:v", "1", str(product))
            manifest = generation_manifest.command_init(argparse.Namespace(output_root=str(root), task_id="server", reuse=False))
            data = generation_manifest.load_manifest(manifest)
            data["userConfig"].update({"videoModel": "seedance-2-mini", "duration": 4})
            data["product"] = {"useBenchmarkProduct": False, "productImages": [str(product)], "productAnalysis": {"productName": "测试产品"}}
            data["storyboards"]["generation"] = [{
                "storyboardId": 1, "segmentId": 1, "file": str(board),
                "globalStart": 0, "globalEnd": 4, "localStart": 0, "localEnd": 4,
                "replacementVerified": True,
                "anchors": [{"productPresent": True, "creatorIds": []} for _ in range(16)],
            }]
            generation_manifest.save_manifest(manifest, data)
            payload = server_prompt_workflow.build_payload(manifest, benchmark, dry_run=True)
            self.assertEqual(tuple(payload["input"]), server_prompt_workflow.INPUT_FIELDS)
            visual = payload["input"]["userConfig"]["visualContext"]["segments"]
            self.assertEqual(len(visual), 1)
            self.assertEqual(visual[0]["storyboardId"], 1)
            self.assertEqual(len(payload["input"]["productBrief"]["productImageUrls"]), 1)
            arguments = server_prompt_workflow.cli_submit_arguments(payload)
            self.assertEqual(arguments[:2], ["recreate-video-prompt", "submit"])
            for option in ("--benchmark-video-url", "--user-config", "--product-brief", "--creator-brief"):
                self.assertIn(option, arguments)

    def test_server_execution_saves_and_preflights_prompts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            board = root / "segment.png"
            benchmark = root / "benchmark.mp4"
            ffmpeg("-f", "lavfi", "-i", "color=c=red:s=400x800", "-frames:v", "1", str(board))
            ffmpeg("-f", "lavfi", "-i", "testsrc2=size=360x640:rate=12", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(benchmark))
            manifest = generation_manifest.command_init(argparse.Namespace(output_root=str(root), task_id="execute", reuse=False))
            data = generation_manifest.load_manifest(manifest)
            data["userConfig"].update({"videoModel": "seedance-2-mini", "duration": 4})
            data["product"] = {"useBenchmarkProduct": True, "productImages": []}
            data["storyboards"]["generation"] = [{
                "storyboardId": 1, "segmentId": 1, "file": str(board),
                "globalStart": 0, "globalEnd": 4, "localStart": 0, "localEnd": 4,
                "replacementVerified": False,
                "anchors": [{"productPresent": False, "creatorIds": []} for _ in range(16)],
            }]
            generation_manifest.save_manifest(manifest, data)
            prompts = {
                "summary": "test", "adaptationPlan": {},
                "qualitySpec": {"hardAnchors": [], "requiredCuts": [], "proof": [], "cta": []},
                "segments": [{
                    "segmentId": 1, "title": "test", "duration": 4,
                    "globalStart": 0, "globalEnd": 4, "storyboardIds": [1],
                    "creatorIds": [], "prompt": valid_prompt(4),
                }],
            }
            payload = {"input": {key: {} for key in server_prompt_workflow.INPUT_FIELDS}}
            payload["input"]["benchmarkVideoUrl"] = "https://example.test/benchmark.mp4"
            response = {"status": "Succeeded", "output": {"success": True, "output": {"videoPrompts": prompts}, "credits": 5}}
            with patch.object(server_prompt_workflow, "build_payload", return_value=payload), patch.object(run_cli, "run_cli", return_value={"id": "prompt-task-1"}), patch.object(run_cli, "poll_task", return_value=response):
                result = server_prompt_workflow.execute(manifest, benchmark)
            self.assertTrue(result["success"])
            self.assertEqual(result["taskId"], "prompt-task-1")
            loaded = generation_manifest.load_manifest(manifest)
            self.assertEqual(loaded["workflowStatus"], "step4_complete")
            self.assertEqual(loaded["serverPromptTask"]["status"], "succeeded")

    def test_run_generation_can_select_single_segment_without_changing_full_plan(self):
        segments = [{"segmentId": 1}, {"segmentId": 2}, {"segmentId": 3}]
        self.assertEqual(run_generation.select_segments(segments, [1]), [{"segmentId": 1}])
        self.assertEqual(run_generation.select_segments(segments, []), segments)
        with self.assertRaisesRegex(ValueError, "不存在"):
            run_generation.select_segments(segments, [4])

    def test_partial_quality_report_allows_missing_final_video(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = generation_manifest.command_init(argparse.Namespace(output_root=temporary, task_id="partial-quality", reuse=False))
            report = Path(temporary) / "quality.json"
            report.write_text(json.dumps({"status": "failed", "passed": False}), encoding="utf-8")
            generation_manifest.set_quality_report(manifest, "candidate-01", str(report))
            loaded = generation_manifest.load_manifest(manifest)
            self.assertEqual(loaded["workflowStatus"], "step5_review_pending")
            self.assertEqual(loaded["qualityReports"][0]["candidateId"], "candidate-01")

    def test_model_capabilities(self):
        self.assertEqual(run_cli._video_model_id("Seedance2fast vip"), "seedance-2-fast-vip")
        self.assertEqual(run_cli._video_model_id("Minimax H3"), "minimax-h3")
        self.assertEqual(run_cli._video_model_id("Grok Imagine 1.5 Preview"), "grok-imagine-1-5-preview")
        self.assertEqual(validate_generation("seedance-2-5", 30)["maxDuration"], 30)
        self.assertEqual(validate_generation("minimax-h3", 15)["minDuration"], 4)
        self.assertEqual(validate_generation("grok-imagine-1-5-preview", 1)["minDuration"], 1)
        self.assertEqual(minimum_segment_count("seedance-2-fast-vip", 30), 2)
        self.assertEqual(minimum_segment_count("minimax-h3", 30), 2)
        self.assertEqual(minimum_segment_count("grok-imagine-1-5-preview", 16), 2)
        self.assertEqual(plan_segment_windows("seedance-2-fast-vip", 30, [14.4])[0]["globalEnd"], 15)
        self.assertEqual(plan_segment_windows("seedance-2-fast-vip", 29, [14.2])[0]["globalEnd"], 14)
        self.assertEqual(len(plan_segment_windows("seedance-2-5", 30, [14.2])), 1)
        with self.assertRaisesRegex(ValueError, "整数秒"):
            plan_segment_windows("seedance-2-fast-vip", 14.5)
        with self.assertRaisesRegex(ValueError, "4-15"):
            validate_generation("seedance-2-fast-vip", 16)
        with self.assertRaisesRegex(ValueError, "4-15"):
            validate_generation("minimax-h3", 1)
        with self.assertRaisesRegex(ValueError, "1-15"):
            validate_generation("grok-imagine-1-5-preview", 16)

    def test_provider_model_mapping_and_xiaoyunque_skeleton(self):
        self.assertEqual(run_cli._official_video_model_id("seedance-2-fast"), "seedance2.0fast_vip")
        self.assertEqual(run_cli._official_video_model_id("seedance-2-mini"), "seedance2.0mini_vip")
        self.assertEqual(run_cli._official_video_model_id("seedance-2"), "seedance2.0_vip")
        self.assertEqual(run_cli._xiaoyunque_video_model_id("seedance-2-fast"), "seedance2.0fast_vip")
        with self.assertRaisesRegex(run_cli.LzStudioError, "适配器尚未配置"):
            run_cli.resolve_video_provider("xiaoyunque_cli", availability={"xiaoyunque_cli": True})
        self.assertEqual(
            run_cli.resolve_video_provider("auto", availability={"official_cli": False, "xiaoyunque_cli": True, "lingzhi_cli": True}),
            "lingzhi_cli",
        )
        help_result = subprocess.CompletedProcess(["dreamina"], 0, stdout="seedance2.0fast_vip seedance2.0_vip", stderr="")
        with patch.object(run_cli, "resolve_official_cli", return_value=Path("/fake/dreamina")), patch.object(run_cli.subprocess, "run", return_value=help_result):
            self.assertTrue(run_cli.official_cli_supports_video_model("seedance-2-fast"))
            self.assertFalse(run_cli.official_cli_supports_video_model("seedance-2-mini"))

    def test_manifest_records_compact_user_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = generation_manifest.command_init(argparse.Namespace(
                output_root=temporary, task_id="config", reuse=False,
                video_model="grok-imagine-1-5-preview", duration_mode="custom",
                target_duration=8, custom_requirement="去掉结尾口播",
            ))
            config = generation_manifest.load_manifest(manifest)["userConfig"]
            self.assertEqual(config["videoModel"], "grok-imagine-1-5-preview")
            self.assertEqual(config["durationMode"], "custom")
            self.assertEqual(config["requestedDuration"], 8)
            self.assertEqual(config["customRequirement"], "去掉结尾口播")
            self.assertEqual(config["resolution"], "720p")
            self.assertEqual(config["qualityProfile"], "strict")

    def test_non_integer_source_duration_is_normalized_without_confirmation_gate(self):
        self.assertEqual(benchmark_analysis.normalized_source_duration(53.23), 53)
        self.assertEqual(benchmark_analysis.normalized_source_duration(53.51), 54)
        with self.assertRaisesRegex(ValueError, "短于模型最短时长"):
            benchmark_analysis.normalized_source_duration(3.99)

        with tempfile.TemporaryDirectory() as temporary:
            manifest = generation_manifest.command_init(argparse.Namespace(output_root=temporary, task_id="duration", reuse=False))
            analysis_file = Path(temporary) / "analysis.json"
            analysis_file.write_text(json.dumps({"media": {"duration": 53.23}, "targetDuration": 53}), encoding="utf-8")
            generation_manifest.set_benchmark_analysis(manifest, str(analysis_file))
            self.assertEqual(generation_manifest.load_manifest(manifest)["userConfig"]["duration"], 53)

    def test_replication_duration_modes_and_model_minimums(self):
        source = benchmark_analysis.resolve_replication_duration(53.23, "seedance-2-fast")
        self.assertEqual((source["durationMode"], source["targetDuration"]), ("source", 53))
        opening = benchmark_analysis.resolve_replication_duration(53.23, "seedance-2-fast", "opening_10")
        self.assertEqual(opening["replicationWindow"], {"start": 0, "end": 10})
        short = benchmark_analysis.resolve_replication_duration(8.6, "seedance-2-fast", "opening_10")
        self.assertEqual(short["targetDuration"], 9)
        custom = benchmark_analysis.resolve_replication_duration(20, "grok-imagine-1-5-preview", "custom", 1)
        self.assertEqual(custom["targetDuration"], 1)
        with self.assertRaisesRegex(ValueError, "超过原视频"):
            benchmark_analysis.resolve_replication_duration(8.5, "seedance-2-fast", "custom", 9)
        with self.assertRaisesRegex(ValueError, "最短时长4秒"):
            benchmark_analysis.resolve_replication_duration(3.99, "minimax-h3")

    def test_partial_replication_uses_target_duration_for_storyboard_and_quality(self):
        plan = segment_plan([(0, 10)])
        storyboard.validate_against_analysis(plan["segments"], {"media": {"duration": 30}, "targetDuration": 10})
        original = {"media": {"duration": 30, "width": 720, "height": 1280, "hasAudio": True}}
        generated = {"media": {"duration": 10, "width": 720, "height": 1280, "hasAudio": True}}
        with tempfile.TemporaryDirectory() as temporary, patch.object(quality_review, "analyze_video", side_effect=[original, generated]), patch.object(quality_review, "comparison_sheet"):
            report = quality_review.build_review("ffmpeg", None, Path("benchmark.mp4"), Path("candidate.mp4"), Path(temporary), target_duration=10)
        self.assertEqual(report["targetDuration"], 10)
        self.assertNotIn("duration_out_of_tolerance", report["technical"]["failures"])

    def test_single_uploaded_creator_only_replaces_primary(self):
        mapping = creator_mapping.normalize_creator_replacement_map([
            {"sourceCreatorId": "source-creator-1", "role": "primary", "action": "replace", "targetCreatorId": "creator-1"},
            {"sourceCreatorId": "source-creator-2", "role": "supporting", "action": "keep"},
        ], available_target_ids={"creator-1"})
        self.assertEqual(mapping[0]["targetCreatorId"], "creator-1")
        self.assertEqual(mapping[1]["action"], "keep")
        with self.assertRaisesRegex(ValueError, "不得映射多个"):
            creator_mapping.normalize_creator_replacement_map([
                {"sourceCreatorId": "source-creator-1", "role": "primary", "action": "replace", "targetCreatorId": "creator-1"},
                {"sourceCreatorId": "source-creator-2", "role": "supporting", "action": "replace", "targetCreatorId": "creator-1"},
            ], available_target_ids={"creator-1"})

    def test_replacement_map_preserves_unmapped_people_in_same_cell(self):
        mapping = [
            {"sourceCreatorId": "source-creator-1", "role": "primary", "action": "replace", "targetCreatorId": "creator-1"},
            {"sourceCreatorId": "source-creator-2", "role": "supporting", "action": "keep"},
        ]
        value = {
            "segmentId": 1,
            "replaceProduct": False,
            "replaceCreator": True,
            "creatorReplacementMap": mapping,
            "cells": [{
                "index": index,
                "product": {"state": "none", "count": 0, "replace": False},
                "creator": {"state": "full", "count": 2, "replace": True, "sourceCreatorIds": ["source-creator-1", "source-creator-2"]},
                "interactionState": "two people",
            } for index in range(1, 17)],
        }
        normalized = storyboard_cells.normalize_replacement_map(value)
        first = normalized["cells"][0]["creator"]
        self.assertEqual(first["targetCreatorIds"], ["creator-1"])
        self.assertEqual(first["keptSourceCreatorIds"], ["source-creator-2"])

    def test_manifest_creator_mapping_covers_all_source_people(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "image.png"
            image.write_bytes(b"image")
            manifest = generation_manifest.command_init(argparse.Namespace(output_root=temporary, task_id="creator-map", reuse=False))
            source_anchors = [{"sourceCreatorIds": ["source-creator-1", "source-creator-2"]} for _ in range(16)]
            generation_manifest.add_storyboard(manifest, "original", str(image), 1, anchors=source_anchors)
            generation_manifest.add_creator(manifest, str(image), "creator-1")
            mapping_file = root / "mapping.json"
            mapping_file.write_text(json.dumps({"creatorReplacementMap": [
                {"sourceCreatorId": "source-creator-1", "role": "primary", "action": "replace", "targetCreatorId": "creator-1"},
                {"sourceCreatorId": "source-creator-2", "role": "supporting", "action": "keep"},
            ]}), encoding="utf-8")
            generation_manifest.set_creator_replacement_map(manifest, str(mapping_file))
            self.assertEqual(len(generation_manifest.load_manifest(manifest)["creatorReplacementMap"]), 2)

    def test_lingzhi_image_terminal_failure_allows_one_agent_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prompt = root / "prompt.txt"
            reference = root / "board.png"
            prompt.write_text("edit board", encoding="utf-8")
            reference.write_bytes(b"image")
            args = argparse.Namespace(
                prompt_file=str(prompt), reference_image=[str(reference)], output=str(root / "edited.png"),
                report=str(root / "report.json"), aspect_ratio="9:16", resolution="1K", resume_task_id=None,
            )
            with patch.object(run_cli, "upload_file", return_value={"url": "https://example.test/board.png"}), patch.object(run_cli, "submit_image", return_value="task-1"), patch.object(run_cli, "poll_image", side_effect=run_cli.TaskFailedError("task-1", "failed", "failed")):
                code, report = image_edit_workflow.execute(args)
            self.assertEqual(code, 2)
            self.assertTrue(report["fallbackAllowed"])
            self.assertEqual(report["status"], "terminal_failed")
            with patch.object(run_cli, "upload_file", return_value={"url": "https://example.test/board.png"}), patch.object(run_cli, "submit_image", return_value="task-2"), patch.object(run_cli, "poll_image", side_effect=run_cli.LzStudioError("timeout")):
                code, report = image_edit_workflow.execute(args)
            self.assertEqual(code, 3)
            self.assertFalse(report["fallbackAllowed"])


if __name__ == "__main__":
    unittest.main()
