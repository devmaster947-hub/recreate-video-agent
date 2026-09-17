import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts.benchmark_analysis import validate_benchmark_duration


class BenchmarkDurationTests(unittest.TestCase):
    def test_six_minutes_is_allowed(self):
        self.assertEqual(validate_benchmark_duration(360), 360.0)

    def test_more_than_six_minutes_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "超过6分钟（360秒）上限"):
            validate_benchmark_duration(360.001)


if __name__ == "__main__":
    unittest.main()
