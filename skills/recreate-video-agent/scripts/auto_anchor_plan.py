#!/usr/bin/env python3
"""Build a deterministic 9-anchor plan from single-pass visual scan metrics."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

PANELS = 9
HOOK_POINTS = (0.5, 1.5, 2.8)


def _finite(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"非有限时间值：{value!r}")
    return number


def _near(values: list[float], target: float, tolerance: float) -> bool:
    return any(abs(value - target) < tolerance for value in values)


def _scan_samples(analysis: dict[str, Any]) -> list[dict[str, float]]:
    raw = analysis.get("visualScan", {}).get("samples", [])
    result: list[dict[str, float]] = []
    if not isinstance(raw, list):
        return result
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            result.append({
                "timestamp": _finite(item.get("timestamp")),
                "qualityScore": _finite(item.get("qualityScore", 0)),
                "changeScore": _finite(item.get("changeScore", 0)),
            })
        except (TypeError, ValueError):
            continue
    return result


def _best_near_target(
    target: float,
    samples: list[dict[str, float]],
    *,
    start: float,
    end: float,
    radius: float,
    selected: list[float],
    min_gap: float,
) -> float:
    """Borrow the reference skill's clear-frame idea without extra FFmpeg seeks."""
    nearby = [
        sample
        for sample in samples
        if start < sample["timestamp"] < end
        and abs(sample["timestamp"] - target) <= radius
        and not _near(selected, sample["timestamp"], min_gap)
    ]
    if not nearby:
        return round(target, 3)
    # Match the reference skill's intent: once a sample is inside the small
    # neighborhood, prefer the clearer frame rather than the mathematically
    # closest timestamp. No additional seek is needed because all samples came
    # from the same single-pass scan.
    best = max(
        nearby,
        key=lambda sample: (
            sample["qualityScore"] + sample["changeScore"] * 0.08,
            -abs(sample["timestamp"] - target),
        ),
    )
    return round(best["timestamp"], 3)


def _visual_select(
    candidates: list[dict[str, Any]],
    selected: list[float],
    count: int,
    *,
    start: float,
    end_anchor: float,
    min_gap: float,
) -> list[tuple[float, str]]:
    """Greedily balance action strength, real cuts, quality and timeline coverage."""
    remaining: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        try:
            timestamp = round(_finite(item.get("timestamp")), 3)
            change = max(0.0, min(1.0, _finite(item.get("changeScore", 0))))
            quality = max(0.0, min(1.0, _finite(item.get("qualityScore", 0))))
        except (TypeError, ValueError):
            continue
        if not (start + min_gap < timestamp < end_anchor - min_gap):
            continue
        remaining.append({
            "timestamp": timestamp,
            "changeScore": change,
            "qualityScore": quality,
            "eventType": "cut" if str(item.get("eventType")) == "cut" else "visual_change",
        })

    chosen: list[tuple[float, str]] = []
    span = max(0.001, end_anchor - start)
    while remaining and len(chosen) < count:
        available = [
            item for item in remaining
            if not _near(selected + [value for value, _ in chosen], item["timestamp"], min_gap)
        ]
        if not available:
            break

        def score(item: dict[str, Any]) -> tuple[float, float, float]:
            anchors = selected + [value for value, _ in chosen]
            coverage = min(abs(item["timestamp"] - point) for point in anchors) / span
            cut_bonus = 0.14 if item["eventType"] == "cut" else 0.0
            total = item["changeScore"] * 0.58 + coverage * 0.24 + item["qualityScore"] * 0.18 + cut_bonus
            return total, item["changeScore"], -item["timestamp"]

        winner = max(available, key=score)
        chosen.append((winner["timestamp"], winner["eventType"]))
        remaining.remove(winner)
    return chosen


def _largest_gap_fill(
    selected: list[float],
    start: float,
    end_anchor: float,
    count: int,
    min_gap: float,
    samples: list[dict[str, float]],
) -> list[float]:
    """Fill uncovered intervals and prefer a clearer already-scanned frame near each midpoint."""
    values = sorted(set(selected))
    scan_radius = max(0.3, min(0.55, (end_anchor - start) / 20.0))
    while len(values) < count:
        gaps = sorted(
            ((right - left, left, right) for left, right in zip(values, values[1:])),
            reverse=True,
        )
        added = False
        for _gap, left, right in gaps:
            midpoint = (left + right) / 2.0
            candidate = _best_near_target(
                midpoint,
                samples,
                start=left + min_gap / 2,
                end=right - min_gap / 2,
                radius=scan_radius,
                selected=values,
                min_gap=min_gap,
            )
            if start < candidate < end_anchor and not _near(values, candidate, min_gap):
                values.append(candidate)
                values.sort()
                added = True
                break
        if added:
            continue
        for index in range(1, 200):
            candidate = round(start + (end_anchor - start) * index / 200.0, 3)
            if start < candidate < end_anchor and not _near(values, candidate, min_gap / 2):
                values.append(candidate)
                values.sort()
                added = True
                break
        if not added:
            raise ValueError("Segment过短，无法生成9个严格递增的真实时间锚点。")
    return values


