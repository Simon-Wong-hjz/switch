import hashlib
import http.client
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urljoin

from tb.board_server import create_server
from tb.replies import ReplyStore, describe


class BoardServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'taskboard.html').write_text('<script>const PERSIST = /*PERSIST*/null;</script>')
        (self.root / 'taskboard.json').write_text('{"tasks":[]}')
        (self.root / 'install.json').write_text('{"private":true}')
        self.assets = self.root / 'web'
        (self.assets / 'vendor').mkdir(parents=True)
        (self.assets / 'vendor' / 'markdown-it.min.js').write_text('window.markdownit = true;')
        (self.assets / 'reply-reader.js').write_text('window.ReplyReader = true;')
        (self.assets / 'private.json').write_text('{"private":true}')
        source_assets = Path(__file__).resolve().parent.parent / 'web'
        for name in ('manifest.webmanifest', 'icon-192.png', 'icon-512.png'):
            (self.assets / name).write_bytes((source_assets / name).read_bytes())
        self.replies = ReplyStore()
        self.reply_text = '  # Exact reply\n\n```py\nx = 1\n```\n  '
        record = {'timestamp': '2026-10-15T10:00:00Z', 'type': 'response_item', 'payload': {
            'type': 'message', 'role': 'assistant', 'channel': 'final',
            'content': [{'type': 'output_text', 'text': self.reply_text}]}}
        raw = json.dumps(record).encode() + b'\n'
        self.source = self.root / 'source.jsonl'
        self.source.write_bytes(raw)
        self.descriptor = describe(record, raw, 0, 'codex')
        self.replies.update({'session-one': (self.source, 'codex', self.descriptor)})
        self.stopped = threading.Event()
        self.server = create_server(0, self.root, lambda: {'generated': 'now', 'tasks': []},
                                    self.root / 'settings.json', self.stopped.set, 'private-token',
                                    read_reply=self.replies.read, assets_dir=self.assets)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, payload=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        h = {'X-Taskboard-Token': 'private-token', 'Content-Type': 'application/json'}
        h.update(headers or {})
        conn.request(method, path, json.dumps(payload) if payload is not None else None, h)
        response = conn.getresponse()
        result = (response.status, response.read(), dict(response.getheaders()))
        conn.close()
        return result

    def reply_url(self, **extra):
        query = {'session': 'session-one', 'reply': self.descriptor['id'], **extra}
        from urllib.parse import urlencode
        return '/api/reply?' + urlencode(query)

    def test_loopback_startup_does_not_require_reverse_dns(self):
        with patch('socket.getfqdn', side_effect=OSError('Resolver unavailable')):
            server = create_server(0, self.root, lambda: {'tasks': []})
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], '127.0.0.1')
        self.assertGreater(server.server_port, 0)

    def test_reply_endpoint_requires_token_origin_and_local_host(self):
        for headers in ({'X-Taskboard-Token': ''}, {'X-Taskboard-Token': 'wrong'},
                        {'Origin': 'https://foreign.example'}, {'Host': 'foreign.example'}):
            self.assertEqual(self.request('GET', self.reply_url(), headers=headers)[0], 403)
        code, body, headers = self.request('GET', self.reply_url())
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {'session_id': 'session-one', 'reply_id': self.descriptor['id'],
                                          'timestamp': self.descriptor['timestamp'], 'text': self.reply_text})
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertNotIn(str(self.source).encode(), body)

    def test_reply_endpoint_rejects_unknown_and_path_requests(self):
        for path in ('/api/reply', '/api/reply?session=session-one', self.reply_url(reply='unknown'),
                     self.reply_url(session='../../etc/passwd'), self.reply_url(path=str(self.source)),
                     self.reply_url() + '&session=another'):
            self.assertEqual(self.request('GET', path)[0], 404)
        self.assertEqual(self.request('GET', '/source.jsonl')[0], 404)

    def test_reply_endpoint_does_not_silently_replace_historical_id(self):
        record = {'timestamp': '2026-10-15T10:00:01Z', 'type': 'response_item', 'payload': {
            'type': 'message', 'role': 'assistant', 'channel': 'final',
            'content': [{'type': 'output_text', 'text': 'new reply'}]}}
        raw = json.dumps(record).encode() + b'\n'
        offset = self.source.stat().st_size
        with self.source.open('ab') as fh:
            fh.write(raw)
        newer = describe(record, raw, offset, 'codex')
        self.replies.update({'session-one': (self.source, 'codex', newer)})
        self.assertEqual(json.loads(self.request('GET', self.reply_url())[1])['text'], self.reply_text)
        self.assertEqual(json.loads(self.request('GET', self.reply_url(reply=newer['id']))[1])['text'], 'new reply')
        self.source.write_bytes(raw)
        code, body, _ = self.request('GET', self.reply_url())
        self.assertEqual(code, 410)
        self.assertNotIn(str(self.source).encode(), body)
        self.source.unlink()
        code, body, _ = self.request('GET', self.reply_url())
        self.assertEqual(code, 503)
        self.assertNotIn(str(self.source).encode(), body)

    def test_reader_assets_are_explicitly_allowlisted(self):
        for url, needle in (('/assets/markdown-it.min.js', b'window.markdownit'),
                            ('/assets/reply-reader.js', b'window.ReplyReader')):
            code, body, headers = self.request('GET', url)
            self.assertEqual(code, 200)
            self.assertIn(needle, body)
            self.assertEqual(headers['Content-Type'], 'application/javascript; charset=utf-8')
        for path in ('/assets/private.json', '/assets/../install.json', '/assets/vendor/markdown-it.min.js',
                     '/assets/%2e%2e/install.json'):
            self.assertEqual(self.request('GET', path)[0], 404)
        (self.assets / 'reply-reader.js').unlink()
        self.assertEqual(self.request('GET', '/assets/reply-reader.js')[0], 503)

    def test_browser_app_manifest_and_icons_are_local_and_complete(self):
        code, body, headers = self.request('GET', '/manifest.webmanifest')
        self.assertEqual(code, 200)
        self.assertEqual(headers['Content-Type'], 'application/manifest+json; charset=utf-8')
        manifest = json.loads(body)
        self.assertEqual(manifest['name'], 'Switch')
        self.assertEqual((manifest['id'], manifest['start_url'], manifest['scope']), ('/', '/', '/'))
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['launch_handler']['client_mode'], 'focus-existing')
        self.assertEqual({icon['sizes'] for icon in manifest['icons']}, {'192x192', '512x512'})
        for icon in manifest['icons']:
            icon_path = urljoin('/manifest.webmanifest', icon['src'])
            self.assertTrue(icon_path.startswith('/assets/'))
            code, png, headers = self.request('GET', icon_path)
            self.assertEqual(code, 200)
            self.assertEqual(headers['Content-Type'], 'image/png')
            self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
            self.assertEqual(struct.unpack('>II', png[16:24]), tuple(map(int, icon['sizes'].split('x'))))
            self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_browser_app_routes_keep_host_and_file_boundaries(self):
        for path in ('/manifest.webmanifest', '/assets/icon-192.png'):
            self.assertEqual(self.request('GET', path, headers={'Host': 'foreign.example'})[0], 403)
        for path in ('/assets/manifest.webmanifest', '/assets/../../settings.json', '/assets/icon-1024.png'):
            self.assertEqual(self.request('GET', path)[0], 404)
        (self.assets / 'manifest.webmanifest').unlink()
        self.assertEqual(self.request('GET', '/manifest.webmanifest')[0], 503)
        self.assertEqual(self.request('GET', '/')[0], 200, 'An unavailable icon/manifest must not stop the board')

    def test_roundtrip_import_and_new_page_bootstrap(self):
        code, body, _ = self.request('GET', '/api/settings')
        self.assertEqual((code, json.loads(body)['revision']), (200, 0))
        self.assertFalse((self.root / 'settings.json').exists())
        imported = {'version': 1, 'settings': {
            'prio': {'one': 'high'}, 'members': {'one': True}, 'removed': {'old': True},
            'order': ['one'], 'seen': {'one': '2026-10-14T10:00:00Z'},
            'lang': 'en', 'theme': 'light', 'initAt': '2026-10-14T09:00:00Z'}}
        code, body, _ = self.request('PUT', '/api/settings', {'settings': imported, 'revision': 0})
        saved = json.loads(body)
        self.assertEqual((code, saved['revision']), (200, 1))
        self.assertEqual(saved['settings'], dict(imported['settings'], seenAt={}, notify=False, muteWhenFocused=False))
        code, html, headers = self.request('GET', '/')
        self.assertEqual(code, 200)
        self.assertIn(b'"revision": 1', html)
        self.assertIn(b'private-token', html)
        self.assertNotIn(b'/*PERSIST*/null', html)
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_stale_and_invalid_writes_preserve_file(self):
        self.request('PUT', '/api/settings', {'settings': {'lang': 'en'}, 'revision': 0})
        original = (self.root / 'settings.json').read_bytes()
        self.assertEqual(self.request('PUT', '/api/settings', {'settings': {'lang': 'zh'}, 'revision': 0})[0], 409)
        for settings in ({'theme': 'no'}, {'version': 7, 'settings': {}}, ['bad']):
            self.assertEqual(self.request('PUT', '/api/settings', {'settings': settings, 'revision': 1})[0], 400)
        self.assertEqual((self.root / 'settings.json').read_bytes(), original)

    def test_corrupt_existing_settings_are_not_replaced(self):
        (self.root / 'settings.json').write_text('broken settings')
        self.assertEqual(self.request('GET', '/api/settings')[0], 500)
        self.assertEqual(self.request('PUT', '/api/settings', {'settings': {}, 'revision': 0})[0], 400)
        self.assertEqual((self.root / 'settings.json').read_text(), 'broken settings')
        self.assertIn(b'"snapshot": null', self.request('GET', '/')[1])

    def test_write_requires_this_server_token_and_origin(self):
        payload = {'settings': {}, 'revision': 0}
        for headers in ({'X-Taskboard-Token': ''}, {'X-Taskboard-Token': 'other'}, {'Origin': 'https://foreign.example'}):
            self.assertEqual(self.request('PUT', '/api/settings', payload, headers)[0], 403)
            self.assertEqual(self.request('POST', '/refresh', headers=headers)[0], 403)
        self.assertFalse((self.root / 'settings.json').exists())
        self.assertEqual(self.request('GET', '/api/settings', headers={'Host': 'foreign.example'})[0], 403)

    def test_only_public_board_files_are_served(self):
        for path in ('/install.json', '/settings.json', '/events.jsonl', '/../install.json', '/%2e%2e/install.json'):
            self.assertEqual(self.request('GET', path)[0], 404)
        self.assertEqual(self.request('GET', '/taskboard.json?fresh=1')[0], 200)
        self.assertNotIn('Access-Control-Allow-Origin', self.request('GET', '/')[2])

    def test_bootstrap_escapes_script_end_in_imported_ids(self):
        self.request('PUT', '/api/settings', {'settings': {'members': {'</script><script>bad()': True}}, 'revision': 0})
        html = self.request('GET', '/')[1]
        self.assertNotIn(b'</script><script>bad()', html)
        self.assertIn(b'<\\/script><script>bad()', html)

    def test_lifecycle_matches_instance_and_rejects_unauthorized_stop(self):
        identity = json.loads(self.request('GET', '/health')[1])
        self.assertEqual(identity['app'], 'taskboard')
        self.assertEqual(identity['instance'], hashlib.sha256(b'private-token').hexdigest())
        self.assertEqual(self.request('POST', '/shutdown', headers={'X-Taskboard-Token': 'wrong'})[0], 403)
        self.assertFalse(self.stopped.is_set())
        self.assertEqual(self.request('POST', '/shutdown')[0], 200)
        self.assertTrue(self.stopped.wait(2))


if __name__ == '__main__':
    unittest.main()
