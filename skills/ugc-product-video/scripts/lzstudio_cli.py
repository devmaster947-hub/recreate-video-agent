#!/usr/bin/env python3
"""Cross-platform adapter for the lzstudio CLI bundled with this Skill."""

from __future__ import annotations

import getpass
import json
import os
import platform
import stat
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlparse


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = Path.home() / ".ugc-product-video" / "config.json"
STUDIO_URL = "https://studio.lingzhiai.com.cn/"
PENDING_STATES = {"pending", "processing", "running", "queued", "submitted"}
SUCCESS_STATES = {"succeeded", "success", "completed", "complete"}
FAILED_STATES = {"failed", "failure", "error", "cancelled", "canceled"}
VIDEO_MODEL_IDS = {
    "google omni": "google-omni",
    "grok imagine 1 5 preview": "grok-imagine-1-5-preview",
    "seedance 2 fast": "seedance-2-fast",
    "seedance 2 mini": "seedance-2-mini",
}


class LzStudioError(RuntimeError):
    """Raised for unsupported platforms, CLI failures, and invalid responses."""


def config_path(value: str | os.PathLike[str] | None = None) -> Path:
    if value is not None:
        return Path(value).expanduser()
    override = os.environ.get("UGC_PRODUCT_VIDEO_CONFIG", "").strip()
    return Path(override).expanduser() if override else DEFAULT_CONFIG_PATH


def _read_config_key(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LzStudioError(f"无法读取 Skill API Key 配置：{exc}") from None
    key = value.get("apiKey") if isinstance(value, dict) else None
    return key.strip() if isinstance(key, str) else ""


def save_api_key(
    api_key: str, *, config: str | os.PathLike[str] | None = None
) -> None:
    """Save a user-provided key without printing or returning it."""
    key = api_key.strip() if isinstance(api_key, str) else ""
    if not key:
        raise LzStudioError("灵智工坊 API Key 不能为空。")
    path = config_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"apiKey": key}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        if os.name != "nt":
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        raise LzStudioError(f"无法保存 Skill API Key 配置：{exc}") from None


def resolve_api_key(
    *,
    api_key: str | None = None,
    config: str | os.PathLike[str] | None = None,
    prompt: Callable[[str], str] | None = None,
    save_user_input: bool = True,
) -> str:
    """Resolve LINGZHI_API_KEY, Skill config, then user input in that order."""
    environment_key = os.environ.get("LINGZHI_API_KEY", "").strip()
    if environment_key:
        return environment_key
    configured_key = _read_config_key(config_path(config))
    if configured_key:
        return configured_key
    supplied_key = api_key.strip() if isinstance(api_key, str) else ""
    if not supplied_key and prompt is not None:
        supplied_key = prompt(
            f"灵智工坊 API Key（可前往 {STUDIO_URL} 获取）: "
        ).strip()
    if not supplied_key:
        raise LzStudioError(
            "缺少灵智工坊 API Key；请设置 LINGZHI_API_KEY、Skill 配置，"
            f"或由用户输入。可前往 {STUDIO_URL} 获取。"
        )
    if save_user_input:
        save_api_key(supplied_key, config=config)
    return supplied_key


def resolve_cli(
    *,
    system: str | None = None,
    machine: str | None = None,
    skill_root: str | os.PathLike[str] | None = None,
) -> Path:
    """Select the bundled macOS arm64 or Windows x64 executable."""
    system_name = system or platform.system()
    architecture = (machine or platform.machine()).lower()
    root = Path(skill_root).resolve() if skill_root else SKILL_ROOT
    if system_name == "Darwin" and architecture in {"arm64", "aarch64"}:
        executable = root / "bin" / "macos" / "lzstudio"
    elif system_name == "Windows" and architecture in {"amd64", "x86_64"}:
        executable = root / "bin" / "windows" / "lzstudio.exe"
    else:
        raise LzStudioError(
            f"不支持的平台：{system_name} {architecture}；仅支持 macOS arm64 和 Windows x64。"
        )
    if not executable.is_file() or executable.stat().st_size <= 0:
        raise LzStudioError(f"Skill 内置 LZStudio CLI 缺失：{executable}")
    if system_name == "Darwin" and not os.access(executable, os.X_OK):
        try:
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        except OSError as exc:
            raise LzStudioError(f"无法设置内置 CLI 的执行权限：{exc}") from None
    return executable


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text


