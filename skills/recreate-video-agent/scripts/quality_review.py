#!/usr/bin/env python3
"""Create non-blocking aligned quality reports for generated videos."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from scripts.benchmark_analysis import analyze as analyze_video
except ModuleNotFoundError:
    from benchmark_analysis import analyze as analyze_video


WEIGHTS = {
    "firstFrameHook": 15,
    "shotOrderCuts": 20,
    "productIdentityGeometryInteraction": 25,
    "keyStatesRewardDensity": 20,
    "motionContinuity": 10,
    "audioVoiceRhythm": 10,
}


def resolve(name: str, value: str | None = None) -> str:
    local = Path.home() / ".local" / "bin" / name
    found = shutil.which(name)
    candidates = [value, str(local) if local.is_file() else None, found]
    checked: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in checked:
            continue
        checked.add(candidate)
        if name == "ffmpeg":
            probe = subprocess.run([candidate, "-hide_banner", "-filters"], text=True, capture_output=True, check=False)
            if probe.returncode or not any(line.split()[1:2] == ["drawtext"] for line in probe.stdout.splitlines()):
                continue
        return candidate
    suffix = "（需要 drawtext 滤镜）" if name == "ffmpeg" else ""
    raise ValueError(f"当前环境缺少 {name}{suffix}。")


def resolve_optional(name: str, value: str | None = None) -> str | None:
    try:
        return resolve(name, value)
    except ValueError:
        return None


def run(command: list[str]) -> None:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:] or "视频质量分析失败。")


def comparison_sheet(ffmpeg: str, benchmark: Path, candidate: Path, duration: float, output: Path) -> None:
    timestamps = [round(duration * value, 3) for value in (0, .1, .2, .3, .4, .5, .6, .7, .8, .95)]
    command = [ffmpeg, "-y"]
    for timestamp in timestamps:
        command.extend(["-ss", str(timestamp), "-i", str(benchmark), "-ss", str(timestamp), "-i", str(candidate)])
    filters: list[str] = []
    pairs: list[str] = []
    for index, timestamp in enumerate(timestamps):
        left, right, pair = f"o{index}", f"n{index}", f"p{index}"
        label = f"{timestamp:.2f}s"
        filters.append(f"[{index * 2}:v]scale=240:426:force_original_aspect_ratio=decrease,pad=240:426:(ow-iw)/2:(oh-ih)/2:black,drawtext=text='原片 {label}':x=8:y=8:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.6[{left}]")
        filters.append(f"[{index * 2 + 1}:v]scale=240:426:force_original_aspect_ratio=decrease,pad=240:426:(ow-iw)/2:(oh-ih)/2:black,drawtext=text='复刻 {label}':x=8:y=8:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.6[{right}]")
        filters.append(f"[{left}][{right}]hstack=inputs=2[{pair}]")
        pairs.append(f"[{pair}]")
    layout = "0_0|480_0|0_426|480_426|0_852|480_852|0_1278|480_1278|0_1704|480_1704"
    filters.append("".join(pairs) + f"xstack=inputs=10:layout={layout}[out]")
    output.parent.mkdir(parents=True, exist_ok=True)
    command.extend(["-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", "1", str(output)])
    run(command)


def build_review(
    ffmpeg: str,
    ffprobe: str | None,
    benchmark: Path,
    candidate: Path,
    output_dir: Path,
    *,
    target_duration: int | float | None = None,
    profile: str = "fast",
) -> dict[str, Any]:
    """Build a non-blocking quality report.

    fast (default): ffprobe/ffmpeg media metadata only. No low-res visual scan and
    no aligned comparison sheet. This is the normal production path.
    full: preserve the legacy visual scan + aligned comparison sheet for an
    explicit deep review request.
    """
    profile = str(profile or "fast").strip().lower()
    if profile not in {"fast", "full"}:
        raise ValueError("quality profile 必须是 fast 或 full。")

    if profile == "full":
        original = analyze_video(ffmpeg, ffprobe, benchmark)
        generated = analyze_video(ffmpeg, ffprobe, candidate)
        original_media = original["media"]
        generated_media = generated["media"]
    else:
        # Reuse the lightweight media probe from benchmark_analysis. This avoids
        # scanning both videos frame-by-frame on every successful generation.
        try:
            from scripts.benchmark_analysis import probe as probe_media
        except ModuleNotFoundError:
            from benchmark_analysis import probe as probe_media
        original_media = probe_media(ffprobe, benchmark, ffmpeg)
        generated_media = probe_media(ffprobe, candidate, ffmpeg)
        original = {"media": original_media}
        generated = {"media": generated_media}

    source_duration = float(original_media["duration"])
    target = float(target_duration) if target_duration is not None else source_duration
    if target <= 0 or target > source_duration + .5:
        raise ValueError("质检目标时长必须大于0且不得超过原片可用范围。")
    duration = min(target, source_duration, float(generated_media["duration"]))
    actual = float(generated_media["duration"])
    tolerance = max(.25, target * .03)
    technical_failures: list[str] = []
    if abs(target - actual) > tolerance:
        technical_failures.append("duration_out_of_tolerance")
    if original_media["width"] * generated_media["height"] != original_media["height"] * generated_media["width"]:
        technical_failures.append("aspect_ratio_mismatch")
    if original_media["hasAudio"] and not generated_media["hasAudio"]:
        technical_failures.append("missing_audio")

    sheet: Path | None = None
    pacing: dict[str, Any]
    if profile == "full":
        sheet = output_dir / "aligned-comparison.jpg"
        comparison_sheet(ffmpeg, benchmark, candidate, duration, sheet)
        original_cuts = [t for t in original.get("candidateCuts", []) if t < target]
        generated_cuts = [t for t in generated.get("candidateCuts", []) if t < duration]
        ratio = len(generated_cuts) / len(original_cuts) if original_cuts else None
        pacing = {
            "status": "reviewed",
            "sourceCandidateCuts": len(original_cuts),
            "candidateCuts": len(generated_cuts),
            "retentionRatio": ratio,
            "warnings": ["pacing_collapse_requires_review"] if ratio is not None and ratio < .8 else [],
            "meaning": "候选数量差异仅提示复核，不证明真实丢镜头",
        }
    else:
        pacing = {
            "status": "not_scanned_fast_profile",
            "warnings": [],
            "meaning": "默认快速质检仅检查媒体技术指标；需要镜头节奏对齐时显式运行full模式。",
        }

    return {
        "status": "reported",
        "qualityProfile": profile,
        "threshold": 85,
        "benchmark": original,
        "candidate": generated,
        "targetDuration": target,
        "replicationWindow": {"start": 0, "end": target},
        "technical": {"passed": not technical_failures, "failures": technical_failures, "durationTolerance": tolerance},
        "comparisonSheet": str(sheet) if sheet else None,
        "weights": WEIGHTS,
        "scores": {},
        "totalScore": None,
        "hardFailures": technical_failures,
        "passed": False,
        "technicalPassed": not technical_failures,
        "semanticStatus": "not_reviewed",
        "pacing": pacing,
        "deliveryBlocked": False,
        "requiresVisualReview": False,
        "mayAutoRegenerate": False,
    }


def finalize(report: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    scores = assessment.get("scores", {})
    for key, maximum in WEIGHTS.items():
        value = scores.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= maximum:
            raise ValueError(f"评分 {key} 必须在 0-{maximum} 之间。")
    hard = list(dict.fromkeys(list(report.get("technical", {}).get("failures", [])) + list(assessment.get("hardFailures", []))))
    total = sum(float(scores[key]) for key in WEIGHTS)
    report.update({
        "status": "passed" if total >= 85 and not hard else "failed",
        "scores": scores,
        "totalScore": total,
        "hardFailures": hard,
        "passed": total >= 85 and not hard,
        "findings": assessment.get("findings", []),
        "recommendedRepairLayer": assessment.get("recommendedRepairLayer", "none" if total >= 85 and not hard else "step4"),
        "requiresUserApprovalForNewCandidate": not (total >= 85 and not hard),
        "deliveryBlocked": False,
        "requiresVisualReview": False,
        "mayAutoRegenerate": False,
    })
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--benchmark", required=True)
    analyze.add_argument("--candidate", required=True)
    analyze.add_argument("--output-dir", required=True)
    analyze.add_argument("--ffmpeg")
    analyze.add_argument("--ffprobe")
    analyze.add_argument("--target-duration", type=float)
    analyze.add_argument("--profile", choices=("fast", "full"), default="fast")
    score = sub.add_parser("score")
    score.add_argument("--report", required=True)
    score.add_argument("--assessment", required=True)
    score.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.command == "analyze":
            output_dir = Path(args.output_dir).expanduser().resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            result = build_review(
                resolve("ffmpeg", args.ffmpeg), resolve_optional("ffprobe", args.ffprobe),
                Path(args.benchmark).expanduser().resolve(), Path(args.candidate).expanduser().resolve(),
                output_dir, target_duration=args.target_duration, profile=args.profile,
            )
            output = output_dir / "quality-report.json"
        else:
            source = Path(args.report).expanduser().resolve()
            result = finalize(json.loads(source.read_text(encoding="utf-8")), json.loads(Path(args.assessment).read_text(encoding="utf-8")))
            output = Path(args.output).expanduser().resolve() if args.output else source
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(output), **result}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
