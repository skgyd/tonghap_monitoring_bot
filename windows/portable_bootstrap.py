"""Single-PC launcher: Windows host + bundled QEMU/Ubuntu/Mattermost/PostgreSQL."""
import argparse, hashlib, hmac, http.server, json, logging, os, secrets, shutil
import socket, subprocess, sys, tempfile, threading, time, urllib.request, webbrowser
from pathlib import Path
from http.cookies import SimpleCookie, CookieError
import uuid
sys.dont_write_bytecode = True
from demo_sessions import Sessions, contain_children, plain
from provision import DEMO_JUDGES

ROOT = Path(__file__).resolve().parent
PORTS = (8764, 8765, 8767, 8780, 8065)
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def atomic_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')
    os.replace(tmp, path)
    try: path.chmod(0o600)
    except OSError: pass

def claim_lock(path, existing=False):
    handle = open(path, 'r+b' if existing else 'a+b')
    if not existing and os.fstat(handle.fileno()).st_size == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close(); raise
    return handle

def check_ports():
    probes=[]
    try:
        for port in PORTS:
            probe=socket.socket(); probes.append(probe)
            if os.name != 'nt': probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            try: probe.bind(('127.0.0.1',port))
            except OSError: raise RuntimeError(f'{port} 포트가 사용 중입니다. 기존 IncidentBridge나 해당 프로그램을 종료한 뒤 다시 실행하세요.')
    finally:
        for probe in probes: probe.close()

def installed_browser():
    for channel, relative in [('msedge', 'Microsoft/Edge/Application/msedge.exe'),
                              ('chrome', 'Google/Chrome/Application/chrome.exe')]:
        for key in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
            folder = os.environ.get(key)
            if folder:
                binary = Path(folder) / relative
                if binary.is_file():
                    return channel, binary
    raise RuntimeError('Microsoft Edge 또는 Google Chrome의 설치 상태를 확인하세요.')

def browser_channel():
    if os.name == 'nt':
        return installed_browser()[0]
    return os.environ.get('BRIDGE_DEMO_BROWSER', '')

def open_demo_window(url, data):
    if os.name != 'nt':
        return webbrowser.open(url)
    _, binary = installed_browser()
    profile = data / 'profiles' / 'viewer'
    preferences = profile / 'Default' / 'Preferences'
    downloads = data / 'downloads'
    downloads.mkdir(exist_ok=True)
    preferences.parent.mkdir(parents=True, exist_ok=True)
    settings = json.loads(preferences.read_text('utf-8')) if preferences.is_file() else {}
    download_settings = settings.setdefault('download', {})
    if download_settings.get('default_directory') != str(downloads):
        download_settings['default_directory'] = str(downloads)
        atomic_json(preferences, settings)
    return subprocess.Popen([str(binary), '--user-data-dir=' + str(profile),
                             '--no-first-run', '--no-default-browser-check', url])

def configure_storage(data):
    temp = data / 'temp'
    temp.mkdir(parents=True, exist_ok=True)
    for key in ('TEMP', 'TMP', 'TMPDIR'):
        os.environ[key] = str(temp)
    tempfile.tempdir = str(temp)
    os.environ['BRIDGE_SQLITE_PATH'] = str(data / 'incidents.db')
    if os.name == 'nt':
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(data / 'browser-cache')

def checked_firmware(source, data):
    """Stage bundled ROMs in the VM working directory, with ASCII QEMU arguments."""
    expected = {
        'bios-256k.bin': 'ae6f6aa973aaccc143f57aa960fb035fd9de4daee4ad0cd713322f8c259e7650',
        'kvmvapic.bin': 'cdf057a71b07e3b52b19cbe210bdefa59250d01a9810b960f7fe1f98eed95a27',
        'linuxboot_dma.bin': '9c49e255340c78fc12e54ed043462bca02fb7fca29b7cfab62ff88a5344b6950',
        'multiboot_dma.bin': '46def003b1c413cc4a5880d858ca54456ad6978d5fdb6e1b7d086b2905a66df0',
        'pvh.bin': '918ff03a118615c2f4611a5df2d9496aabe099b5cfaaf69e544ebf973305d59d',
    }
    relative = Path('firmware')
    target = data / relative
    target.mkdir(exist_ok=True)
    for name, digest in expected.items():
        try:
            payload = (source / name).read_bytes()
        except OSError as exc:
            raise RuntimeError(f'펌웨어 {name} 읽기 실패. 패치의 server 폴더를 실행 파일 옆에 덮어쓰세요.') from exc
        if hashlib.sha256(payload).hexdigest() != digest:
            raise RuntimeError(f'펌웨어 {name} 무결성 검사 실패. 패치의 server 폴더를 다시 덮어쓰세요.')
        temp = target / (name + '.tmp')
        temp.write_bytes(payload)
        os.replace(temp, target / name)
    return target