def _parse_json(raw: str) -> Any:
    text = raw.strip()
    if not text:
        raise LzStudioError("LZStudio CLI 返回了空响应。")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line.strip())
            except json.JSONDecodeError:
                continue
    raise LzStudioError("LZStudio CLI 返回的内容不是有效 JSON。")


def run_cli(
    arguments: Sequence[str],
    *,
    api_key: str | None = None,
    config: str | os.PathLike[str] | None = None,
    cli_path: str | os.PathLike[str] | None = None,
    timeout: float = 300.0,
) -> Any:
    """Execute lzstudio without a shell and parse its JSON response."""
    key = resolve_api_key(api_key=api_key, config=config, prompt=None)
    executable = Path(cli_path).expanduser().resolve() if cli_path else resolve_cli()
    if not executable.is_file():
        raise LzStudioError(f"Skill 内置 LZStudio CLI 缺失：{executable}")
    args = list(map(str, arguments))
    prefix_length = 2 if len(args) >= 2 and args[0] in {
        "sales-video-prompt", "image", "video"
    } else 1
    command = [str(executable), *args[:prefix_length], "--api-key", key, *args[prefix_length:]]
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise LzStudioError(f"LZStudio CLI 调用超时（{timeout:g} 秒）。") from None
    except OSError as exc:
        raise LzStudioError(_redact(f"无法启动 LZStudio CLI：{exc}", key)) from None
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise LzStudioError(
            _redact(f"LZStudio CLI 退出码 {completed.returncode}：{detail[:2000]}", key)
        )
    return _parse_json(completed.stdout)


def _http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _unwrap(value: Any) -> Any:
    while isinstance(value, dict):
        nested = next(
            (value[key] for key in ("data", "result") if isinstance(value.get(key), dict)),
            None,
        )
        if nested is None:
            return value
        value = nested
    return value


def _output(value: Any) -> Any:
    source = _unwrap(value)
    return source["output"] if isinstance(source, dict) and isinstance(source.get("output"), dict) else source


def _task_id(value: Any) -> str:
    source = _unwrap(value)
    identifier = source.get("id", source.get("taskId")) if isinstance(source, dict) else None
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or not str(identifier).strip():
        raise LzStudioError("submit 响应缺少 id/taskId。")
    return str(identifier).strip()


def _media(value: Any, default_mime: str) -> dict[str, Any]:
    source = _output(value)
    if isinstance(source, dict):
        for key in ("image", "video", "media"):
            if isinstance(source.get(key), (dict, str)):
                source = source[key]
                break
    if isinstance(source, str):
        source = {"url": source}
    if not isinstance(source, dict):
        raise LzStudioError("CLI 响应缺少媒体结果。")
    url = source.get("url") or source.get("downloadUrl") or source.get("outputUrl")
    if not url:
        urls = source.get("urls") or source.get("resultUrls")
        url = urls[0] if isinstance(urls, list) and urls else None
    if not _http_url(url):
        raise LzStudioError("CLI 响应缺少有效的公网媒体 URL。")
    return {
        "url": url.strip(),
        "mimeType": source.get("mimeType") or default_mime,
        "expiredAt": source.get("expiredAt") or "",
    }


def _json_object(name: str, value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise LzStudioError(f"{name} 必须是 JSON 对象。")
    return json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))


def upload_file(file_path: str | os.PathLike[str], **kwargs: Any) -> dict[str, Any]:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise LzStudioError(f"上传文件不存在或为空：{path}")
    return _media(run_cli(["upload", str(path)], timeout=600, **kwargs), "application/octet-stream")


def get_credit_balance(**kwargs: Any) -> int | float:
    """Return the current Lingzhi credits balance without exposing credentials."""
    source = _unwrap(run_cli(["account", "--credits"], timeout=300, **kwargs))
    balance = source.get("balance") if isinstance(source, dict) else None
    if (
        isinstance(balance, bool)
        or not isinstance(balance, (int, float))
        or balance < 0
    ):
        raise LzStudioError("积分余额响应缺少有效的 balance。")
    return balance


