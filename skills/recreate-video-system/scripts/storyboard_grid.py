#!/usr/bin/env python3
"""Build a deterministic one-shot-per-cell storyboard prompt."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


SHOT_PATTERN = re.compile(r"镜头\s*(\d+)")
TIME_RANGE_PATTERN = re.compile(
    r"(?m)^\s*((?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?)"
    r"\s*[-–—~～至]\s*"
    r"((?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?)"
)
CHINESE_SECONDS_PATTERN = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*至\s*(\d+(?:\.\d+)?)\s*秒"
)
BRACKET_SECONDS_PATTERN = re.compile(
    r"\[\s*(\d+(?:\.\d+)?)\s*(?:s|秒)"
    r"\s*[-–—~～至]\s*"
    r"(\d+(?:\.\d+)?)\s*(?:s|秒)\s*\]",
    re.IGNORECASE,
)


def extract_shot_numbers(prompt: str) -> list[int]:
    numbers: list[int] = []
    for match in SHOT_PATTERN.finditer(prompt):
        value = int(match.group(1))
        if value not in numbers:
            numbers.append(value)
    return numbers


def extract_time_ranges(prompt: str) -> list[str]:
    return [
        f"{match.group(1)}-{match.group(2)}"
        for match in TIME_RANGE_PATTERN.finditer(prompt)
    ]


def extract_chinese_second_ranges(prompt: str) -> list[str]:
    return [
        f"{match.group(1)}至{match.group(2)}秒"
        for match in CHINESE_SECONDS_PATTERN.finditer(prompt)
    ]


def extract_bracket_second_ranges(prompt: str) -> list[str]:
    return [
        f"{match.group(1)}s-{match.group(2)}s"
        for match in BRACKET_SECONDS_PATTERN.finditer(prompt)
    ]


def extract_shot_order(prompt: str) -> tuple[list[int] | list[str], str]:
    numbers = extract_shot_numbers(prompt)
    if numbers:
        return numbers, "shot headings"
    bracket_ranges = extract_bracket_second_ranges(prompt)
    if bracket_ranges:
        return bracket_ranges, "bracketed second ranges"
    time_ranges = extract_time_ranges(prompt)
    if time_ranges:
        return time_ranges, "time ranges"
    chinese_second_ranges = extract_chinese_second_ranges(prompt)
    if chinese_second_ranges:
        return chinese_second_ranges, "Chinese second ranges"
    return [], ""


def choose_grid(shot_count: int) -> tuple[int, int]:
    if shot_count < 1:
        raise ValueError("shot_count must be at least 1")
    if shot_count == 1:
        return 1, 1
    if shot_count <= 4:
        return 2, 2
    return 3, 3


def select_storyboard_shots(shots, max_panels: int = 9):
    if max_panels < 2:
        raise ValueError("max_panels must be at least 2")
    if len(shots) <= max_panels:
        return shots

    selected_indices = {
        round(index * (len(shots) - 1) / (max_panels - 1))
        for index in range(max_panels)
    }
    return [shots[index] for index in sorted(selected_indices)]


def build_storyboard_prompt(
    *, segment_id: str, title: str, segment_prompt: str
) -> tuple[str, int, int, int, int]:
    shots, boundary_type = extract_shot_order(segment_prompt)
    if not shots:
        raise ValueError(
            "Segment prompt contains no explicit shot boundaries. "
            "Use shot headings or line-leading time ranges; do not invent storyboard cells."
        )
    total_shot_count = len(shots)
    rows, columns = choose_grid(total_shot_count)
    target_panel_count = rows * columns
    selected_shots = select_storyboard_shots(shots)
    selected_shot_count = len(selected_shots)
    if total_shot_count > 9 or selected_shot_count == 9:
        panel_fill_note = (
            "Use exactly nine selected representative shots, one selected source shot per panel."
        )
    elif selected_shot_count == 1:
        panel_fill_note = "Use one representative keyframe from the explicit shot."
    elif selected_shot_count == 2:
        panel_fill_note = (
            "Generate four chronological keyframes. Represent both explicit shots and use "
            "different beginning/process/ending states from those shots to fill all four panels."
        )
    elif selected_shot_count == 3:
        panel_fill_note = (
            "Generate four chronological keyframes. Represent every explicit shot at least once. "
            "Use the fourth panel as a different earlier or later state from the longest or most "
            "action-rich existing shot."
        )
    elif selected_shot_count == 4:
        panel_fill_note = (
            "Generate exactly one representative keyframe from each explicit shot."
        )
    else:
        panel_fill_note = (
            "Represent every selected shot at least once. Fill the remaining panels with "
            "different temporal states from the longest or most action-rich existing shots."
        )
    representative_shot_note = (
        f"This storyboard uses {selected_shot_count} representative shots distributed "
        "across the full timeline."
        if total_shot_count > 9
        else f"This storyboard uses all {selected_shot_count} explicit shots."
    )
    prompt = f"""Use case: ads-marketing