def vm_error(code, log_path):
    with log_path.open('rb') as stream:
        stream.seek(max(0, log_path.stat().st_size - 4096))
        tail = stream.read().decode('utf-8', 'replace').strip().splitlines()
    detail = tail[-1] if tail else 'QEMU 오류 로그가 비어 있습니다.'
    return RuntimeError(f'내부 서버 종료 (코드 {code})\n{detail}\n{log_path}')

def qemu_command(binary, share, disk, serial, qmp_port, memory=2048):
    # JSON block paths also handle commas, spaces and Korean Windows usernames.
    return [str(binary),'-L',str(share),'-bios','bios-256k.bin',
        '-machine','pc-i440fx-8.2','-accel','tcg,thread=multi','-cpu','max','-smp','2','-m',str(memory),
        '-blockdev',json.dumps({'driver':'file','filename':str(disk),'node-name':'diskfile'}),
        '-blockdev',json.dumps({'driver':'qcow2','file':'diskfile','node-name':'serverdisk'}),
        '-device','virtio-blk-pci,drive=serverdisk',
        '-netdev','user,id=localnet,hostfwd=tcp:127.0.0.1:8065-:8065',
        '-device','virtio-net-pci,netdev=localnet,romfile=',
        '-object','rng-builtin,id=rng0','-device','virtio-rng-pci,rng=rng0',
        '-vga','none','-display','none','-serial','file:'+str(serial),'-monitor','none',
        '-qmp',f'tcp:127.0.0.1:{qmp_port},server=on,wait=off','-no-reboot']

def power_down(port):
    with socket.create_connection(('127.0.0.1',port),timeout=5) as conn:
        conn.settimeout(5); stream=conn.makefile('rwb',buffering=0)
        json.loads(stream.readline())
        for command in ('qmp_capabilities','system_powerdown'):
            stream.write((json.dumps({'execute':command,'id':command})+'\r\n').encode())
            while True:
                result=json.loads(stream.readline())
                if result.get('id')==command:
                    if 'error' in result: raise RuntimeError('QMP shutdown rejected')
                    break

