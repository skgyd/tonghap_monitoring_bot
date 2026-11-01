"""Mattermost-native demo. Only read-only source views share the browser's MM origin."""
import hmac
import json
import secrets
import uuid
import ipaddress
import logging
import signal
import threading
import urllib.error
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app import Application, Handler, Server, replica_page
from bridge.collector import Collector
from bridge.engine import Conflict
from commands import Commands
from provision import API

WEB = Path(__file__).parent / 'web'
VIEWS = {'/ib-sources/both', '/ib-sources/polestar', '/ib-sources/jobpass',
         '/ib-sources/source-ui.js', '/ib-sources/data/polestar', '/ib-sources/data/jobpass'}


class SourceHandler(Handler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path not in VIEWS:
            return self.send(404, {})
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            session = cookie.get('MMAUTHTOKEN')
            if not session:
                return self.send(401, {'error':'먼저 Mattermost에 로그인하세요.'})
            me, _ = self.server.api.call('GET', '/users/me', token=session.value)
            if me['id'] not in self.server.settings['judges'].values():
                return self.send(403, {})
            if '/data/' in path:
                with self.a.mock_lock:
                    data = list(self.a.mock[path.rsplit('/',1)[1]])
                return self.send(200, data)
            if path.endswith('/source-ui.js'):
                return self.send(200, (WEB/'source-ui.js').read_bytes(), 'text/javascript; charset=utf-8')
            if path.endswith('/both'):
                body = (WEB/'sources.html').read_bytes()
            else:
                body = replica_page(path.rsplit('/', 1)[1])
            self.send(200, body, 'text/html; charset=utf-8',
                      {'Content-Security-Policy':"frame-ancestors 'self'; base-uri 'self'; object-src 'none'"})
        except CookieError:
            self.send(401, {})
        except urllib.error.HTTPError as exc:
            self.send(exc.code if exc.code in (401,403) else 503, {})
        except OSError:
            self.send(503, {})

    def do_POST(self):
        if self.a.config.get('portable') or self.path != '/ib-operator/events':
            return self.send(404, {})
        key = self.server.operator_key
        auth = self.headers.get('Authorization', '').encode('utf-8')
        if self.headers.get('Origin') or not hmac.compare_digest(auth, ('Bearer ' + key).encode()):
            return self.send(403, {})
        if self.headers.get_content_type() != 'application/json':
            return self.send(415, {})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1024:
                raise ValueError()
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict) or set(body) != {'source', 'request_id'}:
                raise ValueError()
            source, rid = body['source'], body['request_id']
            if source not in ('polestar', 'jobpass') or not isinstance(rid, str):
                raise ValueError()
            parsed = uuid.UUID(rid)
            if parsed.version != 4 or str(parsed) != rid:
                raise ValueError()
            if not self.a.config.get('local_lab'):
                return self.send(403, {})
            event = self.a.add_demo(source, direct=False, request_id=rid)
        except Conflict as exc:
            return self.send(409, {'error':str(exc)})
        except (ValueError, TypeError):
            return self.send(400, {})
        except OSError:
            return self.send(503, {})
        self.send(200, {'event_id':event['event_id'], 'source':source})


class CollectorHandler(Handler):
    def do_GET(self):
        if self.path in ('/health','/mock/polestar','/mock/jobpass','/mock/source-ui.js','/mock-data/polestar','/mock-data/jobpass'):
            return super().do_GET()
        self.send(404, {})

    def do_POST(self):
        self.send(404, {})


class Callback(Handler):
    def do_GET(self):
        self.send(404, {})

    def do_POST(self):
        if self.a.stop.is_set():
            return self.send(503, {'error':'종료 중입니다.'})
        path = urlsplit(self.path).path
        if path == '/mattermost/action':
            return super().do_POST()
        if path != '/mattermost/command':
            return self.send(404, {})
        allowed = self.a.config['mattermost']['callback_allowed_ips']
        if not any(ipaddress.ip_address(self.client_address[0]) in ipaddress.ip_network(n) for n in allowed):
            return self.send(403, {})
        if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
            return self.send(415, {})
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 32768:
                return self.send(400, {})
            parts = parse_qs(self.rfile.read(size).decode(), keep_blank_values=True, max_num_fields=100)
            if any(len(values)!=1 for values in parts.values()):
                return self.send(400, {})
            text = self.server.commands.execute({key:v[0] for key,v in parts.items()})
        except PermissionError:
            return self.send(403, {})
        except ValueError as exc:
            text = '요청을 확인하세요. ' + str(exc)
        except OSError:
            text = 'Mattermost 연결 또는 채널 참여 권한을 확인하세요.'
        except Exception:
            logging.exception('Mattermost command failed')
            text = '명령 처리 실패. 서버 상태를 확인하세요.'
        self.send(200, {'response_type':'ephemeral','text':text[:14500]})


class LocalHTTPServer(Server):
    daemon_threads = False
    def get_request(self):
        conn, address = super().get_request()
        conn.settimeout(12)
        return conn, address


class RunningBridge:
    def __init__(self, root, settings, bind):
        self.a = a = Application(root)
        self.servers, self.threads, self.collectors = [], [], []
        # The parent holds the exclusive instance lock during setup and operation.
        key = None
        if not a.config.get('portable'):
            key_path = a.root / 'operator-key.txt'
            if not key_path.exists():
                temp = key_path.with_suffix('.tmp')
                temp.write_text(secrets.token_hex(32), encoding='ascii')
                temp.chmod(0o600)
                temp.replace(key_path)
            key = key_path.read_text('ascii').strip()
            if len(key) != 64 or any(ch not in '0123456789abcdef' for ch in key):
                raise RuntimeError('시연자 키 파일이 손상되었습니다.')
        try:
            service = LocalHTTPServer if a.config.get('portable') else Server
            private = service(('127.0.0.1', a.config['port']), CollectorHandler)
            self.servers.append(private)
            callback = service((bind, a.config['callback_port']), Callback)
            self.servers.append(callback)
            callback.commands = Commands(a, settings)
            public = service((bind, 8780), SourceHandler)
            self.servers.append(public)
            public.api = API(a.config['mattermost']['url'])
            public.settings, public.operator_key = settings, key
            for server in self.servers:
                server.application = a
                self.start_worker(server.serve_forever)
            for worker in (a.background, a.activity_background):
                self.start_worker(worker)
            for name, cfg in a.config['collectors'].items():
                if cfg['enabled']:
                    collector = Collector(a.engine, a.root, name, cfg, a.stop)
                    collector.start()
                    self.collectors.append(collector)
        except Exception:
            self.close()
            raise

    def start_worker(self, fn):
        thread = threading.Thread(target=fn, daemon=True)
        thread.start()
        self.threads.append(thread)

    def close(self):
        self.a.stop.set()
        for i, server in enumerate(self.servers):
            if i < len(self.threads):
                server.shutdown()
            server.server_close()
        for worker in self.collectors + self.threads:
            worker.join(timeout=None if self.a.config.get('portable') else 15)


def serve(root, settings):
    bridge = RunningBridge(root, settings, '0.0.0.0')
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: bridge.a.stop.set())
    try:
        bridge.a.stop.wait()
    finally:
        bridge.close()