def choose_times(
    segment: dict[str, Any],
    analysis: dict[str, Any],
    first_segment: bool,
) -> tuple[list[float], dict[float, str]]:
    start = _finite(segment["globalStart"])
    end = _finite(segment["globalEnd"])
    duration = end - start
    if duration <= 0:
        raise ValueError("Segment globalEnd 必须大于 globalStart。")

    tail_offset = min(0.08, max(0.04, duration / 100.0))
    end_anchor = round(end - tail_offset, 3)
    min_gap = min(0.12, max(0.04, duration / 100.0))
    selected = [round(start, 3), end_anchor]
    source: dict[float, str] = {round(start, 3): "start", end_anchor: "tail"}
    samples = _scan_samples(analysis)

    # Keep deterministic dense Hook coverage, but snap to a clearer frame already
    # available from the single-pass scan when one exists nearby.
    if first_segment:
        scan_fps = float(analysis.get("visualScan", {}).get("fps") or 4.0)
        snap_radius = max(0.22, 1.1 / max(1.0, scan_fps))
        for point in HOOK_POINTS:
            if not (start + min_gap < point < end_anchor - min_gap):
                continue
            value = _best_near_target(
                point,
                samples,
                start=start + min_gap,
                end=end_anchor - min_gap,
                radius=snap_radius,
                selected=selected,
                min_gap=min_gap,
            )
            if not _near(selected, value, min_gap):
                selected.append(value)
                source[value] = "hook"

    # Visual-change peaks now replace the old "scene cuts only" selection. One or
    # two slots remain reserved for pure timeline coverage so late Proof/CTA is not
    # crowded out by a busy action cluster.
    reserve_context = 1 if duration < 6 else 2
    visual_capacity = max(0, PANELS - len(selected) - reserve_context)
    visual_candidates = analysis.get("visualCandidates", [])
    if not isinstance(visual_candidates, list):
        visual_candidates = []

    # Backward-compatible fallback for cached v3.9 analysis files.
    if not visual_candidates:
        visual_candidates = [
            {"timestamp": value, "eventType": "cut", "changeScore": 1.0, "qualityScore": 0.5}
            for value in analysis.get("candidateCuts", [])
        ]

    for value, event in _visual_select(
        visual_candidates,
        selected,
        visual_capacity,
        start=start,
        end_anchor=end_anchor,
        min_gap=min_gap,
    ):
        selected.append(value)
        source[value] = "cut" if event == "cut" else "visual_change"

    selected = _largest_gap_fill(selected, start, end_anchor, PANELS, min_gap, samples)
    for value in selected:
        source.setdefault(value, "context")
    if len(selected) != PANELS or any(a >= b for a, b in zip(selected, selected[1:])):
        raise ValueError("自动锚点必须正好9个并严格递增。")
    return selected, source


def build_plan(analysis: dict[str, Any]) -> dict[str, Any]:
    segments = analysis.get("recommendedSegments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("benchmark-analysis.json 缺少 recommendedSegments。")
    cuts = [_finite(value) for value in analysis.get("candidateCuts", [])]
    result: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, 1):
        times, sources = choose_times(segment, analysis, index == 1)
        start = _finite(segment["globalStart"])
        near_start_cut = any(abs(cut - start) <= 0.35 for cut in cuts)
        anchors: list[dict[str, Any]] = []
        for position, timestamp in enumerate(times):
            source = sources[timestamp]
            if position == 0:
                event = "first_frame" if index == 1 else ("cut" if near_start_cut else "continuity_start")
                role = "hard"
            elif source == "cut":
                event, role = "cut", "hard"
            elif source == "visual_change":
                event, role = "action_process", "soft"
            elif source == "hook":
                event, role = "hook", "hard"
            else:
                event, role = "context", "context"
            anchors.append({
                "timestamp": timestamp,
                "anchorRole": role,
                "eventType": event,
                "semanticSource": "unobserved",
            })
        result.append({
            "segmentId": int(segment.get("segmentId", index)),
            "globalStart": start,
            "globalEnd": _finite(segment["globalEnd"]),
            "anchors": anchors,
        })
    return {
        "selectionMethod": "single-pass-visual-hook-coverage-v1",
        "segments": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        analysis_file = Path(args.analysis_file).expanduser().resolve()
        output = Path(args.output).expanduser().resolve()
        analysis = json.loads(analysis_file.read_text(encoding="utf-8"))
        plan = build_plan(analysis)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(output), **plan}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
