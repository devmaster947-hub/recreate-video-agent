"""Local, fail-closed preprocessing of the exact benchmark analysis window."""
from __future__ import annotations

from contextlib import contextmanager
import http.client
import ipaddress
import json
import math
import queue
import threading
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

try:
    from scripts.compress_benchmark_video import CompressionError, DEFAULT_MAX_BYTES, prepare_benchmark_video, resolve_ffmpeg
except ModuleNotFoundError:
    from compress_benchmark_video import CompressionError, DEFAULT_MAX_BYTES, prepare_benchmark_video, resolve_ffmpeg

DOWNLOAD_MAX_BYTES = 512_000_000
DOWNLOAD_TIMEOUT = 120.0
MAX_REDIRECTS = 3


def analysis_window(config: dict, source_duration: float) -> float:
    """source is full media; custom is the prefix defined by benchmark_analysis."""
    if not math.isfinite(source_duration) or not 0 < source_duration <= 360:
        raise CompressionError('原视频真实时长必须为 0～360 秒。')
    mode = config.get('durationMode', 'source')
    if mode == 'source':
        target = config.get('duration')
        if target is not None and (isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target) or abs(target - source_duration) > 0.5):
            raise CompressionError('source 模式目标时长与原始媒体差异超过0.5秒，请重新执行技术分析。')
        return source_duration
    if mode != 'custom':
        raise CompressionError('durationMode 必须是 source 或 custom。')
    requested = config.get('requestedDuration')
    target = config.get('duration')
    if requested is None:
        requested = target
    if isinstance(requested, bool) or not isinstance(requested, int) or requested <= 0:
        raise CompressionError('custom 模式必须提供正整数分析时长。')
    if target is not None and target != requested:
        raise CompressionError('custom 模式的 duration 与 requestedDuration 不一致。')
    if requested > source_duration:
        raise CompressionError('自定义复刻时长超过原视频真实时长；不得静默延长。')
    return float(requested)


def probe_video(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        raise CompressionError('视频不存在或为空。')
    ffprobe = shutil.which('ffprobe') or str(Path.home() / '.local/bin/ffprobe')
    try:
        result = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, text=True, timeout=60)
        data = json.loads(result.stdout)
        video = next(s for s in data['streams'] if s.get('codec_type') == 'video')
        duration = float(video.get('duration') or data['format']['duration'])
        rate = video.get('avg_frame_rate', '0/1').split('/')
        fps = float(rate[0]) / float(rate[1]) if float(rate[1]) else 0
        if result.returncode or not math.isfinite(duration) or duration <= 0 or video.get('width', 0) <= 0 or video.get('height', 0) <= 0:
            raise ValueError('invalid media')
        audio = next((s for s in data['streams'] if s.get('codec_type') == 'audio'), None)
        audio_duration = float(audio.get('duration') or data['format']['duration']) if audio else None
        if audio and (not math.isfinite(audio_duration) or audio_duration <= 0):
            raise ValueError('invalid audio')
        rotation = float(video.get('tags', {}).get('rotate', 0))
        for side in video.get('side_data_list', []):
            rotation = float(side.get('rotation', rotation))
        sar = video.get('sample_aspect_ratio', '1:1').split(':')
        aspect = video['width'] / video['height']
        if len(sar) == 2 and float(sar[0]) > 0 and float(sar[1]) > 0:
            aspect *= float(sar[0]) / float(sar[1])
        if int(abs(rotation)) % 180 == 90:
            aspect = 1 / aspect
        return {'duration': duration, 'fps': fps, 'audioDuration': audio_duration, 'aspect': aspect, 'container': data['format'].get('format_name', ''), 'videoCodec': video.get('codec_name'), 'audioCodec': audio.get('codec_name') if audio else None}
    except (OSError, ValueError, TypeError, KeyError, StopIteration, subprocess.TimeoutExpired) as exc:
        raise CompressionError(f'ffprobe 无法解析有效视频/音频流：{path.name}') from exc


def public_endpoint(url: str, timeout: float = 15.0) -> tuple:
    """Resolve once; the connection uses this public IP, preventing DNS rebinding."""
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise CompressionError('视频直链必须为无凭据的 HTTP/HTTPS URL。')
    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        resolved = queue.Queue(maxsize=1)
        def resolve():
            try:
                resolved.put(socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM))
            except OSError as exc:
                resolved.put(exc)
        threading.Thread(target=resolve, daemon=True).start()
        try:
            addresses = resolved.get(timeout=min(15.0, timeout))
        except queue.Empty:
            raise OSError('DNS timeout') from None
        if isinstance(addresses, OSError):
            raise addresses
        if not addresses:
            raise ValueError('empty DNS')
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global or (getattr(ip, 'ipv4_mapped', None) and not ip.ipv4_mapped.is_global):
                raise ValueError('non-public address')
    except (ValueError, OSError) as exc:
        raise CompressionError('视频直链禁止本地、内网、云元数据地址或无法解析的主机。') from exc
    return parsed, addresses[0][4][0], port


def open_download(url: str, timeout: float):
    deadline = time.monotonic() + timeout
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise CompressionError('视频直链连接超时。')
        return value
    parsed, ip, port = public_endpoint(url, timeout=remaining())
    connection = http.client.HTTPConnection(parsed.hostname, port, timeout=remaining())
    sock = socket.create_connection((ip, port), timeout=remaining())
    connection.sock = sock
    try:
        if parsed.scheme == 'https':
            sock.settimeout(remaining())
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
            connection.sock = sock
        sock.settimeout(remaining())
        connection.request('GET', (parsed.path or '/') + ('?' + parsed.query if parsed.query else ''), headers={'Accept-Encoding': 'identity'})
        sock.settimeout(remaining())
        return connection, connection.getresponse()
    except Exception:
        connection.close()
        raise


