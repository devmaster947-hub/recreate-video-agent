from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import libtv_batch_generate  # noqa: E402


class ParallelClient:
    def __init__(self):
        self.active = 0
        self.maximum = 0
        self.lock = threading.Lock()

    def run_video_node(self, project_uuid: str, node_key: str):
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        time.sleep(0.05)
        with self.lock:
            self.active -= 1
        return {
            "data": {
                "url": [f"https://example.invalid/{node_key}.mp4"],
                "taskInfo": {"taskId": f"task-{node_key}", "status": 2, "progressPercent": 100},
            }
        }


class LibTVBatchGenerateTests(unittest.TestCase):
    def test_prompt_uses_exact_node_keys_in_order(self):
        prompt = libtv_batch_generate.bind_prompt("中文镜头说明", ["board-uuid", "product-uuid", "creator-uuid"])
        self.assertTrue(prompt.startswith("参考图已按严格顺序绑定"))
        self.assertLess(prompt.index("{{Node board-uuid}}"), prompt.index("{{Node product-uuid}}"))
        self.assertLess(prompt.index("{{Node product-uuid}}"), prompt.index("{{Node creator-uuid}}"))

    def test_video_node_arguments_connect_uuid_values_not_names(self):
        arguments = libtv_batch_generate.video_node_arguments(
            project_uuid="project-uuid", name="S01-生成视频", prompt="提示词",
            ordered_node_keys=["board-uuid", "product-uuid"], model="Seedance 2.0 Fast VIP",
            duration=11, ratio="9:16", resolution="720p", x=760, y=0,
        )
        left_values = [arguments[index + 1] for index, value in enumerate(arguments) if value == "--left"]
        self.assertEqual(left_values, ["board-uuid", "product-uuid"])
        self.assertNotIn("--run", arguments)

    def test_segment_runs_are_parallel(self):
        client = ParallelClient()
        completed, errors = libtv_batch_generate.run_nodes_parallel(
            client, "project-uuid", {1: "video-1", 2: "video-2"}
        )
        self.assertFalse(errors)
        self.assertEqual(set(completed), {1, 2})
        self.assertEqual(client.maximum, 2)


if __name__ == "__main__":
    unittest.main()
