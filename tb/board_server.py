"""Loopback-only board service; exposes board files, settings and lifecycle endpoints."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from urllib.parse import parse_qs, urlsplit

from tb.replies import ReplyError

from tb.settings_store import SettingsStore, InvalidSettings, SettingsConflict, MAX_SETTINGS_BYTES, validate_settings


class _LoopbackHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # A literal loopback address needs no reverse DNS lookup. HTTPServer's
        # default lookup can block startup when the system resolver is slow.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def create_server(port, directory, refresh, settings_path=None, stop=None, token=None, read_reply=None, assets_dir=None):
    """Return an unstarted server. Settings are opt-in for the installed version.

    The legacy file-based prototype keeps working without settings_path. A runtime
    can provide its own token and stop callback; neither is written by this module.
    """
    root = Path(directory)
    settings = SettingsStore(settings_path) if settings_path else None
    key = token or secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, value, content_type='application/json; charset=utf-8'):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def local_host(self):
            port = self.server.server_port
            if self.headers.get('Host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
                self.reply(403, {'error': 'Invalid local host'})
                return False
            return True

        def authorized(self):
            # A foreign page cannot send this header without a successful CORS
            # preflight. Host validation also rejects DNS rebinding requests.
            supplied = self.headers.get('X-Taskboard-Token', '')
            origin = self.headers.get('Origin')
            allowed = (f'http://127.0.0.1:{self.server.server_port}',
                       f'http://localhost:{self.server.server_port}')
            if not secrets.compare_digest(supplied, key) or (origin and origin not in allowed):
                self.reply(403, {'error': 'Invalid request token or origin'})
                return False
            return True

        def do_GET(self):
            if not self.local_host():
                return
            path = urlsplit(self.path).path
            if path == '/health':
                self.reply(200, {'app': 'taskboard', 'pid': os.getpid(),
                                 'instance': self.server.instance})
                return
            if path == '/api/reply':
                if not self.authorized():
                    return
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if (set(query) != {'session', 'reply'} or any(len(v) != 1 or not v[0] for v in query.values())
                        or read_reply is None):
                    self.reply(404, {'error': 'Reply not found'})
                    return
                try:
                    self.reply(200, read_reply(query['session'][0], query['reply'][0]))
                except ReplyError as error:
                    self.reply(error.status, {'error': str(error)})
                except Exception:
                    self.reply(503, {'error': 'Reply source is unavailable'})
                return
            if path == '/api/settings' and settings:
                try:
                    self.reply(200, settings.read())
                except (OSError, InvalidSettings):
                    self.reply(500, {'error': 'Settings cannot be read; the existing file was not changed'})
                return
            assets = {
                '/assets/markdown-it.min.js': ('vendor/markdown-it.min.js', 'application/javascript; charset=utf-8'),
                '/assets/reply-reader.js': ('reply-reader.js', 'application/javascript; charset=utf-8'),
                '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json; charset=utf-8'),
                '/assets/icon-192.png': ('icon-192.png', 'image/png'),
                '/assets/icon-512.png': ('icon-512.png', 'image/png'),
            }
            if path in assets and assets_dir is not None:
                filename, content_type = assets[path]
                try:
                    body = (Path(assets_dir) / filename).read_bytes()
                except OSError:
                    self.reply(503, {'error': 'Asset is unavailable'})
                    return
                self.reply(200, body, content_type)
                return
            files = {'/': 'taskboard.html', '/taskboard.html': 'taskboard.html',
                     '/taskboard.json': 'taskboard.json'}
            filename = files.get(path)
            if filename is None:
                self.reply(404, {'error': 'Not found'})
                return
            try:
                body = (root / filename).read_bytes()
            except OSError:
                self.reply(503, {'error': 'Board data is not available yet'})
                return
            if filename.endswith('.html'):
                if settings:
                    try:
                        initial = settings.read()
                    except (OSError, InvalidSettings):
                        initial = None
                    bootstrap = json.dumps({'token': key, 'snapshot': initial}, ensure_ascii=False).replace('</', '<\\/').encode('utf-8')
                    body = body.replace(b'/*PERSIST*/null', bootstrap)
                self.reply(200, body, 'text/html; charset=utf-8')
            else:
                self.reply(200, body)

        def do_PUT(self):
            if not self.local_host():
                return
            if urlsplit(self.path).path != '/api/settings' or not settings:
                self.reply(404, {'error': 'Not found'})
                return
            if not self.authorized():
                return
            if self.headers.get_content_type() != 'application/json':
                self.reply(415, {'error': 'JSON required'})
                return
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= length <= MAX_SETTINGS_BYTES:
                    self.reply(413, {'error': 'Settings payload too large or missing length'})
                    return
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict) or set(value) != {'settings', 'revision'}:
                    raise InvalidSettings('Expected settings and revision')
                result = settings.write(validate_settings(value['settings']), value['revision'])
            except SettingsConflict:
                self.reply(409, {'error': 'Settings changed in another page; reload before editing'})
                return
            except (ValueError, TypeError):
                self.reply(400, {'error': 'Invalid settings; existing settings were preserved'})
                return
            except OSError:
                self.reply(500, {'error': 'Settings could not be saved'})
                return
            self.reply(200, result)

        def do_POST(self):
            if not self.local_host():
                return
            path = urlsplit(self.path).path
            if path == '/refresh':
                if settings and not self.authorized():
                    return
                try:
                    self.reply(200, refresh())
                except Exception:
                    self.reply(500, {'error': 'Refresh failed'})
                return
            if path == '/shutdown' and stop:
                if not self.authorized():
                    return
                self.reply(200, {'stopping': True})
                threading.Thread(target=stop, daemon=True).start()
                return
            self.reply(404, {'error': 'Not found'})

        def log_message(self, *args):
            pass

    httpd = _LoopbackHTTPServer(('127.0.0.1', port), Handler)
    httpd.daemon_threads = True
    httpd.token = key
    httpd.instance = hashlib.sha256(key.encode('utf-8')).hexdigest()
    return httpd