def download_video(url: str, destination: Path) -> None:
    started = time.monotonic()
    try:
        for hop in range(MAX_REDIRECTS + 1):
            remaining = DOWNLOAD_TIMEOUT - (time.monotonic() - started)
            if remaining <= 0:
                raise CompressionError('视频直链下载超时。')
            connection, response = open_download(url, min(15.0, remaining))
            try:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader('Location')
                    if not location or hop == MAX_REDIRECTS:
                        raise CompressionError('视频直链重定向超过限制或缺少目标。')
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise CompressionError(f'视频直链下载失败：HTTP {response.status}。')
                length = response.getheader('Content-Length')
                if length and (int(length) < 0 or int(length) > DOWNLOAD_MAX_BYTES):
                    raise CompressionError('视频直链文件超过 512 MB 下载限制。')
                size = 0
                with destination.open('wb') as output:
                    while True:
                        remaining = DOWNLOAD_TIMEOUT - (time.monotonic() - started)
                        if remaining <= 0:
                            raise CompressionError('视频直链下载超时。')
                        if connection.sock:
                            connection.sock.settimeout(min(15.0, remaining))
                        chunk = response.read1(64 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > DOWNLOAD_MAX_BYTES:
                            raise CompressionError('视频直链文件超过 512 MB 下载限制。')
                        output.write(chunk)
                if size == 0 or (length and size != int(length)):
                    raise CompressionError('视频直链下载为空或不完整。')
                return
            finally:
                response.close()
                connection.close()
    except (OSError, ValueError, http.client.HTTPException) as exc:
        destination.unlink(missing_ok=True)
        raise CompressionError('视频直链下载失败。') from exc
    except CompressionError:
        destination.unlink(missing_ok=True)
        raise


def transcode_prefix(source: Path, output: Path, duration: float) -> None:
    ffmpeg = resolve_ffmpeg()
    command = [str(ffmpeg), '-v', 'error', '-nostdin', '-y', '-i', str(source), '-t', str(duration), '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(output)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
        if result.returncode:
            raise CompressionError('FFmpeg 精确裁剪失败；禁止提交原视频。')
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CompressionError('FFmpeg 精确裁剪失败或超时；禁止提交原视频。') from exc


def validate_video(path: Path, source: dict, expected: float, *, max_bytes: int | None = None, decode: bool = False, transcoded: bool = False) -> dict:
    info = probe_video(path)
    tolerance = max(0.1, 2 / source['fps']) if source['fps'] > 0 else 0.1
    if abs(info['duration'] - expected) > tolerance:
        raise CompressionError('预处理视频真实时长不符合分析区间。')
    if abs(info['aspect'] / source['aspect'] - 1) > 0.02:
        raise CompressionError('预处理视频方向或画面比例发生变化。')
    if source['audioDuration'] is not None:
        if info['audioDuration'] is None or abs(info['audioDuration'] - min(source['audioDuration'], expected)) > max(tolerance, 0.25):
            raise CompressionError('预处理视频音轨丢失或音频时长异常。')
    if transcoded and (info['videoCodec'] != 'h264' or (info['audioDuration'] is not None and info['audioCodec'] != 'aac')):
        raise CompressionError('预处理视频必须采用 H.264/AAC。')
    if max_bytes is not None and path.stat().st_size > max_bytes:
        raise CompressionError('预处理视频超过上传大小限制。')
    if decode:
        try:
            result = subprocess.run([str(resolve_ffmpeg()), '-v', 'error', '-xerror', '-nostdin', '-i', str(path), '-map', '0:v:0', '-map', '0:a:0?', '-f', 'null', '-'], capture_output=True, timeout=3600)
            if result.returncode:
                raise CompressionError('预处理视频画面或音频无法完整解码。')
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CompressionError('预处理视频解码校验失败。') from exc
    return info


@contextmanager
def prepare_analysis_video(config: dict, directory: Path, *, benchmark: str | None = None, url: str | None = None):
    """Return upload path and internal metadata; no new webhook fields."""
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='benchmark-', dir=directory) as job:
        work = Path(job)
        source = Path(benchmark).expanduser().resolve() if benchmark else work / 'download.mp4'
        if url:
            source = work / 'download.mp4'
            download_video(url, source)
        original = probe_video(source)
        expected = analysis_window(config, original['duration'])
        trimmed = expected < original['duration']
        candidate = source
        if trimmed:
            candidate = work / 'prefix.mp4'
            transcode_prefix(source, candidate, expected)
            validate_video(candidate, original, expected, transcoded=True)
        if not trimmed and 'mp4' not in original['container']:
            candidate = work / 'normalized.mp4'
            transcode_prefix(source, candidate, expected)
        prepared = prepare_benchmark_video(candidate, output_dir=work)
        output = Path(prepared['filePath'])
        info = validate_video(output, original, expected, max_bytes=DEFAULT_MAX_BYTES, decode=True, transcoded=candidate != source or prepared['compressed'])
        # Validated full URL may remain direct. All prefixes must upload local bytes.
        direct = bool(url and not trimmed and not prepared['compressed'] and not prepared['normalized'] and candidate == source)
        result = {**prepared, 'sourceDuration': original['duration'], 'analysisDuration': info['duration'], 'trimmed': trimmed, 'directUrl': url if direct else None}
        if not direct:
            # Caller uploads inside the context so temporary files stay alive.
            result['filePath'] = str(output)
        yield result
