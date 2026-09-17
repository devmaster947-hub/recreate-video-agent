#!/usr/bin/env python3
"""Prepare a LibTV canvas and run all approved video segments in parallel."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from scripts import generation_manifest, local_video_cli, reference_audit, run_generation  # noqa: E402


class LibTVBatchError(RuntimeError):
    pass


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def parse_json_output(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise LibTVBatchError("LibTV CLI 没有返回可解析的JSON。")


class LibTVClient:
    def __init__(self, executable: Path, cwd: Path):
        self.executable = str(executable)
        self.cwd = cwd

    def call(self, *arguments: str, expect_json: bool = True) -> dict[str, Any] | str:
        command = [self.executable, *arguments]
        result = subprocess.run(
            command,
            cwd=self.cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"
            raise LibTVBatchError(f"LibTV命令失败：{' '.join(arguments[:3])}：{message}")
        return parse_json_output(result.stdout) if expect_json else result.stdout.strip()

    def account_info(self) -> dict[str, Any]:
        return self.call("account", "info")  # type: ignore[return-value]

    def model_info(self, model: str) -> dict[str, Any]:
        return self.call("model", model)  # type: ignore[return-value]

    def project_info(self, project_uuid: str) -> dict[str, Any]:
        return self.call("project", project_uuid)  # type: ignore[return-value]

    def create_project(self, name: str, workspace_id: int) -> str:
        result = self.call("project", "create", name, "--workspace", str(workspace_id))
        assert isinstance(result, dict)
        nested = result.get("project") if isinstance(result.get("project"), dict) else {}
        project_uuid = str(
            result.get("uuid") or result.get("projectUuid")
            or nested.get("uuid") or nested.get("projectUuid") or ""
        ).strip()
        if not project_uuid:
            raise LibTVBatchError("创建LibTV画布后未返回projectUuid。")
        return project_uuid

    def upload(self, project_uuid: str, name: str, file: str, x: int, y: int) -> str:
        result = self.call(
            "upload", name, "--project", project_uuid, "--file", file,
            "--type", "image", "--x", str(x), "--y", str(y),
        )
        assert isinstance(result, dict)
        node_key = str(result.get("nodeKey") or result.get("newNodeKey") or "").strip()
        if not node_key:
            raise LibTVBatchError(f"上传{name}后未返回nodeKey。")
        return node_key

    def create_video_node(
        self,
        project_uuid: str,
        name: str,
        prompt: str,
        ordered_node_keys: list[str],
        model: str,
        duration: int,
        ratio: str,
        resolution: str,
        x: int,
        y: int,
    ) -> str:
        arguments = video_node_arguments(
            project_uuid=project_uuid,
            name=name,
            prompt=prompt,
            ordered_node_keys=ordered_node_keys,
            model=model,
            duration=duration,
            ratio=ratio,
            resolution=resolution,
            x=x,
            y=y,
        )
        result = self.call(*arguments)
        assert isinstance(result, dict)
        node_key = str(result.get("nodeKey") or result.get("newNodeKey") or "").strip()
        if not node_key:
            raise LibTVBatchError(f"创建{name}后未返回nodeKey。")
        return node_key

    def run_video_node(self, project_uuid: str, node_key: str) -> dict[str, Any]:
        result = self.call("node", node_key, "--project", project_uuid, "--run")
        assert isinstance(result, dict)
        return result

    def download(self, project_uuid: str, node_key: str, output_dir: Path, vip: bool) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        arguments = [
            "download", "--project", project_uuid, "--node", node_key,
            "--out", str(output_dir), "--without-ai-watermark",
        ]
        if vip:
            arguments.append("--vip")
        stdout = self.call(*arguments, expect_json=False)
        assert isinstance(stdout, str)
        candidates = [Path(line.strip()) for line in stdout.splitlines() if line.strip()]
        file = next((item for item in reversed(candidates) if item.is_file()), None)
        if file is None:
            raise LibTVBatchError(f"下载节点{node_key}后未找到本地文件。")
        return file.resolve()


def video_node_arguments(
    *, project_uuid: str, name: str, prompt: str, ordered_node_keys: list[str],
    model: str, duration: int, ratio: str, resolution: str, x: int, y: int,
) -> list[str]:
    arguments = [
        "node", "--x", str(x), "--y", str(y), "create", name,
        "--project", project_uuid, "--type", "video", "--prompt", prompt,
        "--set", f"model={model}", "--set", "modeType=mixed2video",
        "--set", f"ratio={ratio}", "--set", f"resolution={resolution}",
        "--set", f"duration={duration}", "--set", "enableSound=on",
        "--set", "searchEnabled=0", "--set", "autoCompliance=1",
    ]
    for node_key in ordered_node_keys:
        arguments.extend(("--left", node_key))
    return arguments


def bind_prompt(prompt: str, ordered_node_keys: list[str]) -> str:
    references = "、".join(f"{{{{Node {node_key}}}}}" for node_key in ordered_node_keys)
    return f"参考图已按严格顺序绑定：{references}。\n\n{prompt.strip()}"


def extract_task(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    task = data.get("taskInfo") if isinstance(data, dict) and isinstance(data.get("taskInfo"), dict) else {}
    urls = data.get("url", []) if isinstance(data, dict) else []
    remote_url = str(urls[0]).strip() if isinstance(urls, list) and urls else ""
    status = task.get("status")
    if status != 2 or not remote_url:
        raise LibTVBatchError(f"LibTV视频节点未成功完成：status={status!r}")
    return {
        "taskId": str(task.get("taskId", "")),
        "remoteUrl": remote_url,
        "progressPercent": task.get("progressPercent"),
    }


def node_index(project: dict[str, Any]) -> dict[str, str]:
    nodes = project.get("nodes") if isinstance(project, dict) else None
    if not isinstance(nodes, list):
        raise LibTVBatchError("LibTV画布详情缺少nodes数组。")
    result: dict[str, str] = {}
    duplicates: set[str] = set()
    for item in nodes:
        if not isinstance(item, dict):
            continue
        name, node_key = str(item.get("name", "")).strip(), str(item.get("id") or item.get("nodeKey") or "").strip()
        if not name or not node_key:
            continue
        if name in result:
            duplicates.add(name)
        result[name] = node_key
    if duplicates:
        raise LibTVBatchError(f"LibTV画布存在重复节点名，无法安全复用：{sorted(duplicates)}")
    return result


def run_nodes_parallel(
    client: LibTVClient,
    project_uuid: str,
    nodes: dict[int, str],
    *, max_workers: int = 4,
) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    completed: dict[int, dict[str, Any]] = {}
    errors: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(nodes)))) as pool:
        futures = {
            pool.submit(client.run_video_node, project_uuid, node_key): segment_id
            for segment_id, node_key in nodes.items()
        }
        for future in as_completed(futures):
            segment_id = futures[future]
            try:
                completed[segment_id] = extract_task(future.result())
            except (OSError, ValueError, LibTVBatchError) as exc:
                errors[segment_id] = str(exc)
    return completed, errors


def default_libtv_model(manifest_model: str) -> str:
    normalized = manifest_model.strip().lower().replace(".", " ")
    if "seedance" in normalized and "fast" in normalized:
        return "Seedance 2.0 Fast VIP"
    return manifest_model.strip()


def existing_uploads(state: dict[str, Any]) -> dict[str, dict[str, str]]:
    uploads = state.get("uploads")
    result = dict(uploads) if isinstance(uploads, dict) else {}
    shared = state.get("sharedReferences")
    if isinstance(shared, dict):
        for item in shared.values():
            if isinstance(item, dict) and item.get("file") and item.get("nodeKey"):
                result[str(Path(str(item["file"])).expanduser().resolve())] = {
                    "nodeKey": str(item["nodeKey"]), "name": str(item.get("name", "")),
                }
    for segment in state.get("segments", []) if isinstance(state.get("segments"), list) else []:
        for item in segment.get("orderedReferences", []) if isinstance(segment, dict) else []:
            if isinstance(item, dict) and item.get("file") and item.get("nodeKey"):
                result[str(Path(str(item["file"])).expanduser().resolve())] = {
                    "nodeKey": str(item["nodeKey"]), "name": str(item.get("name", "")),
                }
    return result


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schemaVersion": "1.0", "provider": "libtv_cli", "uploads": {}, "segments": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise LibTVBatchError("LibTV编排状态文件必须是JSON对象。")
    segments = value.get("segments")
    if isinstance(segments, list):
        value["segments"] = {str(item.get("segmentId")): item for item in segments if isinstance(item, dict)}
    value["uploads"] = existing_uploads(value)
    value.setdefault("segments", {})
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--workspace-id", type=int)
    parser.add_argument("--project-uuid")
    parser.add_argument("--project-name")
    parser.add_argument("--libtv-model")
    parser.add_argument("--generation-approved", action="store_true")
    parser.add_argument("--vip-download", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    try:
        manifest_path = Path(args.manifest).expanduser().resolve()
        root = manifest_path.parent
        data = generation_manifest.load_manifest(manifest_path)
        prompts_file = Path(data["videoPrompts"]["file"]).expanduser().resolve()
        prompt_payload = json.loads(prompts_file.read_text(encoding="utf-8"))["videoPrompts"]
        segments = prompt_payload["segments"]
        generation_manifest.validate_prompt_record(prompt_payload, data)
        audit = reference_audit.audit(data, segments)
        model = args.libtv_model or default_libtv_model(str(data.get("userConfig", {}).get("videoModel", "")))
        ratio = str(data.get("userConfig", {}).get("aspectRatio", "9:16"))
        resolution = str(data.get("userConfig", {}).get("resolution", "720p"))
        plans = [
            {
                "segment": segment,
                "files": [str(Path(value).expanduser().resolve()) for value in run_generation.prepare_references(data, segment, root, str(data.get("userConfig", {}).get("videoModel", "seedance-2-fast")))],
            }
            for segment in segments
        ]
        if args.plan_only:
            print(json.dumps({
                "ok": True, "status": "plan_only", "model": model,
                "segments": [{"segmentId": int(item["segment"]["segmentId"]), "orderedFiles": item["files"]} for item in plans],
                "audit": audit,
            }, ensure_ascii=False))
            return 0
        if not args.generation_approved:
            raise LibTVBatchError("LibTV首次视频生成必须处于用户确认授权内；请传入--generation-approved。")

        executable = local_video_cli.resolve_libtv_cli()
        client = LibTVClient(executable, root)
        account = client.account_info()
        model_info = client.model_info(model)
        schema = model_info.get("schema", {}) if isinstance(model_info, dict) else {}
        duration_spec = schema.get("properties", {}).get("duration", {}) if isinstance(schema, dict) else {}
        minimum, maximum = int(duration_spec.get("min", 1)), int(duration_spec.get("max", 60))
        for plan in plans:
            duration = int(plan["segment"]["duration"])
            if not minimum <= duration <= maximum:
                raise LibTVBatchError(f"Segment {plan['segment']['segmentId']} 时长{duration}秒超出LibTV模型范围{minimum}-{maximum}秒。")

        state_path = root / "libtv-generation.json"
        state = load_state(state_path)
        project_uuid = str(args.project_uuid or state.get("projectUuid") or "").strip()
        if project_uuid:
            project = client.project_info(project_uuid)
        else:
            if not args.workspace_id:
                raise LibTVBatchError("首次创建LibTV画布必须传入--workspace-id；后续会从状态文件自动复用。")
            project_uuid = client.create_project(args.project_name or root.name, args.workspace_id)
            project = client.project_info(project_uuid)
        known_nodes = node_index(project)
        state.update({
            "schemaVersion": "1.0", "provider": "libtv_cli", "projectUuid": project_uuid,
            "workspaceId": args.workspace_id or state.get("workspaceId"), "model": model,
            "status": "preparing", "account": {"memberName": account.get("memberName")},
        })
        atomic_write_json(state_path, state)

        upload_names: dict[str, str] = {}
        product_paths = {str(Path(value).expanduser().resolve()) for value in run_generation.product_files(data)}
        creator_by_path = {
            str(Path(str(item["file"])).expanduser().resolve()): str(item.get("creatorId", "creator"))
            for item in data.get("creators", []) if isinstance(item, dict) and item.get("file")
        }
        for plan in plans:
            segment_id = int(plan["segment"]["segmentId"])
            for position, file in enumerate(plan["files"]):
                if file in upload_names:
                    continue
                if position == 0:
                    upload_names[file] = f"S{segment_id:02d}-最终Storyboard"
                elif file in product_paths:
                    upload_names[file] = f"产品参考图-{len([v for v in upload_names.values() if v.startswith('产品参考图-')]) + 1}"
                elif file in creator_by_path:
                    upload_names[file] = f"{creator_by_path[file]}-人物参考图"
                else:
                    upload_names[file] = f"参考图-{len(upload_names) + 1}"

        uploads = state.setdefault("uploads", {})
        for index, (file, name) in enumerate(upload_names.items()):
            record = uploads.get(file) if isinstance(uploads.get(file), dict) else {}
            node_key = str(record.get("nodeKey", "")).strip()
            if not node_key:
                node_key = known_nodes.get(name, "")
            if not node_key:
                node_key = client.upload(project_uuid, name, file, (index % 3) * 380 - 760, (index // 3) * 720)
                known_nodes[name] = node_key
            uploads[file] = {"nodeKey": node_key, "name": name}
            atomic_write_json(state_path, state)

        state_segments = state.setdefault("segments", {})
        runnable: dict[int, str] = {}
        for index, plan in enumerate(plans):
            segment = plan["segment"]
            segment_id = int(segment["segmentId"])
            key = str(segment_id)
            record = state_segments.get(key) if isinstance(state_segments.get(key), dict) else {}
            ordered_keys = [str(uploads[file]["nodeKey"]) for file in plan["files"]]
            node_name = f"S{segment_id:02d}-生成视频"
            node_key = str(record.get("videoNodeKey", "")).strip()
            if not node_key:
                node_key = known_nodes.get(node_name, "")
            bound_prompt = bind_prompt(str(segment["prompt"]), ordered_keys)
            if not node_key:
                node_key = client.create_video_node(
                    project_uuid, node_name, bound_prompt, ordered_keys, model,
                    int(segment["duration"]), ratio, resolution, 760, index * 720,
                )
                known_nodes[node_name] = node_key
            record.update({
                "segmentId": segment_id, "duration": int(segment["duration"]),
                "videoNodeKey": node_key, "orderedFiles": plan["files"],
                "orderedNodeKeys": ordered_keys, "prompt": bound_prompt,
            })
            if record.get("status") not in {"succeeded", "run_started_uncertain"}:
                record["status"] = "prepared"
            state_segments[key] = record
            atomic_write_json(state_path, state)

        for key, record in state_segments.items():
            segment_id = int(key)
            if record.get("status") == "succeeded":
                continue
            if record.get("status") == "run_started_uncertain":
                current = client.call("node", str(record["videoNodeKey"]), "--project", project_uuid)
                assert isinstance(current, dict)
                try:
                    record.update(extract_task(current))
                    record["status"] = "succeeded"
                    atomic_write_json(state_path, state)
                    continue
                except LibTVBatchError:
                    pass
                raise LibTVBatchError(
                    f"Segment {segment_id}已有状态不确定的LibTV任务；为避免重复付费，本次不重新提交。"
                )
            record["status"] = "run_started_uncertain"
            runnable[segment_id] = str(record["videoNodeKey"])
        atomic_write_json(state_path, state)

        completed, errors = run_nodes_parallel(client, project_uuid, runnable)
        for segment_id, task in completed.items():
            record = state_segments[str(segment_id)]
            record.update(task)
            record["status"] = "succeeded"
            atomic_write_json(state_path, state)
        for segment_id, error in errors.items():
            state_segments[str(segment_id)]["error"] = error
        atomic_write_json(state_path, state)
        if errors:
            raise LibTVBatchError(
                "；".join(f"Segment {segment_id}任务状态不确定：{message}" for segment_id, message in sorted(errors.items()))
                + "。已保存节点和状态，禁止自动重提。"
            )

        output_dir = root / "segments"
        for key in sorted(state_segments, key=int):
            record = state_segments[key]
            local_file = Path(str(record.get("localFile", ""))).expanduser()
            if not local_file.is_file():
                local_file = client.download(project_uuid, str(record["videoNodeKey"]), output_dir, args.vip_download)
                record["localFile"] = str(local_file)
            generation_manifest.set_video(
                manifest_path, int(key), "success", candidateId="candidate-01",
                taskId=str(record.get("taskId", "")), provider="libtv_cli",
                output_file=str(local_file), media={"url": record.get("remoteUrl", "")},
                model=model, resolution=resolution,
            )
            atomic_write_json(state_path, state)

        state["status"] = "segments_completed"
        atomic_write_json(state_path, state)
        latest = generation_manifest.load_manifest(manifest_path)
        latest["videoGeneration"] = {
            "status": "segments_completed", "generated": True,
            "provider": "libtv_cli", "model": model,
        }
        generation_manifest.save_manifest(manifest_path, latest)
        print(json.dumps({
            "ok": True, "status": "segments_completed", "projectUuid": project_uuid,
            "canvasUrl": f"https://www.liblib.tv/canvas?projectId={project_uuid}",
            "state": str(state_path),
            "segments": [state_segments[key] for key in sorted(state_segments, key=int)],
        }, ensure_ascii=False))
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, LibTVBatchError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
