import concurrent.futures
import io
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import benchmark_preprocessing as pre
from scripts.compress_benchmark_video import CompressionError
from scripts.server_video_analysis import build_input


class ProtocolTests(unittest.TestCase):
    def test_duration_semantics(self):
        self.assertEqual(pre.analysis_window({'durationMode': 'source', 'duration': 29}, 28.6), 28.6)
        self.assertEqual(pre.analysis_window({'durationMode': 'custom', 'requestedDuration': 15, 'duration': 15}, 29), 15)
        self.assertEqual(pre.analysis_window({'durationMode': 'custom', 'duration': 15}, 15), 15)
        for config in ({'durationMode': 'custom', 'duration': 30}, {'durationMode': 'custom', 'requestedDuration': 15, 'duration': 10}, {'durationMode': 'other'}, {'durationMode': 'source', 'duration': 15}, {'durationMode': 'custom', 'duration': True}):
            with self.assertRaises(CompressionError):
                pre.analysis_window(config, 29)

    def test_payload_preserves_source_and_filters_cuts(self):
        manifest = {'userConfig': {'durationMode': 'custom', 'duration': 15, 'plannerVersion': '2'}, 'benchmarkVideo': {'analysis': {'media': {'duration': 29}, 'analysisDuration': 15, 'targetDurationSource': 'custom', 'candidateCuts': [-1, 0, 4, 14.9, 15, 20]}}}
        payload = build_input(manifest, {'url': 'https://example.com/prefix.mp4'})
        self.assertEqual(payload['userConfig']['technicalCutCandidates'], [0, 4, 14.9])
        self.assertEqual(payload['userConfig']['sourceDuration'], 29)
        self.assertEqual(payload['userConfig']['targetDuration'], 15)
        self.assertEqual(payload['plannerVersion'], '2')
        self.assertNotIn('analysisDuration', payload['userConfig'])

    def test_public_ip_checks(self):
        for url in ('file:///etc/passwd', 'http://user:pass@example.com/a', 'http://127.0.0.1/a', 'http://10.0.0.1/a', 'http://169.254.169.254/a', 'http://[::1]/a', 'http://[::ffff:127.0.0.1]/a'):
            with self.assertRaises(CompressionError, msg=url):
                pre.public_endpoint(url)
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)), (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))]
        with patch.object(pre.socket, 'getaddrinfo', return_value=addresses):
            with self.assertRaises(CompressionError):
                pre.public_endpoint('https://example.com/a')

    def test_transport_pins_ip_and_tls_hostname(self):
        with patch.object(pre, 'public_endpoint', return_value=(pre.urlsplit('https://example.com/a'), '93.184.216.34', 443)), patch.object(pre.socket, 'create_connection') as connect, patch.object(pre.ssl, 'create_default_context') as tls, patch.object(pre.http.client, 'HTTPConnection') as http:
            pre.open_download('https://example.com/a', 10)
            self.assertEqual(connect.call_args.args[0], ('93.184.216.34', 443))
            self.assertLessEqual(connect.call_args.kwargs['timeout'], 10)
            tls.return_value.wrap_socket.assert_called_once_with(connect.return_value, server_hostname='example.com')
            http.return_value.request.assert_called_once_with('GET', '/a', headers={'Accept-Encoding': 'identity'})

    def test_download_bounds_redirects_errors(self):
        class Response(io.BytesIO):
            def __init__(self, data=b'video', status=200, headers=None):
                super().__init__(data)
                self.status = status
                self.headers = headers or {}
            def getheader(self, key):
                return self.headers.get(key)
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / 'v.mp4'
            for response in (Response(headers={'Content-Length': str(pre.DOWNLOAD_MAX_BYTES + 1)}), Response(data=b'123456', headers={'Content-Length': '4'}), Response(data=b''), Response(status=403)):
                with patch.object(pre, 'open_download', return_value=(Mock(sock=None), response)):
                    with self.assertRaises(CompressionError):
                        pre.download_video('https://example.com/a', dest)
                    self.assertFalse(dest.exists())
            with patch.object(pre, 'open_download', side_effect=lambda *a: (Mock(sock=None), Response(status=302, headers={'Location': '/next'}))) as download:
                with self.assertRaises(CompressionError):
                    pre.download_video('https://example.com/a', dest)
                self.assertEqual(download.call_count, 4)
            with patch.object(pre, 'open_download', side_effect=[(Mock(), Response(status=302, headers={'Location': 'http://127.0.0.1/a'})), CompressionError('blocked')]) as download:
                with self.assertRaises(CompressionError):
                    pre.download_video('https://example.com/a', dest)
                self.assertEqual(download.call_args.args[0], 'http://127.0.0.1/a')
            with patch.object(pre, 'open_download', side_effect=TimeoutError()):
                with self.assertRaises(CompressionError):
                    pre.download_video('https://example.com/a', dest)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires FFmpeg and ffprobe')