def submit_sales_video_prompt(
    user_config: Mapping[str, Any],
    product_brief: Mapping[str, Any],
    creator_brief: Mapping[str, Any] | None = None,
    creative_requirement: str | None = None,
    **kwargs: Any,
) -> str:
    arguments = [
        "sales-video-prompt", "submit",
        "--user-config", _json_object("userConfig", user_config),
        "--product-brief", _json_object("productBrief", product_brief),
    ]
    if creator_brief is not None:
        arguments.extend(["--creator-brief", _json_object("creatorBrief", creator_brief)])
    if creative_requirement is not None:
        if not isinstance(creative_requirement, str):
            raise LzStudioError("creativeRequirement 必须是字符串或 null。")
        arguments.extend(["--creative-requirement", creative_requirement])
    return _task_id(run_cli(arguments, timeout=600, **kwargs))


def fetch_sales_video_prompt(task_id: str, **kwargs: Any) -> Any:
    if not str(task_id).strip():
        raise LzStudioError("任务 id 不能为空。")
    return run_cli(
        ["sales-video-prompt", "fetch", "--id", str(task_id).strip()],
        timeout=300,
        **kwargs,
    )


def _state(value: Any) -> str:
    for source in (value, _unwrap(value)):
        if isinstance(source, dict):
            state = source.get("status") or source.get("state")
            if isinstance(state, str) and state.strip():
                return state.strip().lower()
    return ""


def _failure(value: Any) -> str:
    for source in (value, _unwrap(value), _output(value)):
        if isinstance(source, dict):
            for key in ("errorMessage", "message", "error", "detail", "failMsg"):
                message = source.get(key)
                if isinstance(message, str) and message.strip():
                    return message.strip()[:2000]
    return "任务失败。"


