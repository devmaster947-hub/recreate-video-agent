import unittest

from scripts.storyboard_regions import (
    MAX_CANVAS_ASPECT_RATIO_DRIFT,
    validate_canvas_aspect_ratio,
)


class StoryboardRegionCanvasTests(unittest.TestCase):
    def test_canvas_aspect_ratio_drift_allows_14_9_percent(self) -> None:
        drift = validate_canvas_aspect_ratio(1149, 1000, 1000, 1000)
        self.assertAlmostEqual(drift, 0.149)
        self.assertEqual(MAX_CANVAS_ASPECT_RATIO_DRIFT, 0.15)

    def test_canvas_aspect_ratio_drift_rejects_15_1_percent(self) -> None:
        with self.assertRaisesRegex(ValueError, "15%"):
            validate_canvas_aspect_ratio(1151, 1000, 1000, 1000)

    def test_canvas_aspect_ratio_drift_rejects_invalid_dimensions(self) -> None:
        with self.assertRaisesRegex(ValueError, "正数"):
            validate_canvas_aspect_ratio(0, 1000, 1000, 1000)


if __name__ == "__main__":
    unittest.main()
