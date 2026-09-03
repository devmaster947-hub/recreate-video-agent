#!/usr/bin/env python3
"""Cross-platform wrappers for LZStudio and the official Dreamina CLI."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    from scripts.model_capabilities import MODEL_CAPABILITIES, normalize_model, validate_generation
except ModuleNotFoundError:
    from model_capabilities import MODEL_CAPABILITIES, normalize_model, validate_generation


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = Path.home() / ".recreate-video" / "config.json"
MEDIA_CACHE_VERSION = 1
MEDIA_CACHE_LIMIT = 256
MIN_PUBLIC_URL_REMAINING_SECONDS = 600
_CONFIG_THREAD_LOCK = threading.RLock()
PENDING_STATES = {
    "created", "pending", "processing", "running", "queued", "submitted", "generating",
    "querying",
}
SUCCESS_STATES = {"succeeded", "success", "completed", "complete"}
FAILED_STATES = {"failed", "failure", "fail", "error", "cancelled", "canceled"}
VIDEO_PROVIDERS = {"auto", "official_cli", "xiaoyunque_cli", "lingzhi_cli"}
VIDEO_MODEL_IDS = {
    "grok imagine 1.5 preview": "grok-imagine-1-5-preview",
    "grok imagine 1 5 preview": "grok-imagine-1-5-preview",
    "minimax h3": "minimax-h3",
    "seedance 2": "seedance-2",
    "seedance 2.5": "seedance-2-5",
    "seedance 2 5": "seedance-2-5",
    "seedance 2 fast": "seedance-2-fast",
    "seedance 2 fast vip": "seedance-2-fast-vip",
    "seedance 2 mini": "seedance-2-mini",
    "seedance 2 vip": "seedance-2-vip",
}
OFFICIAL_VIDEO_MODEL_IDS = {
    key: str(value["officialId"])
    for key, value in MODEL_CAPABILITIES.items()
    if value.get("officialId")
}
XIAOYUNQUE_VIDEO_MODEL_IDS = {
    key: str(value["xiaoyunqueId"])
    for key, value in MODEL_CAPABILITIES.items()
    if value.get("xiaoyunqueId")
}
OFFICIAL_VIDEO_PROVISIONAL_MODEL_IDS = {
    key for key, value in MODEL_CAPABILITIES.items() if value.get("provisionalOfficial")
}
RECREATE_USER_CONFIG_OMIT = {
    "videoStyle",
    "videoProvider",
    "targetLanguage",
    "resolution",
    "aspectRatio",
}
BENCHMARK_PRODUCT_NAME = "跟原视频产品一致"
IMAGE_MODEL_ID = "gpt-image-2"
MAX_UPLOAD_BYTES = 20_000_000
UPLOAD_IMAGE_SUFFIXES = {".avif", ".jpeg", ".jpg", ".png", ".webp"}


class LzStudioError(RuntimeError):
    """Raise for unsupported platforms, CLI failures, or invalid responses."""


class TaskFailedError(LzStudioError):
    """Raise only when a provider task reaches an explicit terminal failure state."""

    def __init__(self, task_id: str, state: str, message: str):
        super().__init__(message)
        self.task_id = str(task_id)
        self.state = str(state)


def _config_path(value: str | os.PathLike[str] | None = None) -> Path:
    if value is not None:
        return Path(value).expanduser()
    override = os.environ.get("RECREATE_VIDEO_CONFIG", "").strip()
    return Path(override).expanduser() if override else DEFAULT_CONFIG_PATH


def _read_config(path: Path, *, tolerate_cache_errors: bool = True) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LzStudioError(f"无法读取 API Key 配置：{exc}") from None
    if not isinstance(value, dict):
        raise LzStudioError("API Key 配置必须是 JSON 对象。")
    if tolerate_cache_errors and not isinstance(value.get("mediaCache", {}), dict):
        value["mediaCache"] = {}
    return value


def _write_config(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    if os.name != "nt":
        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    temporary.replace(path)
    if os.name != "nt":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


@contextmanager
def _locked_config(path: Path, *, timeout: float = 900.0) -> Iterable[None]:
    """Serialize cache updates across threads and processes without a persistent lock file."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + timeout
    descriptor: int | None = None
    with _CONFIG_THREAD_LOCK:
        while descriptor is None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    stat.S_IRUSR | stat.S_IWUSR,
                )
                os.write(descriptor, str(os.getpid()).encode("ascii", errors="ignore"))
            except FileExistsError:
                try:
                    stale = time.time() - lock_path.stat().st_mtime > timeout
                except OSError:
                    stale = False
                if stale:
                    try:
                        lock_path.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise LzStudioError("等待素材缓存锁超时。") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                lock_path.unlink()
            except OSError:
                pass