class Launcher:
    def __init__(self, data, vm_options=None):
        self.data=Path(data).resolve(); self.data.mkdir(parents=True,exist_ok=True)
        (self.data/'logs').mkdir(exist_ok=True)
        self.token=secrets.token_urlsafe(32); self.view_token=secrets.token_urlsafe(32); self.stop=threading.Event()
        self.phase='starting'; self.message='시연 환경을 확인하고 있습니다.'; self.error=''
        self.ready=False; self.bridge=None; self.vm=None; self.qmp_port=None
        self.viewers=[]; self.gate=threading.RLock()
        self.vm_options=vm_options or {}; self.start_time=time.time()
    def set_status(self,phase,message):
        self.phase=phase; self.message=message; logging.info('%s: %s',phase,message)
    def status(self):
        value={'phase':self.phase,'message':self.message,'error':self.error,'ready':self.ready,
               'elapsed':int(time.time()-self.start_time),'data_folder':str(self.data)}
        if not self.stop.is_set():
            value['accounts']=[{'username':n,'password':p} for n,p in DEMO_JUDGES.items()]
        if self.ready:
            value['links']={'mattermost':'http://127.0.0.1:8065/bridge-lab/channels/monitor'}
        return value
    def open_viewer(self):
        with self.gate:
            if self.stop.is_set():
                raise RuntimeError('종료 중입니다.')
            proc = open_demo_window('http://127.0.0.1:8764/#token=' + self.token, self.data)
            if isinstance(proc, subprocess.Popen):
                self.viewers.append(proc)

    def close_viewers(self):
        for proc in self.viewers:
            if proc.poll() is None:
                if os.name == 'nt':
                    subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/taskkill.exe'), '/PID', str(proc.pid), '/T', '/F'],
                                   capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    proc.terminate()
                proc.wait(timeout=10)

    def request_stop(self):
        with self.gate:
            self.ready = False
            self.stop.set()
            if self.bridge:
                self.bridge.a.stop.set()

    def submit(self, body):
        if not isinstance(body, dict) or set(body) != {'source', 'request_id'}:
            raise ValueError('원본과 요청 ID를 확인하세요.')
        source, rid = body['source'], body['request_id']
        if source not in ('polestar', 'jobpass') or not isinstance(rid, str):
            raise ValueError('원본과 요청 ID를 확인하세요.')
        parsed = uuid.UUID(rid)
        if parsed.version != 4 or str(parsed) != rid:
            raise ValueError('요청 ID 형식을 확인하세요.')
        with self.gate:
            if not self.ready or self.stop.is_set() or not self.bridge:
                raise RuntimeError('준비 완료 후 다시 시도하세요.')
            self.bridge.a.add_demo(source, direct=False, request_id=rid)
            return {'event_id': rid, 'source': source}

    def prepare_disk(self):
        disk=self.data/'server.qcow2'
        if disk.exists():
            raise RuntimeError('새 회차에 기존 쓰기 디스크가 있습니다. 시작을 중단합니다.')
        manifest=json.loads((ROOT/'server/image-manifest.json').read_text('utf-8'))
        source=ROOT/'server/base.qcow2'
        sources=[source] if source.is_file() else [ROOT/'server'/name for name in manifest.get('parts',[])]
        if not sources or any(not p.is_file() for p in sources):
            raise RuntimeError('서버 이미지가 부족합니다. monitoringbot-part1.zip과 monitoringbot-part2.zip을 같은 폴더에 풀거나 monitoringbot.zip을 사용하세요.')
        if shutil.disk_usage(self.data).free < sum(p.stat().st_size for p in sources)+2*1024**3:
            raise RuntimeError('시연 데이터를 저장할 여유 공간이 부족합니다. 최소 이미지 크기 + 2GB가 필요합니다.')
        self.set_status('copying','이번 시연의 독립 디스크를 만들고 초기 이미지를 검증하고 있습니다.')
        temp=disk.with_suffix('.copying'); digest=hashlib.sha256()
        try:
            with temp.open('wb') as out:
                for part in sources:
                    with part.open('rb') as inp:
                        while True:
                            block=inp.read(4*1024*1024)
                            if not block:break
                            if self.stop.is_set(): raise InterruptedError('시작을 취소했습니다.')
                            digest.update(block);out.write(block)
            if not hmac.compare_digest(digest.hexdigest(),manifest['sha256']): raise RuntimeError('서버 이미지 검증 실패: 배포 ZIP을 다시 받아 압축 해제하세요.')
            os.replace(temp,disk)
        finally: temp.unlink(missing_ok=True)
        return disk
    def start(self):
        try:
            channel = browser_channel()
            share = Path(self.vm_options.get('share', ROOT / 'server/qemu/share'))
            firmware_dir = checked_firmware(share, self.data)
            disk = self.prepare_disk()
            if self.stop.is_set():return
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0));self.qmp_port=probe.getsockname()[1]
            binary=self.vm_options.get('binary',ROOT/'server/qemu/qemu-system-x86_64.exe')
            cmd = qemu_command(Path(binary).resolve(), Path('.'), Path('..') / disk.name,
                               Path('../logs/server-console.log'), self.qmp_port)
            log_path = self.data / 'logs/qemu.log'
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            with log_path.open('wb') as log:
                self.vm = subprocess.Popen(cmd, cwd=str(firmware_dir), stdout=log, stderr=log,
                                           env=self.vm_options.get('env'), creationflags=flags)
            self.set_status('booting','PC 내부의 Mattermost와 PostgreSQL을 시작하고 있습니다. 첫 시작에는 수 분 걸릴 수 있습니다.')
            deadline=time.monotonic()+900
            while True:
                if self.stop.is_set():return
                if self.vm.poll() is not None: raise vm_error(self.vm.returncode, log_path)
                try:
                    with HTTP.open('http://127.0.0.1:8065/api/v4/system/ping',timeout=3) as response:
                        if json.load(response).get('status')=='OK':break
                except (OSError,ValueError):pass
                if time.monotonic()>deadline:raise RuntimeError('서버 준비가 15분 안에 끝나지 않았습니다. 메모리와 logs/server-console.log를 확인하세요.')
                self.stop.wait(2)
            self.set_status('provisioning','심사 계정, 업무 채널, 관제팀 채널을 준비하고 있습니다.')
            from provision import prepare,API
            account_file=self.data/'accounts.json'
            if account_file.exists(): admin_password=json.loads(account_file.read_text('utf-8'))['contest_admin']
            else:
                admin_password='Demo!A'+secrets.token_hex(10)
                atomic_json(account_file,{'contest_admin':admin_password})
            os.environ['MM_ADMIN_PASSWORD']=admin_password
            os.environ['BRIDGE_CALLBACK_BASE']='http://10.0.2.2:8767'
            os.environ['MATTERMOST_ORIGIN']='http://127.0.0.1:8065'
            settings, _ = prepare(self.data, API('http://127.0.0.1:8065'), portable=True)
            settings['source_origin']='http://127.0.0.1:8780'
            config=json.loads((self.data/'config.json').read_text('utf-8'))
            config.update(hosted=True,portable=True,persist_demo=True,callback_host='127.0.0.1')
            for collector in config['collectors'].values():collector['browser_channel']=channel
            atomic_json(self.data/'config.json',config)
            if self.stop.is_set():return
            from hosted import RunningBridge
            self.bridge=RunningBridge(self.data, settings, '127.0.0.1')
            self.set_status('collectors','폴스타·잡패스 화면 수집기를 시작하고 있습니다.')
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                if self.stop.is_set():return
                names={name for name,c in self.bridge.a.config['collectors'].items() if c['enabled']}
                states=[s for s in self.bridge.a.store.snapshot()['collector_state'] if s['collector_id'] in names]
                if len(states)==len(names) and all(s['health_status']=='OK' for s in states):break
                if any(s['health_status']=='STOPPED' for s in states):raise RuntimeError('화면 수집 브라우저 시작 실패: Edge/Chrome 실행 가능 여부와 보안 정책을 확인하세요.')
                self.stop.wait(1)
            else:raise RuntimeError('화면 수집기가 준비되지 않았습니다. logs/launcher.log를 확인하세요.')
            with self.gate:
                if self.stop.is_set(): return
                self.ready=True;self.set_status('ready','준비 완료. 이 PC 안에서 모든 시연 서비스를 실행 중입니다.')
            while not self.stop.wait(2):
                if self.vm.poll() is not None: raise vm_error(self.vm.returncode, log_path)
        except InterruptedError as exc:
            if not self.stop.is_set():
                self.error=str(exc)
        except Exception as exc:
            self.error=str(exc);logging.exception('Local startup/runtime failed')
        finally:
            self.request_stop()
            self.set_status('stopping','서버를 종료하고 있습니다. 잠시 기다려 주세요.')
            if self.bridge:
                try: self.bridge.close()
                except Exception as exc: self.error = (self.error + '\n' + str(exc)).strip()
            if self.vm and self.vm.poll() is None:
                try:
                    power_down(self.qmp_port);self.vm.wait(timeout=120)
                except Exception:
                    logging.exception('Guest shutdown timeout')
                    self.error=(self.error+'\n정상 종료 시간이 초과되어 서버를 강제 종료했습니다. 이번 회차는 폐기합니다.').strip()
                    self.vm.terminate()
                    try:self.vm.wait(timeout=15)
                    except subprocess.TimeoutExpired:self.vm.kill();self.vm.wait()
            self.set_status('cleanup', '이번 회차의 임시 자료를 정리하고 있습니다.')
