#!/usr/bin/env python3
"""Resolve a TikTok/Douyin page to a media URL without downloading or uploading video."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import ssl
import sys
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen


ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".ogg"}
TIKTOK_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
}
DOUYIN_HOSTS = {
    "douyin.com",
    "www.douyin.com",
    "m.douyin.com",
    "v.douyin.com",
    "iesdouyin.com",
    "www.iesdouyin.com",
}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
DOUYIN_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 Mobile/15E148"
)
HTML_LIMIT = 12 * 1024 * 1024
SSL_CONTEXT = ssl.create_default_context()


class ResolveError(RuntimeError):
    pass


class _JsonScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._capture = False
        self._chunks: list[str] = []
        self.payloads: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        script_id = values.get("id", "")
        script_type = values.get("type", "")
        if script_id in {
            "SIGI_STATE",
            "__UNIVERSAL_DATA_FOR_REHYDRATION__",
            "__NEXT_DATA__",
            "RENDER_DATA",
        } or script_type == "application/json":
            self._capture = True
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._capture:
            payload = "".join(self._chunks).strip()
            if payload:
                self.payloads.append(payload)
            self._capture = False
            self._chunks = []


def is_tiktok_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and (
        host in TIKTOK_HOSTS or host.endswith(".tiktok.com")
    )


def is_douyin_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and (
        host in DOUYIN_HOSTS
        or host.endswith(".douyin.com")
        or host.endswith(".iesdouyin.com")
    )


def is_supported_page_url(value: str) -> bool:
    return is_tiktok_url(value) or is_douyin_url(value)


def is_direct_video_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return (
        parsed.scheme in {"http", "https"}
        and Path(parsed.path).suffix.lower() in ALLOWED_VIDEO_SUFFIXES
    )


def is_public_http_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _collect_http_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            yield value
        return
    if isinstance(value, dict):
        for nested in value.values():
            yield from _collect_http_strings(nested)
        return
    if isinstance(value, list):
        for nested in value:
            yield from _collect_http_strings(nested)


def _walk_media_fields(value: Any) -> Iterable[tuple[int, str]]:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = key.lower().replace("_", "")
            if normalized in {"playaddr", "playurl", "playapi"}:
                for url in _collect_http_strings(nested):
                    yield 0, url
            elif normalized in {"downloadaddr", "downloadurl"}:
                for url in _collect_http_strings(nested):
                    yield 1, url
            yield from _walk_media_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_media_fields(nested)


def extract_media_candidates(html: str) -> list[str]:
    parser = _JsonScriptParser()
    parser.feed(html)
    ranked: list[tuple[int, str]] = []
    for payload in parser.payloads:
        variants = [payload]
        decoded = unquote(payload)
        if decoded != payload:
            variants.append(decoded)
        for variant in variants:
            try:
                ranked.extend(_walk_media_fields(json.loads(variant)))
                break
            except json.JSONDecodeError:
                continue

    # Fallback for page variants that embed the same fields in non-JSON
    # script text. json.loads decodes escaped slashes and unicode safely.
    pattern = re.compile(
        r'"(playAddr|play_addr|playApi|play_api|downloadAddr|download_addr)"'
        r'\s*:\s*"((?:\\.|[^"\\])+)"'
    )
    for key, encoded in pattern.findall(html):
        try:
            url = json.loads('"' + encoded + '"')
        except json.JSONDecodeError:
            continue
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            normalized = key.lower().replace("_", "")
            ranked.append((0 if normalized in {"playaddr", "playapi"} else 1, url))

    # Douyin's official mobile share page embeds a JSON object in an untyped
    # script. Extract only url_list arrays nested directly below play_addr so
    # unrelated image and avatar URLs are never treated as video candidates.
    play_addr_pattern = re.compile(
        r'"play_addr"\s*:\s*\{(?P<body>.*?)\}\s*(?:,|\})', re.DOTALL
    )
    url_list_pattern = re.compile(r'"url_list"\s*:\s*(\[[^\]]*\])', re.DOTALL)
    for match in play_addr_pattern.finditer(html):
        list_match = url_list_pattern.search(match.group("body"))
        if not list_match:
            continue
        try:
            urls = json.loads(list_match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(urls, list):
            for url in urls:
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    ranked.append((0, url))

    seen: set[str] = set()
    output: list[str] = []
    for _, url in sorted(ranked, key=lambda item: item[0]):
        if url not in seen:
            seen.add(url)
            output.append(url)
    return output


def _fetch_html(url: str, timeout: float) -> tuple[str, str]:
    headers = {
        "User-Agent": DOUYIN_USER_AGENT if is_douyin_url(url) else USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
    }
    try:
        with urlopen(
            Request(url, headers=headers),
            timeout=timeout,
            context=SSL_CONTEXT,
        ) as response:
            final_url = response.geturl()
            body = response.read(HTML_LIMIT + 1)
            if len(body) > HTML_LIMIT:
                raise ResolveError("video page exceeded the safe response-size limit")
            charset = response.headers.get_content_charset() or "utf-8"
            return body.decode(charset, errors="replace"), final_url
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ResolveError(f"could not read video page: {exc}") from exc


def _tiktok_page_variants(url: str) -> list[str]:
    variants = [url]
    match = re.search(r"/video/(\d+)", urlparse(url).path)
    if match:
        variants.append(f"https://www.tiktok.com/embed/v2/{match.group(1)}")
    return variants


def _douyin_page_variants(url: str) -> list[str]:
    variants = [url]
    match = re.search(r"/(?:video|share/video)/(\d+)", urlparse(url).path)
    if match:
        variants.append(f"https://www.iesdouyin.com/share/video/{match.group(1)}/")
    return variants


def _page_variants(url: str) -> list[str]:
    if is_tiktok_url(url):
        return _tiktok_page_variants(url)
    return _douyin_page_variants(url)


def resolve_page_to_url(url: str, timeout: float) -> str:
    """Extract the first page-declared media URL without requesting its bytes."""
    errors: list[str] = []
    checked_pages: set[str] = set()
    queue = _page_variants(url)
    while queue:
        page_url = queue.pop(0)
        if page_url in checked_pages:
            continue
        checked_pages.add(page_url)
        try:
            html, final_url = _fetch_html(page_url, timeout)
            if not is_supported_page_url(final_url):
                errors.append("page redirected to an unsupported host")
                continue
            for variant in _page_variants(final_url):
                if variant not in checked_pages and variant not in queue:
                    queue.append(variant)
            candidates = extract_media_candidates(html)
            if candidates:
                return candidates[0]
            errors.append("page contained no media address")
        except ResolveError as exc:
            errors.append(str(exc))
    platform = "TikTok" if is_tiktok_url(url) else "Douyin"
    detail = errors[-1] if errors else f"unknown {platform} response"
    raise ResolveError(f"could not extract {platform} media URL ({detail})")


def resolve_input(
    source: str,
    endpoint: str | None = None,
    timeout: float = 120.0,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    expanded = Path(source).expanduser()
    if expanded.exists():
        raise ResolveError(
            "local benchmark videos are no longer uploaded; provide a public HTTP(S) URL"
        )

    if is_public_http_url(source) and not is_supported_page_url(source):
        return {
            "url": source,
            "provider": urlparse(source).hostname,
            "resolution": "direct-unverified",
        }

    if not is_supported_page_url(source):
        raise ResolveError(
            "web-page URLs are unsupported; provide a TikTok or Douyin link, "
            "or a public HTTP(S) video URL"
        )

    media_url = resolve_page_to_url(source, timeout)
    return {
        "url": media_url,
        "provider": urlparse(media_url).hostname,
        "resolution": "page-extracted-unverified",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source", help="TikTok/Douyin URL or public HTTP(S) video URL"
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = resolve_input(args.source, None, args.timeout, None)
        print(json.dumps(result, ensure_ascii=False) if args.json else result["url"])
        return 0
    except (ResolveError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
