from __future__ import annotations

import unittest

from scripts.storyboard_grid import (
    build_storyboard_prompt,
    choose_grid,
    select_storyboard_shots,
)


def numbered_shot_prompt(shot_count: int) -> str:
    return "\n".join(
        f"镜头 {shot_number}：动作 {shot_number}"
        for shot_number in range(1, shot_count + 1)
    )


class StoryboardGridTests(unittest.TestCase):
    def test_layout_is_selected_from_shot_count(self):
        expected_grids = {
            1: (1, 1),
            2: (2, 2),
            3: (2, 2),
            4: (2, 2),
            5: (3, 3),
            9: (3, 3),
            10: (3, 3),
            20: (3, 3),
        }
        for shot_count, expected_grid in expected_grids.items():
            with self.subTest(shot_count=shot_count):
                self.assertEqual(choose_grid(shot_count), expected_grid)

    def test_large_shot_counts_are_not_rejected(self):
        for shot_count in (10, 20, 100):
            with self.subTest(shot_count=shot_count):
                self.assertEqual(choose_grid(shot_count), (3, 3))

    def test_representative_shot_selection(self):
        for shot_count, expected_count in ((9, 9), (10, 9), (12, 9), (20, 9)):
            shots = list(range(1, shot_count + 1))
            with self.subTest(shot_count=shot_count):
                selected = select_storyboard_shots(shots)
                self.assertEqual(len(selected), expected_count)
                self.assertEqual(selected[0], shots[0])
                self.assertEqual(selected[-1], shots[-1])
                self.assertEqual(selected, sorted(selected))
                self.assertEqual(len(selected), len(set(selected)))
                self.assertEqual(selected, select_storyboard_shots(shots))

    def test_nine_shots_are_returned_without_sampling(self):
        shots = list(range(1, 10))
        self.assertIs(select_storyboard_shots(shots), shots)

    def test_twelve_shot_prompt_preserves_total_and_selected_counts(self):
        prompt, total_shot_count, selected_shot_count, rows, columns = (
            build_storyboard_prompt(
                segment_id="12",
                title="Timeline",
                segment_prompt=numbered_shot_prompt(12),
            )
        )
        self.assertEqual(total_shot_count, 12)
        self.assertEqual(selected_shot_count, 9)
        self.assertEqual(rows, 3)
        self.assertEqual(columns, 3)
        self.assertIn("original Segment contains 12 explicit shots", prompt)
        self.assertIn("uses 9 representative shots", prompt)
        self.assertIn("Do not merge multiple shots into one panel", prompt)

    def test_three_shot_prompt_uses_two_by_two_and_fills_all_panels(self):
        prompt, total_shot_count, selected_shot_count, rows, columns = build_storyboard_prompt(
            segment_id="1",
            title="Hook",
            segment_prompt=numbered_shot_prompt(3),
        )
        self.assertEqual(total_shot_count, 3)
        self.assertEqual(selected_shot_count, 3)
        self.assertEqual(rows, 2)
        self.assertEqual(columns, 2)
        self.assertIn("exactly 4 fully rendered", prompt)
        self.assertIn("Every grid cell must contain", prompt)
        self.assertIn("fourth panel", prompt)
        self.assertNotIn("remain empty", prompt)
        self.assertNotIn("unused grid position", prompt)
        self.assertNotIn("placeholder", prompt)

    def test_bracketed_second_ranges_use_two_by_two_grid(self):
        prompts = (
            "\n".join(
                (
                    "[0s-2s] 清洗轮胎印",
                    "[2s-5s] 清洗外墙",
                    "[5s-10s] 清洗桌椅",
                )
            ),
            "\n".join(
                (
                    "[0秒-2秒]",
                    "[2.5s-5s]",
                    "[5s–10s]",
                )
            ),
        )
        for segment_prompt in prompts:
            with self.subTest(segment_prompt=segment_prompt):
                _, total, selected, rows, columns = build_storyboard_prompt(
                    segment_id="1",
                    title="Cleaning",
                    segment_prompt=segment_prompt,
                )
                self.assertEqual((total, selected, rows, columns), (3, 3, 2, 2))

    def test_acceptance_counts_for_nine_twelve_and_twenty_shots(self):
        expected = {
            9: (9, 9, 3, 3),
            12: (12, 9, 3, 3),
            20: (20, 9, 3, 3),
        }
        for shot_count, expected_counts in expected.items():
            with self.subTest(shot_count=shot_count):
                _, total, selected, rows, columns = build_storyboard_prompt(
                    segment_id=str(shot_count),
                    title="Acceptance",
                    segment_prompt=numbered_shot_prompt(shot_count),
                )
                self.assertEqual(
                    (total, selected, rows, columns),
                    expected_counts,
                )


if __name__ == "__main__":
    unittest.main()