def poll_task(
    fetch: Callable[[str], Any],
    task_id: str,
    *,
    interval: float = 20.0,
    timeout: float = 1800.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = fetch(task_id)
        state = _state(value)
        if state in SUCCESS_STATES:
            return value
        if state in FAILED_STATES:
            raise LzStudioError(_failure(value))
        if not state:
            source = _output(value)
            if isinstance(source, dict) and any(
                key in source for key in ("videoPrompts", "segments", "url", "downloadUrl")
            ):
                return value
            raise LzStudioError("fetch 响应缺少 status/state。")
        if state not in PENDING_STATES:
            raise LzStudioError(f"未知任务状态：{state}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LzStudioError(f"任务 {task_id} 轮询超时。")
        sleep(min(interval, remaining))


def _prompt_output(value: Any) -> dict[str, Any]:
    source = _output(value)
    if not isinstance(source, dict):
        raise LzStudioError("提示词结果必须是 JSON 对象。")
    video_prompts = source.get("videoPrompts")
    nested = video_prompts if isinstance(video_prompts, dict) else {}
    segments = source.get("segments", nested.get("segments"))
    summary = source.get("summary", nested.get("summary"))
    if video_prompts is None or not isinstance(segments, list):
        raise LzStudioError("提示词结果缺少 videoPrompts 或 segments。")
    if summary is None:
        summary = ""
    if not isinstance(summary, (str, dict, list)):
        raise LzStudioError("提示词结果中的 summary 类型无效。")
    return {
        "videoPrompts": video_prompts,
        "segments": segments,
        "summary": summary,
        "success": True,
        "errorMessage": "",
    }


def poll_sales_video_prompt(task_id: str, **kwargs: Any) -> dict[str, Any]:
    interval = kwargs.pop("interval", 20.0)
    poll_timeout = kwargs.pop("poll_timeout", 1800.0)
    sleep = kwargs.pop("sleep", time.sleep)
    value = poll_task(
        lambda identifier: fetch_sales_video_prompt(identifier, **kwargs),
        task_id,
        interval=interval,
        timeout=poll_timeout,
        sleep=sleep,
    )
    return _prompt_output(value)


def generate_sales_video_prompt(
    user_config: Mapping[str, Any],
    product_brief: Mapping[str, Any],
    creator_brief: Mapping[str, Any] | None = None,
    creative_requirement: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Submit once, poll the same ID, and always return the stable public schema."""
    poll_kwargs = {
        key: kwargs.pop(key)
        for key in list(kwargs)
        if key in {"interval", "poll_timeout", "sleep"}
    }
    try:
        task_id = submit_sales_video_prompt(
            user_config,
            product_brief,
            creator_brief,
            creative_requirement,
            **kwargs,
        )
        return poll_sales_video_prompt(task_id, **kwargs, **poll_kwargs)
    except LzStudioError as exc:
        return {
            "videoPrompts": {},
            "segments": [],
            "summary": "",
            "success": False,
            "errorMessage": str(exc),
        }


def _reference_urls(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        if not _http_url(value):
            raise LzStudioError("referenceImages 只能包含 upload 返回的 HTTP(S) URL。")
        if value not in result:
            result.append(value)
    return result


def submit_image(
    prompt: str,
    *,
    reference_images: Iterable[str] | None = None,
    model: str = "gpt-image-2",
    aspect_ratio: str = "9:16",
    resolution: str = "1K",
    **kwargs: Any,
) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise LzStudioError("图片 Prompt 不能为空。")
    arguments = [
        "image", "submit", "--model", model, "--prompt", prompt.strip(),
        "--aspect-ratio", aspect_ratio, "--resolution", resolution,
    ]
    for reference in _reference_urls(reference_images):
        arguments.extend(["--reference-image-urls", reference])
    return _task_id(run_cli(arguments, timeout=600, **kwargs))


def fetch_image(task_id: str, **kwargs: Any) -> Any:
    return run_cli(["image", "fetch", "--id", str(task_id)], timeout=300, **kwargs)


def poll_image(task_id: str, **kwargs: Any) -> dict[str, Any]:
    interval = kwargs.pop("interval", 20.0)
    poll_timeout = kwargs.pop("poll_timeout", 1800.0)
    sleep = kwargs.pop("sleep", time.sleep)
    value = poll_task(
        lambda identifier: fetch_image(identifier, **kwargs),
        task_id,
        interval=interval,
        timeout=poll_timeout,
        sleep=sleep,
    )
    return _media(value, "image/png")


def submit_video(
    model: str,
    prompt: str,
    duration: int | float | str,
    *,
    reference_images: Iterable[str] | None = None,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
    **kwargs: Any,
) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise LzStudioError("视频 Prompt 不能为空。")
    normalized = " ".join(
        str(model)
        .lower()
        .replace("_", " ")
        .replace("-", " ")
        .replace(".", " ")
        .split()
    )
    model_id = VIDEO_MODEL_IDS.get(normalized, str(model).strip())
    arguments = [
        "video", "submit", "--model", model_id, "--prompt", prompt.strip(),
        "--aspect-ratio", aspect_ratio, "--resolution", resolution,
        "--duration", str(duration),
    ]
    for reference in _reference_urls(reference_images):
        arguments.extend(["--reference-image-urls", reference])
    return _task_id(run_cli(arguments, timeout=600, **kwargs))


def fetch_video(task_id: str, **kwargs: Any) -> Any:
    return run_cli(["video", "fetch", "--id", str(task_id)], timeout=300, **kwargs)


def poll_video(task_id: str, **kwargs: Any) -> dict[str, Any]:
    interval = kwargs.pop("interval", 20.0)
    poll_timeout = kwargs.pop("poll_timeout", 3600.0)
    sleep = kwargs.pop("sleep", time.sleep)
    value = poll_task(
        lambda identifier: fetch_video(identifier, **kwargs),
        task_id,
        interval=interval,
        timeout=poll_timeout,
        sleep=sleep,
    )
    return _media(value, "video/mp4")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.arguments == ["credential", "save"]:
        save_api_key(
            getpass.getpass(
                f"灵智工坊 API Key（可前往 {STUDIO_URL} 获取）: "
            )
        )
        print(json.dumps({"saved": True}, ensure_ascii=False))
    elif args.arguments:
        print(json.dumps(run_cli(args.arguments), ensure_ascii=False))
    else:
        parser.error("请提供 lzstudio 子命令。")
