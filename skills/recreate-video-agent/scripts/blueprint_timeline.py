"""Adapt the stable server replicationPlan contract to local storyboard metadata.

Planning stays on the server. This module deliberately contains no Segment scoring,
cut selection, utterance penalties, model-duration policy, or keyframe ranking logic.
"""
from __future__ import annotations

import argparse
import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    from scripts import generation_manifest
except ModuleNotFoundError:
    import generation_manifest


SUPPORTED_PLAN_SCHEMA_MAJOR = "1"
SUPPORTED_PLANNER_VERSIONS = {"1", "2"}


def _layout_for_anchor_count(count: int) -> str:
    # Layout is a client rendering detail, not part of the server planning protocol.
    # Planner V1 currently returns 9 anchors. 4x4 remains readable for migration/tests.
    if count == 9:
        return "3x3"
    if count == 16:
        return "4x4"
    raise ValueError(f"当前客户端不支持{count}个anchors；需要升级Storyboard渲染器。")


def normalize_server_plan(plan: dict[str, Any]) -> dict[str, Any]:
    schema = str(plan.get("schemaVersion", "")).strip()
    if not schema or schema.split(".", 1)[0] != SUPPORTED_PLAN_SCHEMA_MAJOR:
        raise ValueError(f"replicationPlan协议版本不兼容：{schema or '缺失'}。")
    planner = str(plan.get("plannerVersion", "")).strip()
    if planner not in SUPPORTED_PLANNER_VERSIONS:
        raise ValueError(f"当前Skill不支持plannerVersion={planner or '缺失'}。")
    segments = plan.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("服务端replicationPlan缺少有效segments。")

    local = deepcopy(plan)
    normalized: list[dict[str, Any]] = []
    cursor = 0.0
    for expected_id, segment in enumerate(segments, 1):
        if not isinstance(segment, dict):
            raise ValueError(f"Segment {expected_id} 不是对象。")
        identifier = int(segment.get("segmentId", expected_id))
        if identifier != expected_id:
            raise ValueError("Segment ID必须从1开始连续递增。")
        start = float(segment["globalStart"])
        end = float(segment["globalEnd"])
        if abs(start - cursor) > .01 or end <= start:
            raise ValueError("Segment时间窗必须从0开始连续、无重叠、无空档。")
        anchors = segment.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            raise ValueError(f"Segment {identifier} 缺少anchors。")
        if planner == "2":
            duration = segment.get("duration")
            if not all(math.isfinite(v) for v in (start, end)) or isinstance(duration, bool) or duration != end - start:
                raise ValueError("V2 Segment时间或duration无效")
            if len(anchors) != 9:
                raise ValueError("V2保持9个anchors")
            shot_ids = segment.get("shotIds", [])
            for field in ("shotIds", "sourceCharacterIds", "sourceProductIds"):
                values = segment.get(field)
                if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values) or len(set(values)) != len(values):
                    raise ValueError(f"V2缺少有效{field}")
            previous = -1.0
            for anchor in anchors:
                t = anchor.get("timestamp")
                if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or not start <= t < end or t <= previous:
                    raise ValueError("V2 anchor时间越界或无序")
                if anchor.get("shotId") not in shot_ids:
                    raise ValueError("V2 anchor必须引用真实shotId")
                if t >= float(plan["sourceDuration"]):
                    raise ValueError("V2 anchor越出原片")
                previous = t
        item = deepcopy(segment)
        item["layout"] = _layout_for_anchor_count(len(anchors))
        item["anchorCount"] = len(anchors)
        normalized.append(item)
        cursor = end
    local["segments"] = normalized
    return local


def load_server_plan(manifest: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    record = manifest.get("replicationPlan")
    timeline = manifest.get("timelinePlan")
    path_value = ""
    if isinstance(record, dict):
        path_value = str(record.get("file", "") or "")
    if not path_value and isinstance(timeline, dict):
        path_value = str(timeline.get("file", "") or "")
    if not path_value:
        raise ValueError("manifest缺少服务端replicationPlan；请先完成RecreateVideoPromptV3。")
    path = Path(path_value).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"服务端replicationPlan文件不存在或为空：{path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("服务端replicationPlan不是JSON对象。")
    return normalize_server_plan(raw), path


def persist_plan(manifest_path: str | Path, output: str | Path) -> dict[str, Any]:
    data = generation_manifest.load_manifest(manifest_path)
    plan, source = load_server_plan(data)
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    recommended = [
        {key: segment[key] for key in ("segmentId", "globalStart", "globalEnd", "duration")}
        for segment in plan["segments"]
    ]

    def mutate(current: dict[str, Any]) -> None:
        current.setdefault("benchmarkVideo", {}).setdefault("analysis", {})["recommendedSegments"] = recommended
        current["timelinePlan"] = {
            "file": str(target),
            "schemaVersion": str(plan.get("schemaVersion", "1.0")),
            "plannerVersion": str(plan.get("plannerVersion", "1")),
            "policyVersion": str(plan.get("policyVersion", "")),
            "source": "server-adapter",
            "serverFile": str(source),
        }

    generation_manifest.update(manifest_path, mutate)
    return {
        "ok": True,
        "source": "server",
        "schemaVersion": str(plan.get("schemaVersion", "")),
        "plannerVersion": str(plan.get("plannerVersion", "")),
        "policyVersion": str(plan.get("policyVersion", "")),
        "segments": len(plan["segments"]),
        "layouts": [segment.get("layout") for segment in plan["segments"]],
        "output": str(target),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(persist_plan(args.manifest, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
