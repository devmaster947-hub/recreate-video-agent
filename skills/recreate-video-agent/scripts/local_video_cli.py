#!/usr/bin/env python3
"""Video CLI discovery plus Dreamina, Xiaoyunque, and LuluLab generation helpers."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlparse
from urllib.request import Request, urlopen

SKILL_ROOT = Path(__file__).resolve().parent.parent

try:
    from scripts import service_privacy
    from scripts.model_capabilities import LOCAL_CLI_REQUIRED_MODEL_IDS, MODEL_CAPABILITIES, normalize_model, validate_generation
except ModuleNotFoundError:
    import service_privacy  # type: ignore[no-redef]
    from model_capabilities import LOCAL_CLI_REQUIRED_MODEL_IDS, MODEL_CAPABILITIES, normalize_model, validate_generation


PENDING_STATES = {
    "created", "pending", "processing", "running", "queued", "submitted", "generating", "querying",
}
SUCCESS_STATES = {"succeeded", "success", "completed", "complete"}
FAILED_STATES = {"failed", "failure", "fail", "error", "cancelled", "canceled", "timeout", "timed_out"}
VIDEO_PROVIDERS = {"auto", "dreamina_cli", "xiaoyunque_cli", "lululab_cli"}
GENERATION_PROVIDER_ORDER = ("libtv_cli", "xiaoyunque_cli", "dreamina_cli")
LULULAB_IMAGE_MODEL_ID = "gpt-image-2-5-sunburst"
LULULAB_IMAGE_WORKFLOW_ID = "ImageGenV2"
LULULAB_VIDEO_WORKFLOW_ID = "VideoGenV2"
LULULAB_IMAGE_RESOLUTION = "1K"

DREAMINA_VIDEO_MODEL_IDS = {
    key: str(value["dreaminaId"])
    for key, value in MODEL_CAPABILITIES.items()
    if value.get("dreaminaId")
}
XIAOYUNQUE_VIDEO_MODEL_IDS = {
    key: str(value["xiaoyunqueId"])
    for key, value in MODEL_CAPABILITIES.items()
    if value.get("xiaoyunqueId")
}


class LocalVideoCliError(RuntimeError):
    pass


class TaskFailedError(LocalVideoCliError):
    def __init__(self, task_id: str, state: str, message: str, *, response: Any = None):
        super().__init__(message)
        self.task_id = str(task_id)
        self.state = str(state)
        self.response = response


def _parse_json(raw: str, provider: str) -> Any:
    text = raw.strip()
    if not text:
        raise LocalVideoCliError(f"{provider} CLI 返回空响应。")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line.strip())
            except json.JSONDecodeError:
                continue
    raise LocalVideoCliError(f"{provider} CLI 返回内容不是有效 JSON。")


def _resolve_executable(names: Sequence[str], *, configured: str = "", cli_path: str | os.PathLike[str] | None = None) -> Path:
    if cli_path:
        candidate = Path(cli_path).expanduser().resolve()
    else:
        discovered = configured or next((found for name in names if (found := shutil.which(name))), "")
        if discovered:
            candidate = Path(discovered).expanduser().resolve()
        else:
            candidate = (Path.home() / ".local" / "bin" / names[0]).resolve()
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        raise LocalVideoCliError(f"未发现可执行 CLI：{names[0]}")
    if platform.system() != "Windows" and not os.access(candidate, os.X_OK):
        raise LocalVideoCliError(f"CLI 不可执行：{candidate}")
    return candidate


def resolve_dreamina_cli(*, cli_path: str | os.PathLike[str] | None = None) -> Path:
    names = ("dreamina.exe", "dreamina") if platform.system() == "Windows" else ("dreamina",)
    return _resolve_executable(names, configured=os.environ.get("DREAMINA_CLI", "").strip(), cli_path=cli_path)


def resolve_xiaoyunque_cli(*, cli_path: str | os.PathLike[str] | None = None) -> Path:
    names = (
        ("xiaoyunque.exe", "xiao-yunque.exe", "xiaoyunque-cli.exe")
        if platform.system() == "Windows"
        else ("xiaoyunque", "xiao-yunque", "xiaoyunque-cli")
    )
    return _resolve_executable(names, configured=os.environ.get("XIAOYUNQUE_CLI", "").strip(), cli_path=cli_path)


def resolve_libtv_cli(*, cli_path: str | os.PathLike[str] | None = None) -> Path:
    names = ("libtv.exe", "libtv") if platform.system() == "Windows" else ("libtv",)
    configured = os.environ.get("LIBTV_CLI", "").strip()
    discovered = configured or next((found for name in names if (found := shutil.which(name))), "")
    if not cli_path and not discovered:
        bundled = Path.home() / ".libtv" / ("libtv.exe" if platform.system() == "Windows" else "libtv")
        if bundled.is_file():
            cli_path = bundled
    return _resolve_executable(names, configured=configured, cli_path=cli_path)


def resolve_lululab_cli(*, cli_path: str | os.PathLike[str] | None = None) -> Path:
    if cli_path:
        candidate = Path(cli_path).expanduser().resolve()
    else:
        configured = os.environ.get("LULULAB_CLI", "").strip()
        if configured:
            candidate = Path(configured).expanduser().resolve()
        else:
            candidate = SKILL_ROOT / "scripts" / "lululab_cli.mjs"
    if not candidate.is_file() or candidate.suffix != ".mjs" or candidate.stat().st_size <= 0:
        raise LocalVideoCliError("未发现 Node.js LuluLab CLI：scripts/lululab_cli.mjs")
    if not (os.environ.get("LULULAB_NODE") or shutil.which("node")):
        raise LocalVideoCliError("未发现 Node.js 20+。")
    return candidate.resolve()


def libtv_cli_available(*, cli_path: str | os.PathLike[str] | None = None) -> bool:
    """Return true only when the executable exists and its help command starts successfully."""
    try:
        executable = resolve_libtv_cli(cli_path=cli_path)
        completed = subprocess.run(
            [str(executable), "--help"],
            check=False, capture_output=True, text=True, timeout=30,
        )
    except (LocalVideoCliError, OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


LULULAB_GENERATION_UNAVAILABLE = "未发现支持 task submit/fetch 的 LuluLab CLI。"


def lululab_cli_available(*, cli_path: str | os.PathLike[str] | None = None) -> bool:
    try:
        executable = resolve_lululab_cli(cli_path=cli_path)
        node = os.environ.get("LULULAB_NODE") or shutil.which("node")
        for command, required in (("submit", ("--workflow-id", "--input")), ("fetch", ("--id",))):
            result = subprocess.run([str(node), str(executable), "task", command, "--help"], capture_output=True, text=True, timeout=30)
            text = result.stdout + result.stderr
            if result.returncode != 0 or not all(flag in text for flag in required):
                return False
        return True
    except (LocalVideoCliError, OSError, subprocess.SubprocessError):
        return False


def _run(executable: Path, arguments: Sequence[str], *, provider: str, timeout: float = 600.0) -> Any:
    try:
        completed = subprocess.run(
            [str(executable), *map(str, arguments)],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise LocalVideoCliError(f"{provider} CLI 调用超时（{timeout:g} 秒）。") from None
    except OSError as exc:
        raise LocalVideoCliError(f"无法启动 {provider} CLI：{exc}") from None
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        service_privacy.raise_if_authorization_unavailable(detail)
        raise LocalVideoCliError(
            f"{provider} CLI 退出码 {completed.returncode}："
            f"{service_privacy.public_error(detail[:1000], '未知错误')}"
        )
    parsed = _parse_json(completed.stdout, provider)
    service_privacy.raise_if_authorization_unavailable(parsed)
    return parsed


def load_lululab_key() -> str:
    for name in ("LULULAB_API_KEY",):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    config = Path.home() / ".recreate-video-lululab" / "config.json"
    if config.is_file():
        try:
            value = json.loads(config.read_text(encoding="utf-8")).get("apiKey", "")
        except (OSError, json.JSONDecodeError, AttributeError):
            value = ""
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise LocalVideoCliError("本机未配置LuluLab API Key。")


def run_lululab_cli(
    arguments: Sequence[str],
    *,
    cli_path: str | os.PathLike[str] | None = None,
    timeout: float = 600.0,
) -> Any:
    """Use only the documented LuluLab task/upload/user interface."""
    try:
        from scripts import server_video_analysis as analysis
    except ModuleNotFoundError:
        import server_video_analysis as analysis
    values = list(map(str, arguments))
    if not values or values[0] not in {"task", "upload", "user"}:
        raise LocalVideoCliError(LULULAB_GENERATION_UNAVAILABLE)
    try:
        executable = resolve_lululab_cli(cli_path=cli_path)
        return analysis.run_cli(str(executable), load_lululab_key(), values, timeout)
    except analysis.AnalysisError as exc:
        raise LocalVideoCliError(str(exc)) from None


def image_workflow_id() -> str:
    """Use the ImageGenV2 contract extracted from the original bundled CLI."""
    return os.environ.get("LULULAB_IMAGE_WORKFLOW_ID", "").strip() or LULULAB_IMAGE_WORKFLOW_ID


def video_workflow_id() -> str:
    """Use the VideoGenV2 contract extracted from the original bundled CLI."""
    return os.environ.get("LULULAB_VIDEO_WORKFLOW_ID", "").strip() or LULULAB_VIDEO_WORKFLOW_ID


def upload_reference_images(files: Iterable[Path]) -> list[dict[str, Any]]:
    return [media(run_lululab_cli(["upload", str(path)]),
                  "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png")
            for path in files]


def run_dreamina_cli(arguments: Sequence[str], *, cli_path: str | os.PathLike[str] | None = None, timeout: float = 600.0) -> Any:
    return _run(resolve_dreamina_cli(cli_path=cli_path), arguments, provider="即梦", timeout=timeout)


def run_xiaoyunque_cli(arguments: Sequence[str], *, cli_path: str | os.PathLike[str] | None = None, timeout: float = 600.0) -> Any:
    return _run(resolve_xiaoyunque_cli(cli_path=cli_path), arguments, provider="小云雀", timeout=timeout)


def _model_id(model: str) -> str:
    return normalize_model(str(model))


def dreamina_cli_supports_video_model(model: str, *, cli_path: str | os.PathLike[str] | None = None) -> bool:
    model_id = _model_id(model)
    provider_id = DREAMINA_VIDEO_MODEL_IDS.get(model_id)
    if not provider_id:
        return False
    try:
        executable = resolve_dreamina_cli(cli_path=cli_path)
        completed = subprocess.run(
            [str(executable), "multimodal2video", "--help"],
            check=False, capture_output=True, text=True, timeout=30,
        )
    except (LocalVideoCliError, OSError, subprocess.SubprocessError):
        return False
    text = f"{completed.stdout}\n{completed.stderr}"
    required = ("--image", "--prompt", "--duration", "--ratio", "--video_resolution", "--model_version")
    return completed.returncode == 0 and provider_id in text and all(token in text for token in required)


def xiaoyunque_cli_supports_video_model(model: str, *, cli_path: str | os.PathLike[str] | None = None) -> bool:
    model_id = _model_id(model)
    provider_id = XIAOYUNQUE_VIDEO_MODEL_IDS.get(model_id)
    if not provider_id:
        return False
    try:
        executable = resolve_xiaoyunque_cli(cli_path=cli_path)
        submit_help = subprocess.run(
            [str(executable), "multimodal2video", "--help"],
            check=False, capture_output=True, text=True, timeout=30,
        )
        query_help = subprocess.run(
            [str(executable), "query_result", "--help"],
            check=False, capture_output=True, text=True, timeout=30,
        )
    except (LocalVideoCliError, OSError, subprocess.SubprocessError):
        return False
    submit_text = f"{submit_help.stdout}\n{submit_help.stderr}"
    query_text = f"{query_help.stdout}\n{query_help.stderr}"
    required = ("--image", "--prompt", "--duration", "--ratio", "--video_resolution", "--model_version")
    return (
        submit_help.returncode == 0
        and query_help.returncode == 0
        and provider_id in submit_text
        and all(token in submit_text for token in required)
        and "--submit_id" in query_text
    )


def detect_video_providers(model: str | None = None) -> dict[str, bool]:
    try:
        resolve_dreamina_cli()
        dreamina = True
    except LocalVideoCliError:
        dreamina = False
    try:
        resolve_xiaoyunque_cli()
        xiaoyunque = True
    except LocalVideoCliError:
        xiaoyunque = False
    lululab = lululab_cli_available()
    if model:
        if dreamina and not dreamina_cli_supports_video_model(model):
            dreamina = False
        if xiaoyunque and not xiaoyunque_cli_supports_video_model(model):
            xiaoyunque = False
    return {"dreamina_cli": dreamina, "xiaoyunque_cli": xiaoyunque, "lululab_cli": lululab}


def detect_generation_providers(model: str | None = None) -> dict[str, bool]:
    """Detect every supported CLI in the fixed selection order."""
    local = detect_video_providers(model)
    return {
        "libtv_cli": libtv_cli_available(),
        "xiaoyunque_cli": local["xiaoyunque_cli"],
        "dreamina_cli": local["dreamina_cli"],
        "lululab_cli": local["lululab_cli"],
    }


def resolve_generation_provider(*, availability: Mapping[str, bool] | None = None) -> str | None:
    channels = dict(availability or detect_generation_providers())
    return next((provider for provider in GENERATION_PROVIDER_ORDER if channels.get(provider, False)), None)


def resolve_video_provider(video_provider: str = "auto", *, availability: Mapping[str, bool] | None = None) -> str | None:
    provider = str(video_provider).strip().lower()
    if provider not in VIDEO_PROVIDERS:
        raise LocalVideoCliError("videoProvider 必须是 auto、dreamina_cli、xiaoyunque_cli 或 lululab_cli。")
    channels = dict(availability or detect_video_providers())
    if provider != "auto":
        if not channels.get(provider, False):
            raise LocalVideoCliError(f"视频生成渠道不可用：{provider}")
        return provider
    if channels.get("xiaoyunque_cli"):
        return "xiaoyunque_cli"
    if channels.get("dreamina_cli"):
        return "dreamina_cli"
    if channels.get("lululab_cli"):
        return "lululab_cli"
    return None


def _provider_model_id(provider: str, model: str) -> str:
    model_id = _model_id(model)
    if provider == "lululab_cli":
        return model_id
    mapping = DREAMINA_VIDEO_MODEL_IDS if provider == "dreamina_cli" else XIAOYUNQUE_VIDEO_MODEL_IDS
    provider_id = mapping.get(model_id)
    if not provider_id:
        raise LocalVideoCliError(f"{provider} 不支持视频模型：{model_id}。")
    required = LOCAL_CLI_REQUIRED_MODEL_IDS.get(model_id)
    if required is not None and provider_id != required:
        raise LocalVideoCliError(f"{model_id} 必须映射为 {required}。")
    return provider_id


def _reference_files(values: Iterable[str | os.PathLike[str]] | None, maximum: int) -> list[Path]:
    files: list[Path] = []
    for value in values or []:
        path = Path(value).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise LocalVideoCliError(f"视频参考图不存在或为空：{path}")
        if path not in files:
            files.append(path)
    if len(files) > maximum:
        raise LocalVideoCliError(f"视频参考图数量 {len(files)} 超过模型上限 {maximum}。")
    return files


def submit_video(
    provider: str,
    model: str,
    prompt: str,
    duration: int | float | str,
    *,
    reference_files: Iterable[str | os.PathLike[str]] | None = None,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
) -> str:
    if provider not in {"dreamina_cli", "xiaoyunque_cli", "lululab_cli"}:
        raise LocalVideoCliError(f"未知本地视频 provider：{provider}")
    if not isinstance(prompt, str) or not prompt.strip():
        raise LocalVideoCliError("视频 Prompt 不能为空。")
    checked = validate_generation(_model_id(model), duration, resolution)
    files = _reference_files(reference_files, int(checked.get("maxImages", 9)))
    model_id = _provider_model_id(provider, model)
    if provider == "lululab_cli":
        payload = {"model": model_id, "prompt": prompt.strip(),
                   "duration": int(checked["duration"]), "aspectRatio": aspect_ratio,
                   "resolution": resolution, "referenceImages": upload_reference_images(files)}
        return _task_id(run_lululab_cli([
            "task", "submit", "--workflow-id", video_workflow_id(),
            "--input", json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ]))
    command = "multimodal2video" if files else "text2video"
    arguments = [
        command,
        "--prompt", prompt.strip(),
        "--duration", str(int(checked["duration"])),
        "--ratio", aspect_ratio,
        "--video_resolution", resolution,
        "--model_version", model_id,
    ]
    for path in files:
        arguments.extend(["--image", str(path)])
    value = run_dreamina_cli(arguments) if provider == "dreamina_cli" else run_xiaoyunque_cli(arguments)
    return _task_id(value)


def fetch_video(provider: str, task_id: str) -> Any:
    if not str(task_id).strip():
        raise LocalVideoCliError("任务 id 不能为空。")
    if provider == "lululab_cli":
        return run_lululab_cli(["task", "fetch", "--id", str(task_id).strip()], timeout=300)
    arguments = ["query_result", "--submit_id", str(task_id).strip()]
    return run_dreamina_cli(arguments, timeout=300) if provider == "dreamina_cli" else run_xiaoyunque_cli(arguments, timeout=300)


def submit_lululab_image(
    prompt: str,
    *,
    reference_files: Iterable[str | os.PathLike[str]] | None = None,
    aspect_ratio: str = "9:16",
) -> str:
    workflow = image_workflow_id()
    if not isinstance(prompt, str) or not prompt.strip():
        raise LocalVideoCliError("图片 Prompt 不能为空。")
    files = _reference_files(reference_files, 9)
    references = upload_reference_images(files)
    payload = {
        "model": LULULAB_IMAGE_MODEL_ID,
        "prompt": prompt.strip(),
        "resolution": LULULAB_IMAGE_RESOLUTION,
        "aspectRatio": aspect_ratio,
        "referenceImages": references,
    }
    return _task_id(run_lululab_cli([
        "task", "submit", "--workflow-id", workflow,
        "--input", json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    ]))


def fetch_lululab_image(task_id: str) -> Any:
    if not str(task_id).strip():
        raise LocalVideoCliError("任务 id 不能为空。")
    return run_lululab_cli(["task", "fetch", "--id", str(task_id).strip()], timeout=300)


def _unwrap(value: Any) -> Any:
    while isinstance(value, dict):
        nested = next((value[key] for key in ("data", "result") if isinstance(value.get(key), dict)), None)
        if nested is None:
            break
        value = nested
    return value


def _output(value: Any) -> Any:
    source = _unwrap(value)
    if isinstance(source, dict) and isinstance(source.get("output"), dict):
        return source["output"]
    return source


def _task_id(value: Any) -> str:
    source = _unwrap(value)
    if not isinstance(source, dict):
        raise LocalVideoCliError("submit 响应必须是 JSON 对象。")
    identifier = source.get("id", source.get("taskId", source.get("submit_id")))
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or not str(identifier).strip():
        raise LocalVideoCliError("submit 响应缺少 id。")
    return str(identifier).strip()


def _http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def media(value: Any, default_mime: str = "video/mp4") -> dict[str, Any]:
    source = _output(value)
    if isinstance(source, dict):
        for key in ("video", "image", "media"):
            if isinstance(source.get(key), (dict, str)):
                source = source[key]
                break
    if isinstance(source, str):
        source = {"url": source}
    if not isinstance(source, dict):
        raise LocalVideoCliError("fetch 响应缺少媒体结果。")
    url = (
        source.get("url") or source.get("downloadUrl") or source.get("outputUrl")
        or source.get("video_url") or source.get("media_url") or source.get("result_url")
    )
    if not url:
        urls = source.get("urls") or source.get("resultUrls")
        if isinstance(urls, list) and urls:
            url = urls[0]
    if not _http_url(url):
        queue = [_output(value)]
        while queue and not _http_url(url):
            current = queue.pop(0)
            if isinstance(current, dict):
                for key, item in current.items():
                    if key in {"url", "downloadUrl", "outputUrl", "video_url", "media_url", "result_url"} and _http_url(item):
                        source, url = current, item
                        break
                    if isinstance(item, (dict, list)):
                        queue.append(item)
            elif isinstance(current, list):
                queue.extend(item for item in current if isinstance(item, (dict, list)))
    if not _http_url(url):
        raise LocalVideoCliError("fetch 响应缺少有效媒体 URL。")
    return {"url": str(url).strip(), "mimeType": source.get("mimeType") or default_mime, "expiredAt": source.get("expiredAt") or source.get("expiresAt") or ""}


def _state(value: Any) -> str:
    queue = [value]
    while queue:
        candidate = queue.pop(0)
        if isinstance(candidate, dict):
            status = candidate.get("status") or candidate.get("state") or candidate.get("gen_status") or candidate.get("task_status")
            if isinstance(status, str) and status.strip():
                return status.strip().lower()
            queue.extend(item for item in candidate.values() if isinstance(item, (dict, list)))
        elif isinstance(candidate, list):
            queue.extend(item for item in candidate if isinstance(item, (dict, list)))
    return ""


def _failure(value: Any) -> str:
    for source in (value, _unwrap(value), _output(value)):
        if isinstance(source, dict):
            for key in ("message", "error", "errorMessage", "detail", "failMsg", "fail_reason"):
                item = source.get(key)
                if isinstance(item, str) and item.strip():
                    return item.strip()[:1000]
    return "任务失败。"


def poll_task(fetch: Callable[[str], Any], task_id: str, *, interval: float = 5.0, timeout: float = 3600.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = fetch(task_id)
        state = _state(value)
        if state in SUCCESS_STATES:
            return value
        if state in FAILED_STATES:
            service_privacy.raise_if_authorization_unavailable(value)
            raise TaskFailedError(
                task_id,
                state,
                service_privacy.public_error(_failure(value), "任务失败。"),
                response=service_privacy.sanitize_payload(value),
            )
        if not state:
            try:
                media(value)
                return value
            except LocalVideoCliError:
                raise LocalVideoCliError("fetch 响应缺少 status/state。") from None
        if state not in PENDING_STATES:
            raise LocalVideoCliError(f"未知任务状态：{state}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LocalVideoCliError(f"任务 {task_id} 轮询超时。")
        time.sleep(min(interval, remaining))


def download_media(media_value: Mapping[str, Any] | str, output_path: str | os.PathLike[str], *, timeout: float = 600.0) -> Path:
    url = media_value if isinstance(media_value, str) else media_value.get("url")
    if not _http_url(url):
        raise LocalVideoCliError("下载结果缺少有效媒体 URL。")
    requested = Path(output_path).expanduser().resolve()
    requested.parent.mkdir(parents=True, exist_ok=True)
    destination = requested
    version = 2
    while destination.exists():
        destination = requested.with_name(f"{requested.stem}-v{version}{requested.suffix}")
        version += 1
    temporary = destination.with_name(destination.name + ".part")
    request = Request(str(url), headers={"User-Agent": "recreate-video-agent/local"})
    try:
        with urlopen(request, timeout=timeout) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        if temporary.stat().st_size <= 0:
            raise LocalVideoCliError("下载的媒体文件为空。")
        temporary.replace(destination)
    except LocalVideoCliError:
        temporary.unlink(missing_ok=True)
        raise
    except (OSError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        raise LocalVideoCliError(f"无法下载媒体结果：{exc}") from None
    return destination


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    detect = sub.add_parser("detect")
    detect.add_argument("--model", default="")
    args = parser.parse_args()
    if args.command == "detect":
        print(json.dumps(detect_video_providers(args.model or None), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
