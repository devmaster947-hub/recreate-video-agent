#!/usr/bin/env python3
"""Submit one benchmark video to RecreateVideoPromptV3 through LZStudio CLI."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

try:
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt  # type: ignore[import-not-found]
except ImportError:  # POSIX
    msvcrt = None  # type: ignore[assignment]

try:
    from scripts import generation_manifest, service_privacy
    from scripts.compress_benchmark_video import CompressionError, prepare_benchmark_video
except ModuleNotFoundError:
    import generation_manifest  # type: ignore[no-redef]
    import service_privacy  # type: ignore[no-redef]
    from compress_benchmark_video import CompressionError, prepare_benchmark_video  # type: ignore[no-redef]


WORKFLOW_ID = "RecreateVideoPromptV3"
PLANNER_VERSION = "2"
PLAN_SCHEMA_MAJOR = "1"
PENDING = {"created", "pending", "processing", "running", "queued", "submitted"}
SUCCESS = {"succeeded", "success", "completed", "complete"}
FAILED = {"failed", "failure", "error", "cancelled", "canceled", "timeout", "timedout"}
MAX_SERVER_ATTEMPTS = 2


class AnalysisError(RuntimeError):
    pass


def acquire_run_lock(manifest_path: Path):
    """Prevent two client processes from uploading/submitting the same manifest."""
    lock_path = manifest_path.parent / "analysis" / "server-video-analysis.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(" ")
            handle.flush()
        handle.seek(0)
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            raise OSError("当前平台没有可用文件锁实现。")
    except (BlockingIOError, OSError):
        handle.close()
        raise AnalysisError(
            "同一manifest已有服务端拆解进程在运行；必须继续等待该进程，禁止启动第二个上传或submit。"
        ) from None
    handle.seek(0)
    handle.truncate()
    json.dump({"pid": os.getpid(), "acquiredAt": time.time()}, handle, ensure_ascii=False)
    handle.flush()
    os.fsync(handle.fileno())
    return handle


def release_run_lock(handle) -> None:
    try:
        handle.seek(0)
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


def submission_receipt_path(manifest_path: Path) -> Path:
    return manifest_path.parent / "analysis" / "recreate-video-prompt-v3-submit-receipt.json"


def load_submit_attempts(manifest_path: Path) -> list[dict[str, Any]]:
    path = submission_receipt_path(manifest_path)
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, AttributeError):
        return []
    if not isinstance(value, dict):
        return []
    attempts = value.get("attempts")
    if isinstance(attempts, list):
        result = []
        for position, item in enumerate(attempts, 1):
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("taskId", "")).strip()
            if task_id:
                result.append({
                    "attempt": int(item.get("attempt", position)),
                    "taskId": task_id,
                    "recordedAt": item.get("recordedAt"),
                })
        if result:
            return result
    task_id = str(value.get("taskId", "")).strip()
    return [{"attempt": 1, "taskId": task_id, "recordedAt": value.get("recordedAt")}] if task_id else []


def load_submit_receipt(manifest_path: Path) -> str:
    attempts = load_submit_attempts(manifest_path)
    return attempts[-1]["taskId"] if attempts else ""


def persist_submit_receipt(manifest_path: Path, task_id: str) -> Path:
    """Durably append a task id before any later manifest mutation."""
    path = submission_receipt_path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    attempts = load_submit_attempts(manifest_path)
    if any(item["taskId"] == task_id for item in attempts):
        return path
    if len(attempts) >= MAX_SERVER_ATTEMPTS:
        raise AnalysisError(f"服务端拆解已达最大尝试次数{MAX_SERVER_ATTEMPTS}，禁止继续提交。")
    recorded_at = time.time()
    attempts.append({"attempt": len(attempts) + 1, "taskId": task_id, "recordedAt": recorded_at})
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "workflowId": WORKFLOW_ID,
        "taskId": task_id,
        "attempt": len(attempts),
        "maxAttempts": MAX_SERVER_ATTEMPTS,
        "recordedAt": recorded_at,
        "attempts": attempts,
    }
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return path


def reserve_submission(manifest_path: Path, request_path: Path) -> None:
    """Create a fail-closed marker before uploads begin."""
    def mutate(data: dict[str, Any]) -> None:
        existing = data.get("videoBlueprint")
        if isinstance(existing, dict) and existing.get("workflowId") == WORKFLOW_ID:
            raise AnalysisError("manifest已存在服务端拆解记录，禁止创建第二个提交预留。")
        data["videoBlueprint"] = {
            "source": "RecreateVideoPromptV3", "workflowId": WORKFLOW_ID,
            "taskId": "", "status": "preparing_submission",
            "requestFile": str(request_path), "file": "",
        }
    generation_manifest.update(manifest_path, mutate)


def parse_json_output(raw: str) -> dict[str, Any]:
    text = raw.strip()
    candidates = [text, *reversed([line.strip() for line in text.splitlines() if line.strip()])]
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise AnalysisError("LZStudio CLI返回内容不是JSON对象。")


def http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def walk_dicts(value: Any):
    queue = [value]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            yield current
            queue.extend(item for item in current.values() if isinstance(item, (dict, list)))
        elif isinstance(current, list):
            queue.extend(item for item in current if isinstance(item, (dict, list)))


def extract_media(response: Mapping[str, Any], mime_type: str) -> dict[str, Any]:
    for item in walk_dicts(response):
        url = item.get("url") or item.get("downloadUrl") or item.get("outputUrl")
        if http_url(url):
            return {
                "url": str(url).strip(),
                "mimeType": mime_type,
                "expiredAt": item.get("expiredAt") or None,
            }
    raise AnalysisError("upload响应缺少有效HTTP(S)媒体URL。")


def media_from_url(value: str) -> dict[str, Any]:
    url = str(value or "").strip()
    if not http_url(url):
        raise AnalysisError("--benchmark-url必须是有效的HTTP(S)地址。")
    return {"url": url, "mimeType": "video/mp4", "expiredAt": None}


def extract_task_id(response: Mapping[str, Any]) -> str:
    for item in walk_dicts(response):
        value = item.get("id", item.get("taskId"))
        if not isinstance(value, bool) and isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    raise AnalysisError("task submit响应缺少任务ID。")


def task_state(response: Mapping[str, Any]) -> str:
    for item in walk_dicts(response):
        value = item.get("status") or item.get("state")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def extract_blueprint(response: Mapping[str, Any]) -> dict[str, Any]:
    for item in walk_dicts(response):
        if item.get("success") is False:
            message = item.get("errorMessage") or item.get("message") or "服务端视频拆解失败。"
            service_privacy.raise_if_authorization_unavailable(item)
            raise AnalysisError(service_privacy.public_error(message, "服务端视频拆解失败。"))
    candidate: Any = None
    for item in walk_dicts(response):
        if isinstance(item.get("videoBlueprint"), dict):
            candidate = item["videoBlueprint"]
            break
        if isinstance(item.get("videoAnalysisSummary"), dict):
            candidate = item["videoAnalysisSummary"]
    if not isinstance(candidate, dict) or not candidate:
        raise AnalysisError("RecreateVideoPromptV3返回缺少有效videoBlueprint。")
    return dict(candidate)

def extract_replication_plan(response: Mapping[str, Any]) -> dict[str, Any]:
    """Extract and validate the stable server-side replicationPlan contract."""
    candidate: Any = None
    for item in walk_dicts(response):
        if isinstance(item.get("replicationPlan"), dict):
            candidate = item["replicationPlan"]
            break
    if not isinstance(candidate, dict) or not candidate:
        raise AnalysisError("RecreateVideoPromptV3返回缺少有效replicationPlan。")
    schema = str(candidate.get("schemaVersion", "")).strip()
    if not schema or schema.split(".", 1)[0] != PLAN_SCHEMA_MAJOR:
        raise AnalysisError(f"replicationPlan协议版本不兼容：{schema or '缺失'}。")
    planner = str(candidate.get("plannerVersion", "")).strip()
    if planner not in {"1", "2"}:
        raise AnalysisError(f"服务端Planner版本不匹配：期望{PLANNER_VERSION}，实际{planner or '缺失'}。")
    segments = candidate.get("segments")
    if not isinstance(segments, list) or not segments:
        raise AnalysisError("replicationPlan缺少有效segments。")
    for index, segment in enumerate(segments, 1):
        if not isinstance(segment, dict):
            raise AnalysisError(f"replicationPlan第{index}个Segment不是对象。")
        anchors = segment.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            raise AnalysisError(f"replicationPlan第{index}个Segment缺少anchors。")
        for field in ("segmentId", "globalStart", "globalEnd", "duration"):
            if field not in segment:
                raise AnalysisError(f"replicationPlan第{index}个Segment缺少{field}。")
    return dict(candidate)


def bundled_cli_candidate(system_name: str | None = None, machine: str | None = None) -> Path | None:
    system_value = system_name or platform.system()
    architecture = (machine or platform.machine()).lower()
    if system_value == "Windows" and architecture in {"amd64", "x86_64", "x64"}:
        return Path(__file__).resolve().parent.parent / "cli" / "windows-x64" / "lzstudio.exe"
    if system_value == "Darwin" and architecture in {"arm64", "aarch64"}:
        return Path(__file__).resolve().parent.parent / "cli" / "macos-arm64" / "lzstudio"
    return None


def resolve_cli(explicit: str | None) -> str:
    bundled = bundled_cli_candidate()
    candidates = [explicit, os.environ.get("LZSTUDIO_CLI"), shutil.which("lzstudio"), shutil.which("lzstudio.exe"), bundled]
    for value in candidates:
        if not value:
            continue
        path = Path(value).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            completed = subprocess.run(
                [str(path), "task", "submit", "--help"],
                capture_output=True, text=True, check=False, timeout=30,
            )
            help_text = completed.stdout + completed.stderr
            if "workflow-id" in help_text and "--input" in help_text:
                return str(path.resolve())
    raise AnalysisError(
        "未发现支持`task submit/fetch`的LZStudio CLI；请在本机升级CLI或设置LZSTUDIO_CLI。"
    )


def load_key() -> str:
    for name in ("LZSTUDIO_API_KEY", "RECREATE_VIDEO_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    config = Path.home() / ".recreate-video" / "config.json"
    if config.is_file():
        try:
            value = json.loads(config.read_text(encoding="utf-8")).get("apiKey", "")
        except (OSError, json.JSONDecodeError, AttributeError):
            value = ""
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise AnalysisError(
        "本机未配置灵智工坊 API Key；请前往 https://www.lingzhiai.com.cn/ 获取，"
        "然后在本地环境设置LZSTUDIO_API_KEY或RECREATE_VIDEO_API_KEY。"
    )


def run_cli(cli: str, key: str, arguments: list[str], timeout: float) -> dict[str, Any]:
    prefix = 2 if arguments and arguments[0] == "task" else 1
    command = [cli, *arguments[:prefix], "--api-key", key, *arguments[prefix:]]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=timeout, shell=False,
        )
    except subprocess.TimeoutExpired:
        raise AnalysisError(f"LZStudio CLI调用超时（{timeout:g}秒）。") from None
    except OSError as exc:
        raise AnalysisError(f"无法启动LZStudio CLI：{exc}") from None
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "未知错误").replace(key, "[REDACTED]")
        service_privacy.raise_if_authorization_unavailable(detail)
        raise AnalysisError(
            f"LZStudio CLI失败：{service_privacy.public_error(detail[:1000], '未知错误')}"
        )
    parsed = parse_json_output(completed.stdout)
    service_privacy.raise_if_authorization_unavailable(parsed)
    return parsed


def verify_key(cli: str, key: str) -> None:
    """Require a successful, non-generative remote authentication check."""
    run_cli(cli, key, ["account", "--credits"], 30)


def display_model(config: Mapping[str, Any]) -> str:
    model = str(config.get("videoModel", "seedance-2-fast"))
    mapping = {
        "seedance-2-fast": "Seedance2 Fast",
        "seedance-2-mini": "Seedance2 Mini",
        "seedance-2": "Seedance2",
    }
    return mapping.get(model, model)


def raw_storyboard_specs(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    boards = manifest.get("storyboards", {}).get("original", [])
    segments = manifest.get("benchmarkVideo", {}).get("analysis", {}).get("recommendedSegments", [])
    if not isinstance(boards, list) or not boards:
        raise AnalysisError("提交服务端拆解前必须生成并登记非空raw Storyboard。")
    if not isinstance(segments, list) or len(segments) != len(boards):
        raise AnalysisError("raw Storyboard数量必须与锁定Segment Plan一致。")
    windows = {int(item["segmentId"]): item for item in segments}
    result: list[dict[str, Any]] = []
    for position, board in enumerate(sorted(boards, key=lambda item: int(item["storyboardId"])), 1):
        storyboard_id = int(board["storyboardId"])
        segment_id = int(board.get("segmentId", storyboard_id))
        if segment_id != position or segment_id not in windows:
            raise AnalysisError("raw Storyboard必须按连续Segment顺序登记。")
        window = windows[segment_id]
        start, end = float(window["globalStart"]), float(window["globalEnd"])
        path = Path(str(board.get("file", ""))).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise AnalysisError(f"raw Storyboard不存在或为空：{path}")
        mime_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        result.append({
            "segmentId": segment_id,
            "storyboardId": storyboard_id,
            "globalStart": start,
            "globalEnd": end,
            "duration": end - start,
            "filePath": str(path),
            "mimeType": mime_type,
        })
    return result


def build_input(
    manifest: Mapping[str, Any],
    media: Mapping[str, Any],
    raw_storyboards: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    config = manifest.get("userConfig")
    if not isinstance(config, dict):
        raise AnalysisError("manifest.userConfig必须是对象。")
    mode = str(config.get("durationMode", "source"))
    duration = config.get("duration", config.get("requestedDuration"))
    if mode != "source" and (
        isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0
    ):
        raise AnalysisError("部分复刻必须在manifest中记录有效的正数目标时长。")
    duration_label = "跟原视频一致" if mode == "source" else f"仅复刻原视频0～{int(duration)}秒"
    requirements = str(config.get("customRequirement", "") or "").strip()
    if mode != "source":
        requirements = f"仅分析原视频0～{int(duration)}秒，忽略此后的内容。 {requirements}".strip()

    technical = manifest.get("benchmarkVideo", {}).get("analysis", {})
    if not isinstance(technical, dict):
        technical = {}
    source_duration = technical.get("media", {}).get("duration") if isinstance(technical.get("media"), dict) else None
    target_duration_source = technical.get("targetDurationSource")

    result: dict[str, Any] = {
        # Stable server contract. Major algorithm changes use a new plannerVersion;
        # ordinary tuning stays inside the server-side Planner Policy.
        "plannerVersion": str(config.get("plannerVersion", "2" if str(manifest.get("skillVersion", "")) == "5.1" else "1")),
        "benchmarkVideo": {
            "url": media["url"], "mimeType": "video/mp4", "expiredAt": media.get("expiredAt") or None,
        },
        "userConfig": {
            "model": display_model(config),
            "newVideoDuration": duration_label,
            "targetCountry": str(config.get("targetCountry", "跟原视频一致")),
            "targetLanguage": str(config.get("targetLanguage", "跟原视频一致")),
            "otherRequirements": requirements,
            "blueprintSchemaVersion": "7.0" if str(config.get("plannerVersion", "2" if str(manifest.get("skillVersion", "")) == "5.1" else "1")) == "2" else "6.0",
            "technicalCutCandidates": technical.get("candidateCuts", []),
            "targetDuration": config.get("duration"),
            # 以下字段只供服务端Code节点做确定性规划；工作流不会把它们注入Gemini Prompt。
            "sourceDuration": source_duration,
            "targetDurationSource": target_duration_source,
        },
    }
    return result


def merged_attempt_history(manifest_path: Path, existing: Any) -> list[dict[str, Any]]:
    """Merge durable receipt history into the manifest without duplicating task ids."""
    attempts = [dict(item) for item in existing] if isinstance(existing, list) else []
    known = {str(item.get("taskId", "")) for item in attempts if isinstance(item, dict)}
    for receipt in load_submit_attempts(manifest_path):
        task_id = receipt["taskId"]
        if task_id in known:
            continue
        attempts.append({
            "attempt": receipt["attempt"],
            "taskId": task_id,
            "status": "submitted",
            "submittedAt": receipt.get("recordedAt"),
        })
        known.add(task_id)
    attempts.sort(key=lambda item: int(item.get("attempt", 0)))
    return attempts


def update_submission(
    manifest_path: Path,
    task_id: str,
    request_path: Path,
    media: Mapping[str, Any],
    raw_storyboards: list[Mapping[str, Any]],
    attempt_number: int,
) -> None:
    def mutate(data: dict[str, Any]) -> None:
        existing = data.get("videoBlueprint")
        attempts = merged_attempt_history(
            manifest_path,
            existing.get("attempts", []) if isinstance(existing, dict) else [],
        )
        for attempt in attempts:
            if str(attempt.get("taskId", "")) == task_id:
                attempt.update({"attempt": attempt_number, "status": "submitted"})
                attempt.setdefault("submittedAt", time.time())
                break
        data["videoBlueprint"] = {
            "source": "RecreateVideoPromptV3", "workflowId": WORKFLOW_ID,
            "taskId": task_id, "status": "submitted", "requestFile": str(request_path), "file": "",
            "attempt": attempt_number, "maxAttempts": MAX_SERVER_ATTEMPTS,
            "attempts": attempts,
        }
        data.setdefault("publicMedia", {})["benchmarkVideo"] = dict(media)
        data["publicMedia"]["rawStoryboards"] = [dict(item) for item in raw_storyboards]
    generation_manifest.update(manifest_path, mutate)


def record_attempt_failure(
    manifest_path: Path,
    task_id: str,
    response: Mapping[str, Any],
) -> None:
    """Record a terminal server failure without storing metering details."""
    state = task_state(response) or "failed"
    message = ""
    for item in walk_dicts(response):
        candidate = item.get("errorMessage") or item.get("message")
        if candidate:
            message = service_privacy.public_error(candidate, "服务端视频拆解失败。")
            break

    def mutate(data: dict[str, Any]) -> None:
        current = data.get("videoBlueprint")
        if not isinstance(current, dict):
            return
        attempts = merged_attempt_history(manifest_path, current.get("attempts", []))
        current["attempts"] = attempts
        for attempt in attempts:
            if isinstance(attempt, dict) and str(attempt.get("taskId", "")) == task_id:
                attempt.update({"status": state, "failedAt": time.time()})
                if message:
                    attempt["error"] = message
                break
        current["status"] = state
        if message:
            current["lastError"] = message
    generation_manifest.update(manifest_path, mutate)


def submit_attempt(
    cli: str,
    key: str,
    manifest_path: Path,
    request_path: Path,
    payload: Mapping[str, Any],
    media: Mapping[str, Any],
    raw_storyboards: list[Mapping[str, Any]],
) -> tuple[str, int]:
    if len(load_submit_attempts(manifest_path)) >= MAX_SERVER_ATTEMPTS:
        raise AnalysisError(f"服务端拆解已达最大尝试次数{MAX_SERVER_ATTEMPTS}，禁止继续提交。")
    submitted = run_cli(
        cli,
        key,
        [
            "task", "submit", "--workflow-id", WORKFLOW_ID,
            "--input", json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ],
        600,
    )
    task_id = extract_task_id(submitted)
    persist_submit_receipt(manifest_path, task_id)
    attempt_number = len(load_submit_attempts(manifest_path))
    update_submission(
        manifest_path, task_id, request_path, media, raw_storyboards, attempt_number,
    )
    return task_id, attempt_number


def poll_task(
    cli: str,
    key: str,
    task_id: str,
    *,
    poll_interval: float,
    poll_timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + poll_timeout
    while True:
        response = run_cli(cli, key, ["task", "fetch", "--id", task_id], 300)
        state = task_state(response)
        if state in SUCCESS:
            return response
        if state in FAILED:
            service_privacy.raise_if_authorization_unavailable(response)
            return response
        if state not in PENDING:
            raise AnalysisError(f"任务返回未知状态：{state or '缺失'}")
        if time.monotonic() >= deadline:
            raise AnalysisError(f"任务{task_id}轮询超时；请用同一ID恢复。")
        time.sleep(max(1.0, poll_interval))


def update_success(
    manifest_path: Path,
    task_id: str,
    request_path: Path,
    response: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    replication_plan: Mapping[str, Any],
) -> dict[str, Any]:
    analysis_dir = manifest_path.parent / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    raw_path = analysis_dir / "recreate-video-prompt-v3-task.json"
    blueprint_path = analysis_dir / "server-video-blueprint.json"
    plan_path = analysis_dir / "server-replication-plan.json"
    sanitized_response = service_privacy.sanitize_payload(response)
    raw_path.write_text(json.dumps(sanitized_response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    blueprint_path.write_text(json.dumps({"videoBlueprint": blueprint}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan_path.write_text(json.dumps(dict(replication_plan), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    segments = replication_plan.get("segments", [])
    recommended = [
        {key: segment[key] for key in ("segmentId", "globalStart", "globalEnd", "duration")}
        for segment in segments
        if isinstance(segment, dict) and all(key in segment for key in ("segmentId", "globalStart", "globalEnd", "duration"))
    ]

    def mutate(data: dict[str, Any]) -> None:
        current = data.get("videoBlueprint")
        attempts = merged_attempt_history(
            manifest_path,
            current.get("attempts", []) if isinstance(current, dict) else [],
        )
        for attempt in attempts:
            if isinstance(attempt, dict) and str(attempt.get("taskId", "")) == task_id:
                attempt.update({"status": "succeeded", "completedAt": time.time()})
                break
        data["videoBlueprint"] = {
            "source": "RecreateVideoPromptV3", "workflowId": WORKFLOW_ID,
            "taskId": task_id, "status": "succeeded", "requestFile": str(request_path),
            "rawFile": str(raw_path), "file": str(blueprint_path),
            "attempt": len(attempts), "maxAttempts": MAX_SERVER_ATTEMPTS,
            "attempts": attempts,
        }
        data["replicationPlan"] = {
            "source": "RecreateVideoPromptV3",
            "status": "succeeded",
            "file": str(plan_path),
            "plannerVersion": str(replication_plan.get("plannerVersion", PLANNER_VERSION)),
            "schemaVersion": str(replication_plan.get("schemaVersion", "1.0")),
            "policyVersion": str(replication_plan.get("policyVersion", "")),
        }
        analysis = data.setdefault("benchmarkVideo", {}).setdefault("analysis", {})
        analysis["recommendedSegments"] = recommended
        data["timelinePlan"] = {
            "file": str(plan_path),
            "schemaVersion": str(replication_plan.get("schemaVersion", "1.0")),
            "plannerVersion": str(replication_plan.get("plannerVersion", PLANNER_VERSION)),
            "source": "server",
        }
        data["workflowStatus"] = "step3a_complete"
    generation_manifest.update(manifest_path, mutate)
    return {
        "ok": True,
        "workflowId": WORKFLOW_ID,
        "taskId": task_id,
        "file": str(blueprint_path),
        "replicationPlanFile": str(plan_path),
        "segments": len(recommended),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--benchmark")
    parser.add_argument(
        "--benchmark-url",
        help="Use an existing HTTP(S) benchmark video URL and skip the upload step.",
    )
    parser.add_argument("--resume-task-id")
    parser.add_argument("--cli")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--poll-timeout", type=float, default=1800.0)
    args = parser.parse_args()
    lock_handle = None
    try:
        cli = resolve_cli(args.cli)
        key = load_key()
        verify_key(cli, key)
        manifest_path = Path(args.manifest).expanduser().resolve()
        lock_handle = acquire_run_lock(manifest_path)
        manifest = generation_manifest.load_manifest(manifest_path)
        existing = manifest.get("videoBlueprint")
        existing_id = str(existing.get("taskId", "")).strip() if isinstance(existing, dict) and existing.get("workflowId") == WORKFLOW_ID else ""
        receipt_id = load_submit_receipt(manifest_path)
        if receipt_id:
            existing_id = receipt_id
        resume_id = str(args.resume_task_id or "").strip()
        if existing_id and not resume_id:
            raise AnalysisError(f"manifest已记录任务{existing_id}；只能用--resume-task-id继续。")
        if resume_id and existing_id and resume_id != existing_id:
            raise AnalysisError("--resume-task-id与manifest记录不一致。")
        if isinstance(existing, dict) and existing.get("workflowId") == WORKFLOW_ID and not existing_id and not resume_id:
            raise AnalysisError(
                "manifest已标记preparing_submission且任务状态未知；禁止重新上传或submit。"
                "请先确认原进程或外部任务历史，只有确认从未提交后才可人工清除预留。"
            )
        request_path = manifest_path.parent / "analysis" / "recreate-video-prompt-v3-input.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        if resume_id:
            task_id = resume_id
            try:
                payload = json.loads(request_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AnalysisError(f"恢复任务时无法读取原请求：{exc}") from None
            if not isinstance(payload, dict) or not isinstance(payload.get("benchmarkVideo"), dict):
                raise AnalysisError("恢复任务的原请求缺少benchmarkVideo。")
            media = dict(payload["benchmarkVideo"])
            raw_value = manifest.get("publicMedia", {}).get("rawStoryboards", [])
            raw_storyboards = list(raw_value) if isinstance(raw_value, list) else []
            if not load_submit_attempts(manifest_path):
                persist_submit_receipt(manifest_path, task_id)
            attempt_number = len(load_submit_attempts(manifest_path))
        else:
            if not args.benchmark and not args.benchmark_url:
                raise AnalysisError("首次提交必须提供--benchmark或--benchmark-url。")
            reserve_submission(manifest_path, request_path)
            if args.benchmark_url:
                media = media_from_url(args.benchmark_url)
            else:
                prepared = prepare_benchmark_video(args.benchmark, output_dir=manifest_path.parent / "analysis" / "upload")
                upload = run_cli(cli, key, ["upload", prepared["filePath"]], 600)
                media = extract_media(upload, "video/mp4")
            raw_storyboards: list[dict[str, Any]] = []
            if args.benchmark:
                generation_manifest.update(manifest_path, lambda data: data.setdefault("benchmarkVideo", {}).update({"file": str(Path(args.benchmark).expanduser().resolve())}))
            payload = build_input(manifest, media, raw_storyboards)
            request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            task_id, attempt_number = submit_attempt(
                cli, key, manifest_path, request_path, payload, media, raw_storyboards,
            )
        while True:
            response = poll_task(
                cli, key, task_id,
                poll_interval=args.poll_interval,
                poll_timeout=args.poll_timeout,
            )
            state = task_state(response)
            if state in SUCCESS:
                break
            record_attempt_failure(manifest_path, task_id, response)
            if attempt_number >= MAX_SERVER_ATTEMPTS:
                task_ids = [item["taskId"] for item in load_submit_attempts(manifest_path)]
                raise AnalysisError(
                    f"服务端视频拆解已自动重试{MAX_SERVER_ATTEMPTS - 1}次仍失败；"
                    f"任务ID：{', '.join(task_ids)}。"
                )
            task_id, attempt_number = submit_attempt(
                cli, key, manifest_path, request_path, payload, media, raw_storyboards,
            )
        blueprint = extract_blueprint(response)
        replication_plan = extract_replication_plan(response)
        requested = json.loads(request_path.read_text(encoding="utf-8")).get("plannerVersion", "1")
        if str(replication_plan["plannerVersion"]) != str(requested):
            raise AnalysisError("服务端Planner与本次请求不匹配；请导入配套工作流，不自动降级或重提。")
        if str(requested) == "2":
            try:
                from scripts.entity_bindings import validate_blueprint
            except ModuleNotFoundError:
                from entity_bindings import validate_blueprint
            validate_blueprint(blueprint, replication_plan.get("targetDuration"))
            try:
                from scripts.blueprint_timeline import normalize_server_plan
            except ModuleNotFoundError:
                from blueprint_timeline import normalize_server_plan
            normalize_server_plan(replication_plan)
        print(json.dumps(
            update_success(manifest_path, task_id, request_path, response, blueprint, replication_plan),
            ensure_ascii=False,
        ))
        return 0
    except service_privacy.AuthorizationUnavailableError as exc:
        parser.exit(1, f"{exc}\n")
    except (AnalysisError, CompressionError, OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"{exc}\n")
    finally:
        if lock_handle is not None:
            release_run_lock(lock_handle)


if __name__ == "__main__":
    raise SystemExit(main())
