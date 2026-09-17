import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import blueprint_timeline, generation_manifest, storyboard, prepare_prompt_handoff


def server_plan(layout="3x3"):
    count = 9 if layout == "3x3" else 16
    anchors = []
    for i in range(count):
        anchors.append({
            "timestamp": round(i * 11.8 / max(1, count - 1), 6),
            "shotId": "boundary" if i in {0, count - 1} else f"s{i}",
            "anchorRole": "hard" if i == 0 else "soft",
            "eventType": "first_frame" if i == 0 else "context",
            "semanticSource": "unobserved",
        })
    return {
        "schemaVersion": "1.0",
        "plannerVersion": "1",
        "policyVersion": "1.0.0",
        "sourceDuration": 11.841,
        "targetDuration": 12,
        "segments": [{
            "segmentId": 1, "globalStart": 0, "globalEnd": 12, "duration": 12,
            "anchors": anchors,
        }],
        "blueprintWarnings": [],
        "unresolvedCuts": [],
    }


class TimelineTests(unittest.TestCase):
    def test_local_timeline_only_persists_server_plan(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = generation_manifest.command_init(type("Args", (), {"output_root": td, "task_id": "test", "reuse": False})())
            plan_path = root / "plan.json"
            plan = server_plan("4x4")
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            generation_manifest.update(manifest, lambda data: data.update({
                "replicationPlan": {"source": "RecreateVideoPromptV3", "status": "succeeded", "file": str(plan_path)},
                "timelinePlan": {"file": str(plan_path), "source": "server", "schemaVersion": "1.0", "plannerVersion": "1"},
            }))
            output = root / "segment-anchors.json"
            result = blueprint_timeline.persist_plan(manifest, output)
            self.assertEqual(result["source"], "server")
            self.assertEqual(result["layouts"], ["4x4"])
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["schemaVersion"], "1.0")
            self.assertEqual(saved["plannerVersion"], "1")
            self.assertEqual(saved["segments"][0]["layout"], "4x4")
            self.assertEqual(saved["segments"][0]["anchorCount"], 16)
            manifest_data = generation_manifest.load_manifest(manifest)
            self.assertEqual(manifest_data["benchmarkVideo"]["analysis"]["recommendedSegments"][0]["duration"], 12)

    def test_missing_server_plan_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = generation_manifest.command_init(type("Args", (), {"output_root": td, "task_id": "test", "reuse": False})())
            with self.assertRaisesRegex(ValueError, "replicationPlan"):
                blueprint_timeline.persist_plan(manifest, Path(td) / "segment-anchors.json")

    def test_storyboard_and_handoff_accept_server_16_cells(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = generation_manifest.command_init(type("Args", (), {"output_root": td, "task_id": "test", "reuse": False})())
            data = generation_manifest.load_manifest(path)
            segment = server_plan("4x4")["segments"][0]
            normalized = storyboard.normalize_segment(segment, 1)
            self.assertEqual(len(normalized["anchors"]), 16)
            board = root / "board.png"
            board.write_bytes(b"unaltered")
            data["userConfig"]["duration"] = 12
            data["benchmarkVideo"]["analysis"] = {"recommendedSegments": [
                {key: segment[key] for key in ("segmentId", "globalStart", "globalEnd", "duration")}
            ]}
            for anchor in segment["anchors"]:
                anchor.update(personPresent=False, personCount=0, creatorIds=[])
            data["storyboards"]["original"] = [dict(segment, storyboardId=1, file=str(board))]
            generation_manifest.save_manifest(path, data)
            prepare_prompt_handoff.prepare(path, root / "handoff")
            saved = generation_manifest.load_manifest(path)["storyboards"]["generation"][0]
            self.assertEqual(saved["layout"], "4x4")
            self.assertEqual(len(saved["anchors"]), 16)
            self.assertEqual(board.read_bytes(), b"unaltered")


if __name__ == "__main__":
    unittest.main()