class Control(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self, code, data, mime='application/json; charset=utf-8', *, view=False, grant=False):
        if not isinstance(data,bytes):
            data = json.dumps(data,ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','SAMEORIGIN' if view else 'DENY')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Cross-Origin-Resource-Policy','same-origin')
        if view:
            self.send_header('Content-Security-Policy',"frame-ancestors 'self'; base-uri 'none'; object-src 'none'")
        if grant:
            self.send_header('Set-Cookie','IBSourceView='+self.server.launcher.view_token+
                             '; Path=/operator-sources/; HttpOnly; SameSite=Strict')
        self.end_headers()
        self.wfile.write(data)

    def serve_operator_view(self):
        launcher = self.server.launcher
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie',''))
        except CookieError:
            pass
        value = cookies.get('IBSourceView')
        if (not launcher.ready or launcher.stop.is_set() or not value or not launcher.view_token
                or not hmac.compare_digest(value.value.encode(), launcher.view_token.encode())):
            return self.send(403, '이번 회차의 운영자 웹에서 화면을 다시 여세요.'.encode(),
                             'text/plain; charset=utf-8',view=True)
        from app import ASSETS, replica_page
        web = ASSETS / 'web'
        name = self.path[len('/operator-sources/'):]
        if name == 'both':
            page=(web/'sources.html').read_bytes().replace(b'/ib-sources/',b'/operator-sources/')
            return self.send(200,page,'text/html; charset=utf-8',view=True)
        if name in ('polestar','jobpass'):
            return self.send(200,replica_page(name),'text/html; charset=utf-8',view=True)
        if name == 'source-ui.js':
            return self.send(200,(web/name).read_bytes(),'text/javascript; charset=utf-8',view=True)
        if name in ('data/polestar','data/jobpass'):
            app = launcher.bridge.a
            with app.mock_lock:
                data = list(app.mock[name.split('/')[1]])
            return self.send(200,data,view=True)
        return self.send(404,{},view=True)

    def authorized(self):
        token = self.server.launcher.token
        return bool(token) and self.headers.get('Host') == '127.0.0.1:8764' and hmac.compare_digest(
            self.headers.get('X-Launcher-Token', '').encode('utf-8'), token.encode('ascii'))
    def do_GET(self):
        if self.headers.get('Host') != '127.0.0.1:8764':return self.send(403, {})
        if self.path=='/':self.send(200,(ROOT/'web_launcher/index.html').read_bytes(),'text/html; charset=utf-8')
        elif self.path=='/operator.js':self.send(200,(ROOT/'web_launcher/operator.js').read_bytes(),'text/javascript; charset=utf-8')
        elif self.path=='/api/status' and self.authorized():
            status = self.server.launcher.status()
            self.send(200,status,grant=status['ready'])
        elif self.path.startswith('/operator-sources/'):
            self.serve_operator_view()
        else:self.send(403,{'error':'실행기를 다시 열어 연결하세요.'})
    def do_POST(self):
        from bridge.engine import Conflict
        if not self.authorized() or self.headers.get('Origin') != 'http://127.0.0.1:8764':
            return self.send(403, {'error':'운영자 창에서 다시 연결하세요.'})
        launcher = self.server.launcher
        try:
            if self.path == '/api/stop':
                launcher.request_stop()
                return self.send(200, {'ok': True})
            if self.path == '/api/reopen':
                launcher.open_viewer()
                return self.send(200, {'ok': True})
            if self.path != '/api/operator/events':
                return self.send(404, {})
            if self.headers.get_content_type() != 'application/json':
                return self.send(415, {'error':'JSON 요청이 필요합니다.'})
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1024:
                raise ValueError('요청 크기를 확인하세요.')
            result = launcher.submit(json.loads(self.rfile.read(size)))
            return self.send(200, result)
        except Conflict as exc:
            return self.send(409, {'error':str(exc)})
        except (ValueError, TypeError):
            return self.send(400, {'error':'원본 또는 요청 ID가 올바르지 않습니다.'})
        except (RuntimeError, OSError) as exc:
            return self.send(503, {'error':str(exc)})