def _api_key_config_candidates(
    config_path: str | os.PathLike[str] | None = None,
) -> list[tuple[Path, str]]:
    """Return config locations in priority order without exposing key material."""
    if config_path is not None:
        return [(Path(config_path).expanduser(), "函数参数 config_path")]

    override = os.environ.get("RECREATE_VIDEO_CONFIG", "").strip()
    if override:
        return [(Path(override).expanduser(), "环境变量 RECREATE_VIDEO_CONFIG")]

    candidates: list[tuple[Path, str]] = [
        (
            Path.home() / ".recreate-video" / "config.json",
            "Path.home()/.recreate-video/config.json",
        )
    ]
    user_profile = os.environ.get("USERPROFILE", "").strip()
    if user_profile:
        candidates.append(
            (
                Path(user_profile) / ".recreate-video" / "config.json",
                "USERPROFILE/.recreate-video/config.json",
            )
        )
    home_drive = os.environ.get("HOMEDRIVE", "").strip()
    home_path = os.environ.get("HOMEPATH", "").strip()
    if home_drive and home_path:
        candidates.append(
            (
                Path(home_drive + home_path) / ".recreate-video" / "config.json",
                "HOMEDRIVE + HOMEPATH/.recreate-video/config.json",
            )
        )

    unique_candidates: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for path, source in candidates:
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized not in seen:
            seen.add(normalized)
            unique_candidates.append((path, source))
    return unique_candidates


def _log_api_key_source(source: str) -> None:
    """Log only where the key came from, never the key itself."""
    print(f"[LZStudio] API Key 来源：{source}（内容已脱敏）", file=sys.stderr)


def load_api_key(
    *,
    api_key: str | None = None,
    config_path: str | os.PathLike[str] | None = None,
    prompt: Callable[[str], str] = getpass.getpass,
) -> str:
    """Resolve the key by runtime priority, persisting only prompted input."""
    parameter_key = api_key.strip() if isinstance(api_key, str) else ""
    if parameter_key:
        _log_api_key_source("函数参数 api_key")
        return parameter_key

    environment_key = os.environ.get("RECREATE_VIDEO_API_KEY", "").strip()
    if environment_key:
        _log_api_key_source("环境变量 RECREATE_VIDEO_API_KEY")
        return environment_key

    for path, source in _api_key_config_candidates(config_path):
        if not path.is_file():
            continue
        config = _read_config(path)
        key = config.get("apiKey") if isinstance(config, dict) else None
        if isinstance(key, str) and key.strip():
            _log_api_key_source(source)
            return key.strip()

    path = _config_path(config_path)
    key = prompt("灵智工坊 API Key: ").strip()
    saved_key = save_api_key(key, config_path=path)
    _log_api_key_source("用户输入（已保存到配置文件）")
    return saved_key


def save_api_key(
    api_key: str,
    *,
    config_path: str | os.PathLike[str] | None = None,
) -> str:
    """Persist one user-provided key without printing or returning it to CLI output."""
    key = api_key.strip() if isinstance(api_key, str) else ""
    if not key:
        raise LzStudioError("灵智工坊 API Key 不能为空。")
    path = _config_path(config_path)
    try:
        with _locked_config(path):
            config = _read_config(path) if path.exists() else {}
            config["apiKey"] = key
            _write_config(path, config)
    except (OSError, LzStudioError) as exc:
        raise LzStudioError(f"无法保存 API Key 配置：{exc}") from None
    return key