class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name).resolve()
        for seconds, audio in ((29, True), (15, True), (60, False)):
            output = cls.root / f'{seconds}.mp4'
            command = ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x240:rate=10']
            if audio:
                command += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000']
            command += ['-t', str(seconds), '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-pix_fmt', 'yuv420p']
            if audio:
                command += ['-c:a', 'aac']
            subprocess.run([*command, str(output)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def prepare(self, seconds, config, **kwargs):
        return pre.prepare_analysis_video(config, self.root / 'upload', benchmark=str(self.root / f'{seconds}.mp4'), **kwargs)

    def test_prefix_audio_and_no_audio(self):
        for seconds, target, audio in ((29, 15, True), (60, 30, False)):
            source = self.root / f'{seconds}.mp4'
            before = source.read_bytes()
            with self.prepare(seconds, {'durationMode': 'custom', 'duration': target}) as result:
                self.assertTrue(result['trimmed'])
                self.assertFalse(result['compressed'])
                self.assertAlmostEqual(result['sourceDuration'], seconds, places=1)
                info = pre.probe_video(Path(result['filePath']))
                self.assertAlmostEqual(info['duration'], target, places=1)
                self.assertEqual(info['audioDuration'] is not None, audio)
                self.assertEqual(info['videoCodec'], 'h264')
                self.assertAlmostEqual(info['aspect'], 2/3)
                if audio:
                    self.assertLess(abs(info['audioDuration'] - target), 0.1)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse(Path(result['filePath']).exists())

    def test_equal_and_full_do_not_transcode(self):
        for seconds, config in ((15, {'durationMode': 'custom', 'duration': 15}), (29, {'durationMode': 'source', 'duration': 29})):
            with patch.object(pre, 'transcode_prefix') as trim:
                with self.prepare(seconds, config) as result:
                    self.assertEqual(Path(result['filePath']), self.root / f'{seconds}.mp4')
                    self.assertFalse(result['trimmed'])
                trim.assert_not_called()

    def test_rotation_metadata_is_preserved_visually(self):
        source = self.root / 'rotated.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-display_rotation:v:0', '90', '-i', str(self.root / '29.mp4'), '-c', 'copy', str(source)], check=True, capture_output=True)
        original = pre.probe_video(source)
        with pre.prepare_analysis_video({'durationMode': 'custom', 'duration': 15}, self.root / 'upload', benchmark=str(source)) as result:
            output = pre.probe_video(Path(result['filePath']))
            self.assertAlmostEqual(original['aspect'], output['aspect'])
            self.assertAlmostEqual(output['aspect'], 1.5)

    def test_trim_before_compression(self):
        from scripts.compress_benchmark_video import prepare_benchmark_video
        def compressed(path, **kwargs):
            self.assertAlmostEqual(pre.probe_video(Path(path))['duration'], 15, places=1)
            return prepare_benchmark_video(path, max_bytes=180_000, **kwargs)
        with patch.object(pre, 'prepare_benchmark_video', side_effect=compressed):
            with self.prepare(29, {'durationMode': 'custom', 'duration': 15}) as result:
                self.assertTrue(result['compressed'])
                self.assertLessEqual(Path(result['filePath']).stat().st_size, 180_000)

    def test_direct_url_downloads_and_only_uploads_prefix(self):
        def download(url, destination):
            shutil.copyfile(self.root / '29.mp4', destination)
        with patch.object(pre, 'download_video', side_effect=download):
            with self.prepare(29, {'durationMode': 'custom', 'duration': 15}, url='https://example.com/a.mp4') as result:
                self.assertIsNone(result['directUrl'])
                self.assertAlmostEqual(pre.probe_video(Path(result['filePath']))['duration'], 15, places=1)
            with self.prepare(29, {'durationMode': 'source'}, url='https://example.com/a.mp4') as result:
                self.assertEqual(result['directUrl'], 'https://example.com/a.mp4')

    def test_failure_and_validation_are_fail_closed(self):
        with patch.object(pre, 'transcode_prefix', side_effect=CompressionError('裁剪失败')):
            with self.assertRaises(CompressionError):
                with self.prepare(29, {'durationMode': 'custom', 'duration': 15}):
                    self.fail('must never return an upload path')
        with patch.object(pre.subprocess, 'run', side_effect=OSError('FFmpeg missing')):
            with self.assertRaises(CompressionError):
                pre.transcode_prefix(self.root / '29.mp4', self.root / 'fail.mp4', 15)
        info = pre.probe_video(self.root / '29.mp4')
        with self.assertRaises(CompressionError):
            pre.validate_video(self.root / '29.mp4', info, 15)
        with self.assertRaises(CompressionError):
            pre.validate_video(self.root / '60.mp4', info, 60)
        with self.assertRaises(CompressionError):
            pre.validate_video(self.root / '29.mp4', info, 29, max_bytes=1)

    def test_main_uploads_only_validated_prefix(self):
        import json
        import sys
        from scripts import server_video_analysis as server
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'version': '4', 'userConfig': {'durationMode': 'custom', 'duration': 15, 'requestedDuration': 15, 'plannerVersion': '2'}, 'benchmarkVideo': {'analysis': {'media': {'duration': 999}, 'candidateCuts': [5, 20]}}}))
            def upload(cli, key, args, timeout):
                self.assertEqual(args[0], 'upload')
                info = pre.probe_video(Path(args[1]))
                self.assertAlmostEqual(info['duration'], 15, places=1)
                self.assertIsNotNone(info['audioDuration'])
                return {'url': 'https://example.com/processed.mp4'}
            with patch.object(sys, 'argv', ['server', '--manifest', str(manifest), '--benchmark', str(self.root / '29.mp4')]), patch.object(server, 'resolve_cli', return_value=Path('/fake/cli')), patch.object(server, 'load_key', return_value='fake'), patch.object(server, 'verify_key'), patch.object(server, 'run_cli', side_effect=upload), patch.object(server, 'submit_attempt', side_effect=RuntimeError('stop before remote submit')) as submit:
                with self.assertRaisesRegex(RuntimeError, 'stop before remote'):
                    server.main()
                payload = submit.call_args.args[4]
                self.assertEqual(payload['benchmarkVideo']['url'], 'https://example.com/processed.mp4')
                self.assertEqual(payload['userConfig']['sourceDuration'], 29)
                self.assertEqual(payload['userConfig']['technicalCutCandidates'], [5])
                self.assertFalse(any((root / 'analysis/upload').iterdir()))

    def test_main_preprocessing_failure_never_uploads_or_reserves(self):
        import contextlib
        import json
        import sys
        from scripts import server_video_analysis as server
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / 'manifest.json'
            manifest.write_text(json.dumps({'version': '4', 'userConfig': {'durationMode': 'custom', 'duration': 15}, 'benchmarkVideo': {}}))
            with patch.object(sys, 'argv', ['server', '--manifest', str(manifest), '--benchmark', str(self.root / '29.mp4')]), patch.object(server, 'resolve_cli', return_value=Path('/fake/cli')), patch.object(server, 'load_key', return_value='fake'), patch.object(server, 'verify_key'), patch.object(pre, 'transcode_prefix', side_effect=CompressionError('裁剪失败')), patch.object(server, 'run_cli') as upload, patch.object(server, 'submit_attempt') as submit, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    server.main()
                upload.assert_not_called()
                submit.assert_not_called()
                self.assertNotIn('videoBlueprint', json.loads(manifest.read_text()))

    def test_concurrent_unique_directories(self):
        barrier = __import__('threading').Barrier(2)
        def prepare(_):
            with self.prepare(29, {'durationMode': 'custom', 'duration': 15}) as result:
                barrier.wait(timeout=30)
                path = Path(result['filePath'])
                self.assertTrue(path.is_file())
                return path
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            paths = list(pool.map(prepare, range(2)))
        self.assertNotEqual(paths[0].parent, paths[1].parent)


if __name__ == '__main__':
    unittest.main()