def run(no_browser=False):
    from logging.handlers import RotatingFileHandler
    sessions = Sessions(ROOT)
    lock_path = sessions.work / 'launcher.lock'
    plain(lock_path)
    try:
        lock = claim_lock(lock_path)
    except OSError:
        url = sessions.active_url()
        if not no_browser:
            request = urllib.request.Request('http://127.0.0.1:8764/api/reopen', data=b'',
                headers={'Origin':'http://127.0.0.1:8764', 'X-Launcher-Token':url.split('#token=')[1]})
            with HTTP.open(request, timeout=8):
                pass
        return False

    with lock:
        check_ports()
        sessions.clear_previous()
        contain_children()
        data = sessions.create()
        server = None
        launcher = None
        worker = None
        log_handler = None
        failures = []
        try:
            configure_storage(data)
            launcher = Launcher(data)
            log_handler = RotatingFileHandler(data/'logs/launcher.log', maxBytes=2000000,
                                              backupCount=2, encoding='utf-8')
            logging.getLogger().setLevel(logging.INFO)
            logging.getLogger().addHandler(log_handler)
            server = http.server.ThreadingHTTPServer(('127.0.0.1',8764),Control)
            server.launcher = launcher
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            atomic_json(data/'launch.json', {'pid':os.getpid(), 'url':'http://127.0.0.1:8764/#token='+launcher.token})
            if not no_browser:
                launcher.open_viewer()
            launcher.start()
            if launcher.error:
                failures.append(launcher.error)
        finally:
            if launcher:
                launcher.request_stop()
                launcher.token = ''
                launcher.view_token = ''
            if server:
                if worker and worker.is_alive():
                    server.shutdown()
                server.server_close()
            if launcher:
                try:
                    launcher.close_viewers()
                except (OSError, subprocess.SubprocessError) as exc:
                    failures.append(str(exc))
            if log_handler:
                logging.getLogger().removeHandler(log_handler)
                log_handler.close()
            sessions.remove(data)
        if failures:
            raise RuntimeError('\n'.join(failures))
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        ended = run(args.no_browser)
        if ended and os.name == 'nt' and not args.no_browser:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, '서버를 종료하고 이번 시연 자료를 폐기했습니다.', 'IncidentBridge', 0x40)
    except Exception as exc:
        if os.name != 'nt' or args.no_browser:
            raise
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(exc), 'IncidentBridge 실행 또는 정리 오류', 0x10)


if __name__ == '__main__':
    main()