def query_api_key(
    *,
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Return the currently effective Lingzhi API Key without prompting."""
    environment_key = os.environ.get("RECREATE_VIDEO_API_KEY", "").strip()
    if environment_key:
        return {
            "apiKey": environment_key,
            "source": "环境变量 RECREATE_VIDEO_API_KEY",
        }

    for path, source in _api_key_config_candidates(config_path):
        if not path.is_file():
            continue
        config = _read_config(path)
        key = config.get("apiKey") if isinstance(config, dict) else None
        if isinstance(key, str) and key.strip():
            return {"apiKey": key.strip(), "source": source}

    raise LzStudioError("尚未配置灵智工坊 API Key。")


def resolve_cli(
    *,
    system: str | None = None,
    machine: str | None = None,
    skill_root: str | os.PathLike[str] | None = None,
    install_dir: str | os.PathLike[str] | None = None,
    home: str | os.PathLike[str] | None = None,
    persist_path: bool = True,
    verify: bool = True,
) -> Path:
    """Install into the user's application directory, configure PATH, and return it."""
    try:
        try:
            from scripts.install_lzstudio import ensure_installed
        except ModuleNotFoundError:
            from install_lzstudio import ensure_installed  # type: ignore[no-redef]
        result = ensure_installed(
            system=system,
            machine=machine,
            skill_root=skill_root,
            install_dir=install_dir,
            home=home,
            persist_path=persist_path,
            verify=verify,
        )
    except (OSError, RuntimeError) as exc:
        raise LzStudioError(str(exc)) from None
    return Path(str(result["installedPath"])).resolve()


def resolve_official_cli(
    *,
    cli_path: str | os.PathLike[str] | None = None,
    system: str | None = None,
) -> Path:
    """Locate the official Dreamina CLI without installing or modifying it."""
    system_name = system or platform.system()
    if cli_path is not None:
        candidate = Path(cli_path).expanduser().resolve()
    else:
        executable_names = (
            ("dreamina.exe", "dreamina")
            if system_name == "Windows"
            else ("dreamina",)
        )
        discovered = next(
            (found for name in executable_names if (found := shutil.which(name))),
            None,
        )
        if discovered:
            candidate = Path(discovered).resolve()
        else:
            local_bin = Path.home() / ".local" / "bin"
            local_candidates = [local_bin / name for name in executable_names]
            candidate = next(
                (path.resolve() for path in local_candidates if path.is_file()),
                local_candidates[0].resolve(),
            )
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        raise LzStudioError("未发现可执行的官方 Seedance CLI（dreamina）。")
    if system_name != "Windows" and not os.access(candidate, os.X_OK):
        raise LzStudioError("未发现可执行的官方 Seedance CLI（dreamina）。")
    return candidate


def detect_video_providers(
    *,
    official_cli_path: str | os.PathLike[str] | None = None,
    system: str | None = None,
    machine: str | None = None,
    skill_root: str | os.PathLike[str] | None = None,
    install_dir: str | os.PathLike[str] | None = None,
    home: str | os.PathLike[str] | None = None,
    persist_path: bool = True,
    verify: bool = True,
) -> dict[str, bool]:
    """Detect installed video channels without login, network, or credit usage."""
    try:
        resolve_official_cli(cli_path=official_cli_path, system=system)
        official_available = True
    except LzStudioError:
        official_available = False
    try:
        resolve_cli(
            system=system,
            machine=machine,
            skill_root=skill_root,
            install_dir=install_dir,
            home=home,
            persist_path=persist_path,
            verify=verify,
        )
        lingzhi_available = True
    except LzStudioError:
        lingzhi_available = False
    return {
        "official_cli": official_available,
        "xiaoyunque_cli": False,
        "lingzhi_cli": lingzhi_available,
    }


def resolve_video_provider(
    video_provider: str = "auto",
    *,
    availability: Mapping[str, bool] | None = None,
) -> str:
    """Resolve auto before submission, preferring the official Seedance CLI."""
    provider = str(video_provider).strip().lower()
    if provider not in VIDEO_PROVIDERS:
        raise LzStudioError(
            "videoProvider 必须是 auto、official_cli、xiaoyunque_cli 或 lingzhi_cli。"
        )
    channels = dict(availability or detect_video_providers())
    channels["xiaoyunque_cli"] = False
    if provider == "xiaoyunque_cli":
        raise LzStudioError("小云雀 CLI 适配器尚未配置，当前禁止提交。")
    if provider != "auto":
        if not channels.get(provider, False):
            raise LzStudioError(f"视频生成渠道不可用：{provider}")
        return provider
    if channels.get("official_cli", False):
        return "official_cli"
    if channels.get("xiaoyunque_cli", False):
        return "xiaoyunque_cli"
    if channels.get("lingzhi_cli", False):
        return "lingzhi_cli"
    raise LzStudioError("未检测到可用的视频生成渠道。")


def official_cli_supports_video_model(
    model: str,
    *,
    cli_path: str | os.PathLike[str] | None = None,
) -> bool:
    """Check the exact provider model ID locally without submitting a task."""
    model_id = _video_model_id(str(model))
    official_id = OFFICIAL_VIDEO_MODEL_IDS.get(model_id)
    if official_id is None:
        return False
    try:
        executable = resolve_official_cli(cli_path=cli_path)
        completed = subprocess.run(
            [str(executable), "multimodal2video", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (LzStudioError, OSError, subprocess.SubprocessError):
        return False
    help_text = f"{completed.stdout}\n{completed.stderr}"
    return completed.returncode == 0 and official_id in help_text


def video_provider_availability(
    model: str,
    *,
    availability: Mapping[str, bool] | None = None,
) -> dict[str, bool]:
    """Return provider availability filtered by the selected model."""
    channels = dict(availability or detect_video_providers())
    model_id = _video_model_id(str(model))
    channels["xiaoyunque_cli"] = False
    if model_id not in OFFICIAL_VIDEO_MODEL_IDS:
        channels["official_cli"] = False
    elif channels.get("official_cli", False) and not official_cli_supports_video_model(model):
        channels["official_cli"] = False
    return channels


def _redact(value: str, secret: str) -> str:
    return value.replace(secret, "[REDACTED]") if secret else value


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


def _video_model_id(model: str) -> str:
    value = str(model).strip()
    normalized = " ".join(value.lower().replace("_", " ").replace("-", " ").split())
    return VIDEO_MODEL_IDS.get(normalized, normalize_model(value))


def run_cli(
    arguments: Sequence[str],
    *,
    input_json: Mapping[str, Any] | None = None,
    api_key: str | None = None,
    config_path: str | os.PathLike[str] | None = None,
    cli_path: str | os.PathLike[str] | None = None,
    timeout: float = 300.0,
) -> Any:
    """Run the selected executable without a shell and parse its JSON output."""
    normalized_arguments = list(map(str, arguments))
    if normalized_arguments[:2] == ["image", "submit"]:
        try:
            model_index = normalized_arguments.index("--model")
            image_model = normalized_arguments[model_index + 1]
        except (ValueError, IndexError):
            raise LzStudioError(
                f"图片生成必须显式使用固定模型 {IMAGE_MODEL_ID}。"
            ) from None
        if image_model != IMAGE_MODEL_ID:
            raise LzStudioError(
                f"图片生成模型固定为 {IMAGE_MODEL_ID}，不接受修改。"
            )
    executable = Path(cli_path).expanduser().resolve() if cli_path else resolve_cli()
    if not executable.is_file():
        raise LzStudioError(f"已安装的 LZStudio CLI 缺失：{executable}")
    key = load_api_key(api_key=api_key, config_path=config_path)
    if not key:
        raise LzStudioError("灵智工坊 API Key 不能为空。")
    prefix_length = (
        2
        if len(normalized_arguments) >= 2
        and normalized_arguments[0] in {"image", "video"}
        else 1
    )
    command = [
        str(executable),
        *normalized_arguments[:prefix_length],
        "--api-key",
        key,
        *normalized_arguments[prefix_length:],
    ]
    payload = None
    if input_json is not None:
        payload = json.dumps(input_json, ensure_ascii=False, separators=(",", ":"))
    try:
        completed = subprocess.run(
            command,
            input=payload,
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
            _redact(f"LZStudio CLI 退出码 {completed.returncode}：{detail[:1000]}", key)
        )
    return _parse_json(completed.stdout)


def run_official_cli(
    arguments: Sequence[str],
    *,
    cli_path: str | os.PathLike[str] | None = None,
    timeout: float = 600.0,
) -> Any:
    """Run the official Dreamina CLI without a shell and parse JSON output."""
    executable = resolve_official_cli(cli_path=cli_path)
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
        raise LzStudioError(f"官方 Seedance CLI 调用超时（{timeout:g} 秒）。") from None
    except OSError as exc:
        raise LzStudioError(f"无法启动官方 Seedance CLI：{exc}") from None
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise LzStudioError(
            f"官方 Seedance CLI 退出码 {completed.returncode}：{detail[:1000]}"
        )
    return _parse_json(completed.stdout)


def _http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _parse_expired_at(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("expiredAt 必须是 ISO 8601 字符串或空值。")
    normalized = value.strip()
    normalized = re.sub(
        r"(\.\d{6})\d+(?=(?:Z|[+-]\d\d:\d\d)$)",
        r"\1",
        normalized,
    )
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError("expiredAt 必须是有效的 ISO 8601 时间。") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _public_media_usable(
    media: Any,
    *,
    at: datetime | None = None,
    min_remaining_seconds: int = MIN_PUBLIC_URL_REMAINING_SECONDS,
) -> bool:
    if not isinstance(media, dict) or not _http_url(media.get("url")):
        return False
    try:
        expires = _parse_expired_at(media.get("expiredAt"))
    except ValueError:
        return False
    if expires is None:
        return True
    current = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return expires - current >= timedelta(seconds=min_remaining_seconds)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _prune_media_cache(
    values: Mapping[str, Any],
    *,
    at: datetime | None = None,
) -> dict[str, Any]:
    current = at or datetime.now(timezone.utc)
    retained = [
        (key, dict(value))
        for key, value in values.items()
        if isinstance(value, dict) and _public_media_usable(value, at=current)
    ]
    retained.sort(
        key=lambda item: str(item[1].get("lastUsedAt", "")),
        reverse=True,
    )
    return dict(retained[:MEDIA_CACHE_LIMIT])


def _resolve_cached_upload(
    file_path: str | os.PathLike[str],
    *,
    kind: str,
    benchmark: bool = False,
    config_path: str | os.PathLike[str] | None = None,
    uploader: Callable[..., dict[str, Any]] | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Reuse one valid global upload or upload it once while holding the cache lock."""
    if kind not in {"benchmark", "storyboard", "product", "creator"}:
        raise LzStudioError(f"不支持的素材缓存类型：{kind}")
    path = Path(file_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise LzStudioError(f"上传文件不存在或为空：{path}")
    stat_result = path.stat()
    fingerprint = _sha256_file(path)
    cache_key = f"{kind}:{fingerprint}"
    config_file = _config_path(config_path)
    upload = uploader or upload_file
    current = at or datetime.now(timezone.utc)

    with _locked_config(config_file):
        config = _read_config(config_file) if config_file.exists() else {}
        cache = config.get("mediaCache")
        if not isinstance(cache, dict):
            cache = {}
        cached = cache.get(cache_key)
        if _public_media_usable(cached, at=current):
            cached = dict(cached)
            cached["filePath"] = str(path)
            cached["fileSize"] = stat_result.st_size
            cached["modifiedNs"] = stat_result.st_mtime_ns
            cached["lastUsedAt"] = current.isoformat()
            cache[cache_key] = cached
            config["mediaCacheVersion"] = MEDIA_CACHE_VERSION
            config["mediaCache"] = _prune_media_cache(cache, at=current)
            _write_config(config_file, config)
            return {
                "url": cached["url"],
                "mimeType": cached.get("mimeType", ""),
                "expiredAt": cached.get("expiredAt", ""),
                "cacheHit": True,
                "fingerprint": fingerprint,
            }

        media = upload(path, benchmark=benchmark)
        if not _public_media_usable(media, at=current):
            raise LzStudioError("上传返回的公网媒体 URL 无效或即将过期。")
        entry = {
            "kind": kind,
            "sha256": fingerprint,
            "filePath": str(path),
            "fileSize": stat_result.st_size,
            "modifiedNs": stat_result.st_mtime_ns,
            "url": media["url"],
            "mimeType": media.get("mimeType", ""),
            "expiredAt": media.get("expiredAt", ""),
            "lastUsedAt": current.isoformat(),
        }
        cache[cache_key] = entry
        config["mediaCacheVersion"] = MEDIA_CACHE_VERSION
        config["mediaCache"] = _prune_media_cache(cache, at=current)
        _write_config(config_file, config)
        return {
            "url": entry["url"],
            "mimeType": entry["mimeType"],
            "expiredAt": entry["expiredAt"],
            "cacheHit": False,
            "fingerprint": fingerprint,
        }


def _unwrap(value: Any) -> Any:
    while isinstance(value, dict):
        nested = next((value[key] for key in ("data", "result") if isinstance(value.get(key), dict)), None)
        if nested is None:
            break
        value = nested
    return value


def _output(value: Any) -> Any:
    """Unwrap common task envelopes without discarding public result fields."""
    source = _unwrap(value)
    if isinstance(source, dict) and isinstance(source.get("output"), dict):
        return source["output"]
    return source


def _task_id(value: Any) -> str:
    source = _unwrap(value)
    if not isinstance(source, dict):
        raise LzStudioError("submit 响应必须是 JSON 对象。")
    identifier = source.get("id", source.get("taskId", source.get("submit_id")))
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or not str(identifier).strip():
        raise LzStudioError("submit 响应缺少 id。")
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
        raise LzStudioError("fetch 响应缺少媒体结果。")
    url = (
        source.get("url")
        or source.get("downloadUrl")
        or source.get("outputUrl")
        or source.get("video_url")
        or source.get("media_url")
        or source.get("result_url")
    )
    if not url:
        urls = source.get("urls") or source.get("resultUrls")
        if isinstance(urls, list) and urls:
            url = urls[0]
    if not _http_url(url):
        queue = [value]
        while queue and not _http_url(url):
            current = queue.pop(0)
            if isinstance(current, dict):
                for key, item in current.items():
                    if key in {
                        "url", "downloadUrl", "outputUrl", "video_url", "media_url", "result_url"
                    } and _http_url(item):
                        source, url = current, item
                        break
                    if isinstance(item, (dict, list)):
                        queue.append(item)
            elif isinstance(current, list):
                queue.extend(item for item in current if isinstance(item, (dict, list)))
    if not _http_url(url):
        raise LzStudioError("fetch 响应缺少有效的公网媒体 URL。")
    return {
        "url": url.strip(),
        "mimeType": source.get("mimeType") or default_mime,
        "expiredAt": source.get("expiredAt") or "",
    }


def _credits(value: Any) -> int | float:
    """Read provider-reported credits without estimating missing values."""
    keys = {
        "credits",
        "creditsConsumed",
        "credits_consumed",
        "consume_credit",
        "consumed_credit",
        "cost_credit",
        "credit_count",
    }
    queue = [value]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            for key, item in current.items():
                if key in keys and not isinstance(item, bool) and isinstance(item, (int, float)):
                    return item
                if isinstance(item, (dict, list)):
                    queue.append(item)
        elif isinstance(current, list):
            queue.extend(item for item in current if isinstance(item, (dict, list)))
    return 0


def get_remaining_credits(
    provider: str = "lingzhi_cli",
    *,
    lingzhi_kwargs: Mapping[str, Any] | None = None,
    official_kwargs: Mapping[str, Any] | None = None,
) -> int | float:
    """Read the provider-reported post-task credit balance without estimating it."""
    normalized = str(provider).strip().lower()
    if normalized == "lingzhi_cli":
        value = run_cli(["account"], timeout=300, **dict(lingzhi_kwargs or {}))
        balances = value.get("balances") if isinstance(value, dict) else None
        for item in balances if isinstance(balances, list) else []:
            if not isinstance(item, dict):
                continue
            ledger_type = str(item.get("ledgerType", "")).strip().lower()
            balance = item.get("balance")
            if (
                ledger_type == "credits"
                and not isinstance(balance, bool)
                and isinstance(balance, (int, float))
            ):
                return balance
        raise LzStudioError("灵智工坊账户响应缺少 Credits 余额。")
    if normalized == "official_cli":
        value = run_official_cli(
            ["user_credit"], timeout=300, **dict(official_kwargs or {})
        )
        balance = value.get("total_credit") if isinstance(value, dict) else None
        if not isinstance(balance, bool) and isinstance(balance, (int, float)):
            return balance
        raise LzStudioError("官方 Seedance 账户响应缺少 total_credit 余额。")
    raise LzStudioError("剩余积分查询只支持 lingzhi_cli 或 official_cli。")


def _references(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        if not _http_url(value):
            raise LzStudioError("referenceImages 只能包含 upload 返回的 HTTP(S) URL。")
        if value not in result:
            result.append(value)
    return result


def prepare_benchmark_video_for_upload(
    file_path: str | os.PathLike[str],
    *,
    output_dir: str | os.PathLike[str] | None = None,
    max_bytes: int = MAX_UPLOAD_BYTES,
    ffmpeg_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Prepare a benchmark video without overwriting the user's original file."""
    try:
        from scripts.compress_benchmark_video import (
            CompressionError,
            prepare_benchmark_video,
        )
    except ModuleNotFoundError:
        from compress_benchmark_video import (  # type: ignore[no-redef]
            CompressionError,
            prepare_benchmark_video,
        )
    try:
        return prepare_benchmark_video(
            file_path,
            output_dir=output_dir,
            max_bytes=max_bytes,
            ffmpeg_path=ffmpeg_path,
        )
    except CompressionError as exc:
        raise LzStudioError(str(exc)) from None


def prepare_image_for_upload(
    file_path: str | os.PathLike[str],
    *,
    output_dir: str | os.PathLike[str] | None = None,
    max_bytes: int = MAX_UPLOAD_BYTES,
    ffmpeg_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Prepare an upload image without overwriting the user's original file."""
    try:
        from scripts.compress_benchmark_video import CompressionError, prepare_upload_image
    except ModuleNotFoundError:
        from compress_benchmark_video import (  # type: ignore[no-redef]
            CompressionError,
            prepare_upload_image,
        )
    try:
        return prepare_upload_image(
            file_path,
            output_dir=output_dir,
            max_bytes=max_bytes,
            ffmpeg_path=ffmpeg_path,
        )
    except CompressionError as exc:
        raise LzStudioError(str(exc)) from None


def upload_file(
    file_path: str | os.PathLike[str],
    *,
    benchmark: bool = False,
    compression_dir: str | os.PathLike[str] | None = None,
    max_upload_bytes: int = MAX_UPLOAD_BYTES,
    max_benchmark_bytes: int | None = None,
    ffmpeg_path: str | os.PathLike[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise LzStudioError(f"上传文件不存在或为空：{path}")
    limit = max_benchmark_bytes if benchmark and max_benchmark_bytes is not None else max_upload_bytes
    if benchmark:
        prepared = prepare_benchmark_video_for_upload(
            path,
            output_dir=compression_dir,
            max_bytes=limit,
            ffmpeg_path=ffmpeg_path,
        )
        path = Path(prepared["filePath"]).resolve()
    elif path.stat().st_size > limit and path.suffix.lower() in UPLOAD_IMAGE_SUFFIXES:
        prepared = prepare_image_for_upload(
            path,
            output_dir=compression_dir,
            max_bytes=limit,
            ffmpeg_path=ffmpeg_path,
        )
        path = Path(prepared["filePath"]).resolve()
    if not path.is_file() or path.stat().st_size > limit:
        raise LzStudioError(f"上传素材压缩结果无效或仍超过 {limit} 字节。")
    return _media(run_cli(["upload", str(path)], timeout=600, **kwargs), "application/octet-stream")


def download_media(
    media: Mapping[str, Any] | str,
    output_path: str | os.PathLike[str],
    *,
    timeout: float = 600.0,
) -> Path:
    """Download one public CLI result to a non-overwriting local artifact path."""
    url = media if isinstance(media, str) else media.get("url")
    if not _http_url(url):
        raise LzStudioError("下载结果缺少有效的公网媒体 URL。")
    requested = Path(output_path).expanduser().resolve()
    requested.parent.mkdir(parents=True, exist_ok=True)
    destination = requested
    version = 2
    while destination.exists():
        destination = requested.with_name(f"{requested.stem}-v{version}{requested.suffix}")
        version += 1
    temporary = destination.with_name(destination.name + ".part")
    request = Request(str(url), headers={"User-Agent": "recreate-video-agent/1"})
    try:
        with urlopen(request, timeout=timeout) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        if temporary.stat().st_size <= 0:
            raise LzStudioError("下载的媒体文件为空。")
        temporary.replace(destination)
    except LzStudioError:
        temporary.unlink(missing_ok=True)
        raise
    except (OSError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        raise LzStudioError(f"无法下载媒体结果：{exc}") from None
    return destination


def _state(value: Any) -> str:
    queue = [value]
    while queue:
        candidate = queue.pop(0)
        if isinstance(candidate, dict):
            status = (
                candidate.get("status")
                or candidate.get("state")
                or candidate.get("gen_status")
                or candidate.get("task_status")
            )
            if isinstance(status, str) and status.strip():
                return status.strip().lower()
            queue.extend(
                item for item in candidate.values() if isinstance(item, (dict, list))
            )
        elif isinstance(candidate, list):
            queue.extend(item for item in candidate if isinstance(item, (dict, list)))
    return ""


def _failure(value: Any) -> str:
    candidates = [value, _unwrap(value), _output(value)]
    for source in candidates:
        if isinstance(source, dict):
            for key in ("message", "error", "errorMessage", "detail", "failMsg", "fail_reason"):
                item = source.get(key)
                if isinstance(item, str) and item.strip():
                    return item.strip()[:1000]
    return "任务失败。"


def poll_task(
    fetch: Callable[[str], Any],
    task_id: str,
    *,
    interval: float = 20.0,
    timeout: float = 1800.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Poll one submitted ID until Succeeded or Failed."""
    deadline = time.monotonic() + timeout
    while True:
        value = fetch(task_id)
        state = _state(value)
        if state in SUCCESS_STATES:
            return value
        if state in FAILED_STATES:
            raise TaskFailedError(task_id, state, _failure(value))
        if not state:
            source = _unwrap(value)
            if isinstance(source, dict) and any(
                key in source for key in ("url", "downloadUrl", "videoPrompts", "creatorReferencePlan")
            ):
                return value
            raise LzStudioError("fetch 响应缺少 status/state。")
        if state not in PENDING_STATES:
            raise LzStudioError(f"未知任务状态：{state}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LzStudioError(f"任务 {task_id} 轮询超时。")
        sleep(min(interval, remaining))


def submit_image(
    prompt: str,
    *,
    reference_images: Iterable[str] | None = None,
    aspect_ratio: str = "9:16",
    resolution: str = "1K",
    **kwargs: Any,
) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise LzStudioError("图片 Prompt 不能为空。")
    if "model" in kwargs:
        raise TypeError("submit_image() 的图片模型固定为 gpt-image-2，不接受 model 参数。")
    arguments = [
        "image", "submit", "--model", IMAGE_MODEL_ID, "--prompt", prompt.strip(),
        "--aspect-ratio", aspect_ratio, "--resolution", resolution,
    ]
    for reference in _references(reference_images):
        arguments.extend(["--reference-image-urls", reference])
    return _task_id(run_cli(arguments, timeout=600, **kwargs))


def fetch_image(task_id: str, **kwargs: Any) -> Any:
    return run_cli(["image", "fetch", "--id", str(task_id)], timeout=300, **kwargs)


def poll_image(task_id: str, **kwargs: Any) -> dict[str, Any]:
    interval = kwargs.pop("interval", 20.0)
    poll_timeout = kwargs.pop("poll_timeout", 1800.0)
    sleep = kwargs.pop("sleep", time.sleep)
    value = poll_task(
        lambda identifier: fetch_image(identifier, **kwargs), task_id,
        interval=interval, timeout=poll_timeout, sleep=sleep,
    )
    return _media(value, "image/png")


def generate_creator_image(
    prompt: str,
    appears_in_segments: Iterable[int | str],
    *,
    reference_images: Iterable[str] | None = None,
    aspect_ratio: str = "9:16",
    resolution: str = "1K",
    on_submit: Callable[[int, str], None] | None = None,
    submit_kwargs: Mapping[str, Any] | None = None,
    poll_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate one Creator image, retrying one terminal failure for cross-Segment use."""
    segments = list(dict.fromkeys(appears_in_segments))
    max_attempts = 2 if len(segments) >= 2 else 1
    task_ids: list[str] = []
    last_error: TaskFailedError | None = None
    submit_options = dict(submit_kwargs or {})
    poll_options = dict(poll_kwargs or {})
    references = list(reference_images or [])

    for attempt in range(1, max_attempts + 1):
        task_id = submit_image(
            prompt,
            reference_images=references,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            **submit_options,
        )
        task_ids.append(task_id)
        if on_submit is not None:
            on_submit(attempt, task_id)
        try:
            media = poll_image(task_id, **poll_options)
        except TaskFailedError as exc:
            last_error = exc
            continue
        return {
            "status": "success",
            "taskIds": task_ids,
            "media": media,
            "manualRequired": False,
            "affectedSegments": segments,
        }

    return {
        "status": "failed",
        "taskIds": task_ids,
        "error": str(last_error) if last_error is not None else "任务失败。",
        "manualRequired": max_attempts == 2,
        "affectedSegments": segments,
    }


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
    model_id = _video_model_id(str(model))
    if not model_id:
        raise LzStudioError("视频 model 不能为空。")
    try:
        validate_generation(model_id, duration, resolution)
    except ValueError as exc:
        raise LzStudioError(str(exc)) from None
    arguments = [
        "video", "submit", "--model", model_id, "--prompt", prompt.strip(),
        "--aspect-ratio", aspect_ratio, "--resolution", resolution,
        "--duration", str(duration),
    ]
    for reference in _references(reference_images):
        arguments.extend(["--reference-image-urls", reference])
    return _task_id(run_cli(arguments, timeout=600, **kwargs))


def fetch_video(task_id: str, **kwargs: Any) -> Any:
    return run_cli(["video", "fetch", "--id", str(task_id)], timeout=300, **kwargs)


def poll_video(task_id: str, **kwargs: Any) -> dict[str, Any]:
    interval = kwargs.pop("interval", 20.0)
    poll_timeout = kwargs.pop("poll_timeout", 3600.0)
    sleep = kwargs.pop("sleep", time.sleep)
    value = poll_task(
        lambda identifier: fetch_video(identifier, **kwargs), task_id,
        interval=interval, timeout=poll_timeout, sleep=sleep,
    )
    return _media(value, "video/mp4")


def _official_video_model_id(model: str) -> str:
    model_id = _video_model_id(model)
    official_id = OFFICIAL_VIDEO_MODEL_IDS.get(model_id)
    if official_id is None:
        raise LzStudioError(
            f"官方 Seedance CLI 不支持视频模型：{model_id}；该模型请使用灵智工坊 CLI。"
        )
    return official_id


def _xiaoyunque_video_model_id(model: str) -> str:
    model_id = _video_model_id(model)
    provider_id = XIAOYUNQUE_VIDEO_MODEL_IDS.get(model_id)
    if provider_id is None:
        raise LzStudioError(f"小云雀 CLI 不支持视频模型：{model_id}。")
    return provider_id


def submit_official_video(
    model: str,
    prompt: str,
    duration: int | float | str,
    *,
    reference_files: Iterable[str | os.PathLike[str]] | None = None,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
    **kwargs: Any,
) -> str:
    """Submit one Seedance 2 task through the official Dreamina CLI."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise LzStudioError("视频 Prompt 不能为空。")
    normalized_model = _video_model_id(str(model))
    try:
        checked = validate_generation(normalized_model, duration, resolution)
    except (TypeError, ValueError) as exc:
        raise LzStudioError(str(exc)) from None
    duration_value = int(checked["duration"])
    model_id = _official_video_model_id(normalized_model)
    files: list[Path] = []
    for value in reference_files or []:
        path = Path(value).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise LzStudioError(f"官方 Seedance 参考图不存在或为空：{path}")
        if path not in files:
            files.append(path)
    maximum_images = int(checked.get("maxImages", 9))
    if len(files) > maximum_images:
        raise LzStudioError(f"官方 Seedance multimodal2video 最多支持 {maximum_images} 张参考图。")
    command = "multimodal2video" if files else "text2video"
    arguments = [
        command,
        "--prompt", prompt.strip(),
        "--duration", str(duration_value),
        "--ratio", aspect_ratio,
        "--video_resolution", resolution,
        "--model_version", model_id,
    ]
    for path in files:
        arguments.extend(["--image", str(path)])
    return _task_id(run_official_cli(arguments, timeout=600, **kwargs))


def fetch_official_video(task_id: str, **kwargs: Any) -> Any:
    if not str(task_id).strip():
        raise LzStudioError("任务 id 不能为空。")
    return run_official_cli(
        ["query_result", "--submit_id", str(task_id).strip()],
        timeout=300,
        **kwargs,
    )


def generate_video(
    model: str,
    prompt: str,
    duration: int | float | str,
    *,
    video_provider: str = "auto",
    reference_images: Iterable[str] | None = None,
    reference_files: Iterable[str | os.PathLike[str]] | None = None,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
    availability: Mapping[str, bool] | None = None,
    on_submit: Callable[[str], None] | None = None,
    lingzhi_kwargs: Mapping[str, Any] | None = None,
    official_kwargs: Mapping[str, Any] | None = None,
    poll_interval: float = 20.0,
    poll_timeout: float = 3600.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Generate through either provider and return one stable public shape."""
    try:
        channels = video_provider_availability(model, availability=availability)
        provider = resolve_video_provider(video_provider, availability=channels)
        if provider == "xiaoyunque_cli":
            raise LzStudioError("小云雀 CLI 适配器尚未配置，当前禁止提交。")
        if provider == "official_cli":
            official_options = dict(official_kwargs or {})
            task_id = submit_official_video(
                model,
                prompt,
                duration,
                reference_files=reference_files,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                **official_options,
            )
            if on_submit is not None:
                on_submit(task_id)
            value = poll_task(
                lambda identifier: fetch_official_video(identifier, **official_options),
                task_id,
                interval=poll_interval,
                timeout=poll_timeout,
                sleep=sleep,
            )
        else:
            lingzhi_options = dict(lingzhi_kwargs or {})
            task_id = submit_video(
                model,
                prompt,
                duration,
                reference_images=reference_images,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                **lingzhi_options,
            )
            if on_submit is not None:
                on_submit(task_id)
            value = poll_task(
                lambda identifier: fetch_video(identifier, **lingzhi_options),
                task_id,
                interval=poll_interval,
                timeout=poll_timeout,
                sleep=sleep,
            )
        return {
            "video": _media(value, "video/mp4"),
            "credits": _credits(value),
            "errorMessage": "",
        }
    except (LzStudioError, OSError, ValueError) as exc:
        return {"video": None, "credits": 0, "errorMessage": str(exc)}


def build_final_output(
    *,
    videos: Iterable[Mapping[str, Any]],
    creator_images: Iterable[Mapping[str, Any]],
    video_prompts: Mapping[str, Any],
    creator_reference_plan: Mapping[str, Any],
    credits_consumed: int | float = 0,
) -> dict[str, Any]:
    """Build the stable public result without exposing provider envelopes."""
    if isinstance(credits_consumed, bool) or not isinstance(credits_consumed, (int, float)):
        raise LzStudioError("creditsConsumed 必须是数字。")
    normalized_videos: list[dict[str, Any]] = []
    for item in videos:
        if not isinstance(item, Mapping) or "segmentId" not in item:
            raise LzStudioError("每个视频结果必须包含 segmentId。")
        normalized_videos.append(
            {"segmentId": item["segmentId"], **_media(dict(item), "video/mp4")}
        )
    normalized_creators: list[dict[str, Any]] = []
    for item in creator_images:
        if not isinstance(item, Mapping) or "creatorId" not in item:
            raise LzStudioError("每个达人图结果必须包含 creatorId。")
        normalized_creators.append(
            {"creatorId": item["creatorId"], **_media(dict(item), "image/png")}
        )
    return {
        "success": True,
        "result": {
            "videos": normalized_videos,
            "creatorImages": normalized_creators,
            "videoPrompts": dict(video_prompts),
            "creatorReferencePlan": dict(creator_reference_plan),
        },
        "creditsConsumed": credits_consumed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="Arguments passed to lzstudio.")
    args = parser.parse_args()
    if not args.arguments:
        parser.error("请提供 lzstudio 子命令。")
    if args.arguments == ["video-provider", "detect"]:
        print(json.dumps(detect_video_providers(), ensure_ascii=False))
        return 0
    if args.arguments == ["cli", "ensure-installed"]:
        try:
            try:
                from scripts.install_lzstudio import ensure_installed
            except ModuleNotFoundError:
                from install_lzstudio import ensure_installed  # type: ignore[no-redef]
            installed = ensure_installed()
        except (OSError, RuntimeError) as exc:
            parser.exit(1, f"{exc}\n")
        print(json.dumps(installed, ensure_ascii=False))
        return 0
    if args.arguments == ["credential", "save"]:
        try:
            save_api_key(getpass.getpass("灵智工坊 API Key: "))
        except LzStudioError as exc:
            parser.exit(1, f"{exc}\n")
        print(json.dumps({"saved": True}, ensure_ascii=False))
        return 0
    if args.arguments == ["credential", "show"]:
        try:
            credential = query_api_key()
        except LzStudioError as exc:
            parser.exit(1, f"{exc}\n")
        print(json.dumps(credential, ensure_ascii=False))
        return 0
    try:
        result = run_cli(args.arguments)
    except LzStudioError as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
