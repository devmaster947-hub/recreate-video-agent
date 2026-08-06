from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/resolve_benchmark_video.py"
spec = importlib.util.spec_from_file_location("resolve_benchmark_video", SCRIPT)
resolver = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(resolver)


class ResolverClassificationTests(unittest.TestCase):
    def test_recognizes_tiktok_hosts_and_short_links(self):
        self.assertTrue(resolver.is_tiktok_url("https://www.tiktok.com/@a/video/123"))
        self.assertTrue(resolver.is_tiktok_url("https://vm.tiktok.com/ABC123/"))
        self.assertTrue(resolver.is_tiktok_url("https://vt.tiktok.com/ABC123/"))
        self.assertFalse(resolver.is_tiktok_url("https://not-tiktok.example/video/123"))

    def test_recognizes_douyin_hosts_and_short_links(self):
        self.assertTrue(resolver.is_douyin_url("https://www.douyin.com/video/123"))
        self.assertTrue(resolver.is_douyin_url("https://v.douyin.com/ABC123/"))
        self.assertTrue(resolver.is_douyin_url("https://www.iesdouyin.com/share/video/123"))
        self.assertFalse(resolver.is_douyin_url("https://not-douyin.example/video/123"))

    def test_recognizes_direct_video_url_without_query_confusion(self):
        self.assertTrue(resolver.is_direct_video_url("https://cdn.example/a.mp4?sig=1"))
        self.assertFalse(resolver.is_direct_video_url("https://example/video/123?format=mp4"))

    def test_direct_public_url_is_returned_without_verification(self):
        result = resolver.resolve_input(
            "https://cdn.example/video/123?token=x", None, 1, None
        )
        self.assertEqual(result["url"], "https://cdn.example/video/123?token=x")
        self.assertEqual(result["resolution"], "direct-unverified")

    def test_local_video_is_rejected_instead_of_uploaded(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "benchmark.mp4"
            video.write_bytes(b"video")
            with self.assertRaises(resolver.ResolveError):
                resolver.resolve_input(str(video), None, 1, None)


class ResolverExtractionTests(unittest.TestCase):
    def test_extracts_sigi_state_play_address_first(self):
        payload = {
            "ItemModule": {
                "123": {
                    "video": {
                        "downloadAddr": "https://cdn.example/download.mp4",
                        "playAddr": "https://cdn.example/play.mp4",
                    }
                }
            }
        }
        html = f'<script id="SIGI_STATE" type="application/json">{json.dumps(payload)}</script>'
        self.assertEqual(
            resolver.extract_media_candidates(html),
            ["https://cdn.example/play.mp4", "https://cdn.example/download.mp4"],
        )

    def test_extracts_universal_data_url_list_and_deduplicates(self):
        payload = {
            "__DEFAULT_SCOPE__": {
                "webapp.video-detail": {
                    "itemInfo": {
                        "itemStruct": {
                            "video": {
                                "playAddr": {
                                    "UrlList": [
                                        "https://cdn.example/video.mp4",
                                        "https://cdn.example/video.mp4",
                                    ]
                                }
                            }
                        }
                    }
                }
            }
        }
        html = (
            '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
            + json.dumps(payload)
            + "</script>"
        )
        self.assertEqual(
            resolver.extract_media_candidates(html),
            ["https://cdn.example/video.mp4"],
        )

    def test_fallback_decodes_escaped_play_address(self):
        html = r'<script>window.x={"playAddr":"https:\/\/cdn.example\/v.mp4"}</script>'
        self.assertEqual(
            resolver.extract_media_candidates(html),
            ["https://cdn.example/v.mp4"],
        )

    def test_extracts_percent_encoded_douyin_render_data(self):
        payload = {
            "aweme": {
                "video": {
                    "play_addr": {
                        "url_list": ["https://cdn.example/douyin.mp4"]
                    }
                }
            }
        }
        html = (
            '<script id="RENDER_DATA" type="application/json">'
            + quote(json.dumps(payload))
            + "</script>"
        )
        self.assertEqual(
            resolver.extract_media_candidates(html),
            ["https://cdn.example/douyin.mp4"],
        )

    def test_extracts_douyin_mobile_share_page_play_address(self):
        payload = {
            "item_list": [
                {
                    "video": {
                        "play_addr": {
                            "uri": "video-id",
                            "url_list": ["https://cdn.example/share-video.mp4"],
                        }
                    }
                }
            ]
        }
        html = "<script>window._ROUTER_DATA=" + json.dumps(payload) + "</script>"
        self.assertEqual(
            resolver.extract_media_candidates(html),
            ["https://cdn.example/share-video.mp4"],
        )

    def test_adds_official_douyin_share_page_variant(self):
        self.assertEqual(
            resolver._douyin_page_variants("https://www.douyin.com/video/123?x=1"),
            [
                "https://www.douyin.com/video/123?x=1",
                "https://www.iesdouyin.com/share/video/123/",
            ],
        )

    def test_page_resolution_returns_candidate_without_download_helpers(self):
        original = resolver._fetch_html
        payload = {"video": {"playAddr": "https://cdn.example/media/123"}}
        try:
            resolver._fetch_html = lambda url, timeout: (
                '<script type="application/json">' + json.dumps(payload) + "</script>",
                "https://www.tiktok.com/@a/video/123",
            )
            self.assertEqual(
                resolver.resolve_page_to_url("https://www.tiktok.com/@a/video/123", 1),
                "https://cdn.example/media/123",
            )
        finally:
            resolver._fetch_html = original
        self.assertFalse(hasattr(resolver, "_download_candidate"))
        self.assertFalse(hasattr(resolver, "_upload_and_verify"))


if __name__ == "__main__":
    unittest.main()