Asset type: 9:16 vertical storyboard contact sheet for Segment {segment_id}, containing equal-size 9:16 vertical storyboard cells
Primary request: Create one complete {rows} rows × {columns} columns storyboard grid containing exactly {target_panel_count} fully rendered visual keyframe panels.
Storyboard boundary detection: Selected representative shots were detected from {boundary_type}.
Every grid cell must contain a valid visual keyframe. Fill the entire grid. Do not leave any cell blank, empty, black, white, or transparent.
The original Segment contains {total_shot_count} explicit shots. The selected source shots are: {selected_shots}.
Selection summary: {representative_shot_note}
Preserve the source timeline and arrange all visual keyframes chronologically from left to right and top to bottom.
When the selected shot count is smaller than {target_panel_count}, derive the additional panels only from different temporal states inside the existing selected shots, such as:
- beginning state
- action process
- later action state
- product-operation close-up
- ending state
- visible result or before/after boundary
These additional panels are keyframes inside existing shots, not new shots or new story events.
Do not invent new actions, people, products, locations, functions, claims, results, or story events.
Do not duplicate an identical frame merely to fill the grid.
Every panel must show one clear moment in time. Do not use split-screen, picture-in-picture, or multiple time states inside one panel.
Do not merge multiple shots into one panel.
Do not switch to 1×3, 3×1, horizontal strips, vertical strips, free-form collage, or any layout other than the specified {rows} × {columns} grid.
Every cell must be an equal-size 9:16 vertical frame. The complete contact sheet must also remain 9:16.
Panel allocation: {panel_fill_note}
Input images: Treat every supplied product image only as product identity/appearance reference. Treat every supplied creator image only as the recurring character identity reference for this Segment.
Style/medium: realistic UGC commercial storyboard frames, consistent product and character identity across cells.
Constraints: preserve the actions, camera angles, locations, lighting, and continuity from the selected shots in the source prompt.
Avoid: subtitles, captions, labels, shot numbers, logos, watermarks, extra advertising text, unrelated people, unrelated products, and references from other Segments.

Segment title: {title}
Source Segment prompt (verbatim, read-only):
{segment_prompt}
"""
    return prompt, total_shot_count, selected_shot_count, rows, columns


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create an adaptive storyboard grid prompt from an exact workflow Segment prompt."
    )
    parser.add_argument("--segment-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--output-prompt")
    args = parser.parse_args()

    source = Path(args.prompt_file).read_text(encoding="utf-8")
    try:
        prompt, total_shot_count, selected_shot_count, rows, columns = build_storyboard_prompt(
            segment_id=args.segment_id,
            title=args.title,
            segment_prompt=source,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.output_prompt:
        destination = Path(args.output_prompt)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(prompt, encoding="utf-8")
    else:
        print(prompt, end="")
    print(
        f"storyboard_grid={rows}x{columns} "
        f"total_shot_count={total_shot_count} "
        f"selected_shot_count={selected_shot_count}",
        file=__import__("sys").stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
