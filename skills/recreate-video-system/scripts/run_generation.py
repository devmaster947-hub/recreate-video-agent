#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


SKILL_DIR = Path(os.environ.get("RECREATE_VIDEO_SKILL_DIR", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(SKILL_DIR))
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from scripts import concat_videos, generation_manifest, run_cli, storyboard_grid  # noqa: E402


def next_versioned(path: Path) -> Path:
    if not path.exists():
        return path
    version = 2
    while True:
        candidate = path.with_name(f"{path.stem}-v{version}{path.suffix}")
        if not candidate.exists():
            return candidate
        version += 1


class Tracker:
    def __init__(self, manifest: Path):
        self.manifest = manifest
        self.lock = threading.Lock()

    def mutate(self, callback):
        with self.lock:
            data = generation_manifest.load_manifest(self.manifest)
            callback(data)
            generation_manifest.save_manifest(self.manifest, data)

    def snapshot(self) -> dict:
        with self.lock:
            return generation_manifest.load_manifest(self.manifest)

    def task_entry(self, kind: str, identifier: str) -> dict:
        data = self.snapshot()
        return dict(data.get("generation_tasks", {}).get(kind, {}).get(str(identifier), {}))

    def set_workflow_status(self, status: str):
        self.mutate(lambda data: data.__setitem__("workflow_status", status))

    def set_decisions(self, image_plan: str, generate_video: bool):
        def update(data):
            data["decisions"]["creator_images"] = "yes" if image_plan in {"all", "creator-only"} else "skipped"
            data["decisions"]["storyboards"] = "yes" if image_plan in {"all", "storyboard-only"} else "skipped"
            data["decisions"]["videos"] = "yes" if generate_video else "skipped"
            data["image_backend"] = "lzstudio-image" if image_plan != "skip" else None
        self.mutate(update)

    def record_task(self, kind: str, identifier: str, task_id: str, attempt: int | None = None):
        def update(data):
            tasks = data.setdefault("generation_tasks", {}).setdefault(kind, {})
            entry = tasks.setdefault(str(identifier), {})
            if attempt is None:
                previous = entry.get("submit_id")
                if previous and previous != task_id:
                    raise ValueError(f"{kind} {identifier} 已绑定其他任务 ID")
                entry["submit_id"] = task_id
            else:
                attempts = entry.setdefault("attempts", [])
                if not any(item.get("attempt") == attempt for item in attempts):
                    attempts.append({"attempt": attempt, "submit_id": task_id})
            entry["status"] = "submitted"
        self.mutate(update)

    def finish_task(self, kind: str, identifier: str, status: str, **values):
        def update(data):
            entry = data.setdefault("generation_tasks", {}).setdefault(kind, {}).setdefault(str(identifier), {})
            entry["status"] = status
            entry.update(values)
        self.mutate(update)

    def add_creator(self, creator_id: str, image_path: Path, segments: list[int], media: dict):
        with self.lock:
            generation_manifest.command_add_creator(argparse.Namespace(
                manifest=str(self.manifest), creator_id=str(creator_id), image=str(image_path), segment=segments
            ))
            generation_manifest.command_set_public_media(argparse.Namespace(
                manifest=str(self.manifest), file=str(image_path), url=media["url"],
                mime_type=media.get("mimeType", "image/png"), expired_at=media.get("expiredAt", "")
            ))

    def add_storyboard(self, segment_id: int, image_path: Path, grid: str, media: dict):
        with self.lock:
            generation_manifest.command_add_storyboard(argparse.Namespace(
                manifest=str(self.manifest), segment_id=segment_id, image=str(image_path), grid=grid
            ))
            generation_manifest.command_set_public_media(argparse.Namespace(
                manifest=str(self.manifest), file=str(image_path), url=media["url"],
                mime_type=media.get("mimeType", "image/png"), expired_at=media.get("expiredAt", "")
            ))

    def references(self, segment_id: int) -> dict:
        with self.lock:
            data = generation_manifest.load_manifest(self.manifest)
            return generation_manifest.select_segment_references(data, segment_id)

    def set_segment(self, segment: dict, status: str, provider: str, submit_id: str | None = None,
                    references: list[str] | None = None, output_file: Path | None = None):
        with self.lock:
            generation_manifest.command_set_segment(argparse.Namespace(
                manifest=str(self.manifest), segment_id=int(segment["segmentId"]),
                title=segment.get("title"), duration=int(segment["duration"]), status=status,
                provider=provider, submit_id=submit_id, reference=references or [],
                output_file=str(output_file) if output_file else None,
            ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--image-plan", choices=("all", "creator-only", "storyboard-only", "skip"), required=True)
    parser.add_argument("--generate-video", choices=("yes", "no"), required=True)
    parser.add_argument("--video-provider", choices=("auto", "official_cli", "lingzhi_cli"), default="auto")
    parser.add_argument("--segment-id", type=int, action="append", default=[])
    args = parser.parse_args()
    manifest = Path(args.manifest).expanduser().resolve()
    root = manifest.parent
    manifest_data = generation_manifest.load_manifest(manifest)
    result_path = Path(manifest_data["prompt_task"]["result_file"]).expanduser().resolve()
    result_envelope = json.loads(result_path.read_text(encoding="utf-8"))
    result = result_envelope.get("output", result_envelope)
    segments = result["videoPrompts"]["segments"]
    if args.segment_id:
        selected_ids = list(dict.fromkeys(args.segment_id))
        available_ids = {int(item["segmentId"]) for item in segments}
        missing_ids = [value for value in selected_ids if value not in available_ids]
        if missing_ids:
            parser.error(f"未找到 Segment ID：{missing_ids}")
        selected = set(selected_ids)
        segments = [item for item in segments if int(item["segmentId"]) in selected]
    creators = result.get("creatorPrompts", {}).get("creators", [])
    tracker = Tracker(manifest)
    generate_creators = args.image_plan in {"all", "creator-only"}
    generate_storyboards = args.image_plan in {"all", "storyboard-only"}
    generate_videos = args.generate_video == "yes"
    tracker.set_decisions(args.image_plan, generate_videos)

    image_credits = 0
    creator_outputs: list[dict] = []
    storyboard_outputs: list[dict] = []
    video_outputs: list[dict] = []

    if generate_creators:
        tracker.set_workflow_status("creator_images_pending")
        print(json.dumps({"stage": "creator_images_pending", "count": len(creators)}, ensure_ascii=False), flush=True)

    def creator_worker(creator: dict) -> dict:
        creator_id = str(creator["creatorId"])
        creator_prompt = generation_manifest.apply_prompt_override(
            manifest_data, "creator-images", creator_id, creator["prompt"]
        )
        previous = tracker.task_entry("creators", creator_id)
        previous_output = Path(previous.get("output_file", "")) if previous.get("output_file") else None
        if previous.get("status") == "success" and previous_output and previous_output.is_file():
            return {"ok": True, "creatorId": creator_id, "path": str(previous_output),
                    "media": previous["media"], "credits": previous.get("credits", 0), "reused": True}
        attempts = previous.get("attempts", [])
        if previous.get("status") == "submitted" and attempts:
            task_id = attempts[-1]["submit_id"]
            try:
                media = run_cli.poll_image(task_id)
                generated = {"status": "success", "taskIds": [item["submit_id"] for item in attempts], "media": media}
            except run_cli.TaskFailedError as exc:
                if len(creator.get("appearsInSegments", [])) >= 2 and len(attempts) < 2:
                    retry_id = run_cli.submit_image(creator_prompt, aspect_ratio="9:16", resolution="1K")
                    tracker.record_task("creators", creator_id, retry_id, len(attempts) + 1)
                    media = run_cli.poll_image(retry_id)
                    generated = {"status": "success", "taskIds": [task_id, retry_id], "media": media}
                else:
                    generated = {"status": "failed", "error": str(exc),
                                 "affectedSegments": creator.get("appearsInSegments", [])}
        else:
            generated = run_cli.generate_creator_image(
                creator_prompt, creator.get("appearsInSegments", []),
                aspect_ratio="9:16", resolution="1K",
                on_submit=lambda attempt, task_id: tracker.record_task("creators", creator_id, task_id, attempt),
            )
        if generated.get("status") != "success":
            tracker.finish_task("creators", creator_id, "failed", error=generated.get("error", "任务失败"),
                                affected_segments=generated.get("affectedSegments", []))
            return {"ok": False, "creator": creator, "result": generated}
        media = generated["media"]
        output = run_cli.download_media(media, root / "creators" / f"creator-{creator_id}.png")
        raw = run_cli.fetch_image(generated["taskIds"][-1])
        credits = run_cli._credits(raw)
        tracker.add_creator(creator_id, output, [int(v) for v in creator.get("appearsInSegments", [])], media)
        tracker.finish_task("creators", creator_id, "success", output_file=str(output), media=media, credits=credits)
        return {"ok": True, "creatorId": creator_id, "path": str(output), "media": media, "credits": credits}

    if generate_creators and creators:
        with ThreadPoolExecutor(max_workers=len(creators)) as pool:
            creator_results = [future.result() for future in as_completed([pool.submit(creator_worker, item) for item in creators])]
        failures = [item for item in creator_results if not item["ok"]]
        if failures:
            tracker.set_workflow_status("creator_images_failed")
            print(json.dumps({"stage": "creator_images_failed", "failures": failures}, ensure_ascii=False), flush=True)
            return 2
        creator_results.sort(key=lambda item: int(item["creatorId"]))
        image_credits += sum(item["credits"] for item in creator_results)
        creator_outputs = creator_results
        print(json.dumps({"stage": "creator_images_complete", "outputs": creator_results}, ensure_ascii=False), flush=True)

    if generate_storyboards:
        tracker.set_workflow_status("storyboards_pending")
        print(json.dumps({"stage": "storyboards_pending", "count": len(segments)}, ensure_ascii=False), flush=True)

    def storyboard_worker(segment: dict) -> dict:
        segment_id = int(segment["segmentId"])
        previous = tracker.task_entry("storyboards", str(segment_id))
        previous_output = Path(previous.get("output_file", "")) if previous.get("output_file") else None
        if previous.get("status") == "success" and previous_output and previous_output.is_file():
            return {"segmentId": segment_id, "path": str(previous_output), "media": previous["media"],
                    "credits": previous.get("credits", 0), "grid": previous.get("grid", ""), "reused": True}
        references = tracker.references(segment_id)
        if references["upload_required"]:
            raise RuntimeError(f"Segment {segment_id} 引用素材缺少可用公网 URL：{references['upload_required']}")
        prompt, _, _, rows, columns = storyboard_grid.build_storyboard_prompt(
            segment_id=str(segment_id), title=segment["title"], segment_prompt=segment["prompt"]
        )
        prompt = generation_manifest.apply_prompt_override(
            manifest_data, "storyboards", segment_id, prompt
        )
        prompt_path = root / "prompts" / f"storyboard-segment-{segment_id}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        task_id = previous.get("submit_id") if previous.get("status") == "submitted" else None
        if not task_id:
            task_id = run_cli.submit_image(
                prompt, reference_images=references["reference_urls"], aspect_ratio="9:16", resolution="1K"
            )
            tracker.record_task("storyboards", str(segment_id), task_id)
        media = run_cli.poll_image(task_id)
        output = run_cli.download_media(media, root / "storyboards" / f"segment-{segment_id}.png")
        raw = run_cli.fetch_image(task_id)
        credits = run_cli._credits(raw)
        tracker.add_storyboard(segment_id, output, f"{rows}x{columns}", media)
        tracker.finish_task("storyboards", str(segment_id), "success", output_file=str(output), media=media,
                            credits=credits, grid=f"{rows}x{columns}")
        return {"segmentId": segment_id, "path": str(output), "media": media, "credits": credits,
                "grid": f"{rows}x{columns}"}

    if generate_storyboards:
        with ThreadPoolExecutor(max_workers=len(segments)) as pool:
            storyboard_results = [future.result() for future in as_completed([pool.submit(storyboard_worker, item) for item in segments])]
        storyboard_results.sort(key=lambda item: item["segmentId"])
        image_credits += sum(item["credits"] for item in storyboard_results)
        storyboard_outputs = storyboard_results
        print(json.dumps({"stage": "storyboards_complete", "outputs": storyboard_results}, ensure_ascii=False), flush=True)

    availability = run_cli.detect_video_providers()
    if run_cli._video_model_id(manifest_data["model"]) not in run_cli.OFFICIAL_VIDEO_MODEL_IDS:
        availability["official_cli"] = False
    provider = run_cli.resolve_video_provider(args.video_provider, availability=availability) if generate_videos else ""
    if generate_videos:
        tracker.set_workflow_status("videos_pending")
        print(json.dumps({"stage": "videos_pending", "count": len(segments), "provider": provider}, ensure_ascii=False), flush=True)

    def video_worker(segment: dict) -> dict:
        segment_id = int(segment["segmentId"])
        video_prompt = generation_manifest.apply_prompt_override(
            manifest_data, "videos", segment_id, segment["prompt"]
        )
        current_manifest = tracker.snapshot()
        previous_segment = dict(current_manifest.get("segments", {}).get(str(segment_id), {}))
        previous_task = tracker.task_entry("videos", str(segment_id))
        previous_output = Path(previous_segment.get("output_file", "")) if previous_segment.get("output_file") else None
        if previous_segment.get("status") == "success" and previous_output and previous_output.is_file() and previous_task.get("media"):
            return {"ok": True, "segmentId": segment_id, "path": str(previous_output),
                    "media": previous_task["media"], "credits": previous_task.get("credits", 0),
                    "provider": previous_segment.get("provider", provider), "reused": True}
        references = tracker.references(segment_id)
        if references["upload_required"]:
            raise RuntimeError(f"Segment {segment_id} 引用素材缺少可用公网 URL：{references['upload_required']}")
        submitted = {"id": None}

        def on_submit(task_id: str):
            submitted["id"] = task_id
            tracker.set_segment(segment, "submitted", provider, submit_id=task_id,
                                references=references["ordered_images"])

        if previous_segment.get("status") in {"submitted", "querying"} and previous_segment.get("submit_id"):
            task_id = previous_segment["submit_id"]
            submitted["id"] = task_id
            if previous_segment.get("provider") == "official_cli":
                raw = run_cli.poll_task(run_cli.fetch_official_video, task_id, interval=20.0, timeout=3600.0)
                generated = {"video": run_cli._media(raw, "video/mp4"), "credits": run_cli._credits(raw), "errorMessage": ""}
            else:
                media = run_cli.poll_video(task_id)
                raw = run_cli.fetch_video(task_id)
                generated = {"video": media, "credits": run_cli._credits(raw), "errorMessage": ""}
        else:
            generated = run_cli.generate_video(
                manifest_data["model"], video_prompt, int(segment["duration"]),
                video_provider=provider, reference_images=references["reference_urls"],
                reference_files=references["ordered_images"], availability=availability,
                aspect_ratio="9:16", resolution="720p", on_submit=on_submit,
            )
        task_id = submitted["id"]
        if not generated.get("video"):
            tracker.set_segment(segment, "failed", provider, submit_id=task_id,
                                references=references["ordered_images"])
            tracker.finish_task("videos", str(segment_id), "failed", submit_id=task_id,
                                error=generated.get("errorMessage", "任务失败"), provider=provider)
            return {"ok": False, "segmentId": segment_id, "error": generated.get("errorMessage", "任务失败")}
        media = generated["video"]
        output = run_cli.download_media(media, root / "videos" / f"segment-{segment_id}.mp4")
        tracker.set_segment(segment, "success", provider, submit_id=task_id,
                            references=references["ordered_images"], output_file=output)
        tracker.finish_task("videos", str(segment_id), "success", output_file=str(output),
                            media=media, credits=generated.get("credits", 0), provider=provider)
        return {"ok": True, "segmentId": segment_id, "path": str(output), "media": media,
                "credits": generated.get("credits", 0), "provider": provider}

    final_path = None
    video_results = []
    if generate_videos:
        with ThreadPoolExecutor(max_workers=len(segments)) as pool:
            video_results = [future.result() for future in as_completed([pool.submit(video_worker, item) for item in segments])]
        video_results.sort(key=lambda item: item["segmentId"])
        failures = [item for item in video_results if not item["ok"]]
        if failures:
            tracker.set_workflow_status("videos_failed")
            print(json.dumps({"stage": "videos_failed", "failures": failures}, ensure_ascii=False), flush=True)
            return 3
        video_outputs = video_results
        print(json.dumps({"stage": "videos_complete", "outputs": video_results}, ensure_ascii=False), flush=True)

        ffmpeg = shutil.which("ffmpeg") or str(Path.home() / ".local" / "bin" / "ffmpeg")
        final_path = next_versioned(root / "videos" / "final.mp4")
        data = generation_manifest.load_manifest(manifest)
        ordered = concat_videos.ordered_segments(data)
        concat_videos.render([path for _, path in ordered], final_path, ffmpeg)
        generation_manifest.command_set_final(argparse.Namespace(
            manifest=str(manifest), output_file=str(final_path), segment_id=[segment_id for segment_id, _ in ordered]
        ))
    tracker.set_workflow_status("complete")

    video_credits = sum(item["credits"] for item in video_results)
    balances = {}
    balance_errors = {}
    providers_used = (["lingzhi_cli"] if args.image_plan != "skip" else []) + ([provider] if generate_videos else [])
    for name in providers_used:
        if name in balances:
            continue
        try:
            balances[name] = run_cli.get_remaining_credits(name)
        except Exception as exc:
            balance_errors[name] = str(exc)

    final_output = run_cli.build_final_output(
        videos=[{"segmentId": item["segmentId"], **item["media"]} for item in video_outputs],
        creator_images=[{"creatorId": item["creatorId"], **item["media"]} for item in creator_outputs],
        video_prompts=result["videoPrompts"],
        creator_reference_plan=result.get("creatorPrompts", {}),
        credits_consumed=image_credits + video_credits,
    )
    final_output["result"]["storyboards"] = [
        {"segmentId": item["segmentId"], **item["media"]} for item in storyboard_outputs
    ]
    final_output["result"]["finalVideo"] = str(final_path) if final_path else ""
    credits_by_provider = {"lingzhi_cli": image_credits}
    if provider:
        credits_by_provider[provider] = credits_by_provider.get(provider, 0) + video_credits
    final_output["creditsByProvider"] = credits_by_provider
    final_output["remainingCredits"] = balances
    final_output["balanceErrors"] = balance_errors
    output_json = root / "result.json"
    output_json.write_text(json.dumps(final_output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "stage": "complete", "final": str(final_path) if final_path else "", "result": str(output_json),
        "creatorImages": creator_outputs, "storyboards": storyboard_outputs, "videos": video_outputs,
        "creditsByProvider": credits_by_provider,
        "remainingCredits": balances, "balanceErrors": balance_errors,
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
