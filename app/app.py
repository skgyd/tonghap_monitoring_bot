"""Incident Bridge: SQLite-backed screen-to-Mattermost bridge."""
import argparse,copy,hmac,ipaddress,json,logging,os,secrets,shutil,socket,ssl,sys,threading,time,webbrowser
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from datetime import datetime,timezone,timedelta
from logging.handlers import RotatingFileHandler
from bridge.store import Store,StoreError,now,uid,row
from bridge.engine import Engine,Conflict,Forbidden
from bridge.mattermost import Mattermost,Dispatcher
from bridge.collector import Collector
from bridge.activity import ActivitySync
from html import escape
from demo_events import BUSINESSES, OPERATIONS, business_path, make_event

ASSETS=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))

def running_job_rows():
    rows = []
    for number, business in enumerate(BUSINESSES, 1):
        op = escape(OPERATIONS[business])
        path = escape(business_path('jobpass', business))
        css, state = ('long', '실행중(장기실행)') if number <= 2 else ('ok', '실행중')
        rows.append(f'<tr><td>{number}</td><td>{path}</td><td>{escape(business)}_{op}</td>'
                    f'<td>{op}</td><td class="{css}">⊙ {state}</td></tr>')
    return ''.join(rows).encode('utf-8')


def replica_page(source):
 titles = {'polestar':'폴스타 알람콘솔', 'jobpass':'잡패스 작업모니터'}
 title = titles[source]
 web = ASSETS / 'web'
 template = (web / 'replica.html').read_bytes()
 panel = (web / (source + '-panel.html')).read_bytes()
 if source == 'jobpass':
  panel = panel.replace(b'<!--RUNNING-JOBS-->', running_job_rows())
  panel = panel.replace(b'<!--RUNNING-COUNT-->', str(len(BUSINESSES)).encode('ascii'))
 return (template.replace(b'<!--TITLE-->', title.encode())
         .replace(b'SOURCE_ID', source.encode())
         .replace(b'<!--SOURCE-PANEL-->', panel))

def validate_config(c):
 if c['mode'] not in ['demo','live']:raise ValueError('mode must be demo/live')
 for k in ['poll_seconds','absence_confirmations','unack_seconds','inprogress_seconds','capture_retention_days','min_free_mb']:
  if not isinstance(c[k],int) or c[k]<1:raise ValueError(f'{k}: positive integer required')
 if 'callback_port' in c and (not isinstance(c['callback_port'],int) or not 1024<=c['callback_port']<=65535 or c['callback_port'] in (8764,c['port'])):raise ValueError('Callback port must be 1024..65535 and separate from local management')
 if not isinstance(c['port'],int) or not 1024<=c['port']<=65535:raise ValueError('port: 1024..65535')
 if c['mode']=='demo' and c['host'] not in ['127.0.0.1','localhost']:raise ValueError('Demo binds to localhost only')
 if c.get('local_lab') and c['host'] not in ['127.0.0.1','localhost']:raise ValueError('Local lab must bind loopback')
 if c['mode']=='live':
  m=c['mattermost']
  for k in ['url','callback_base']:
   u=urlsplit(m[k])
   if (u.scheme!='https' and not (c.get('trusted_lan_http') and u.scheme=='http')) or not u.hostname or u.username or u.password:raise ValueError(f'{k}: HTTPS URL required')
  if not m['monitor_channel'] or not m['allowed_user_ids'] or not m['callback_allowed_ips']:raise ValueError('Configure monitor channel, authorized users and callback IP allowlist')
  for address in m['callback_allowed_ips']:ipaddress.ip_network(address)
 for name,s in c['collectors'].items():
  if s['enabled']:
   u=urlsplit(s.get('url',''))
   if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:raise ValueError(name+': invalid screen URL')
   if not s.get('business_delimiter','/') or not isinstance(s.get('business_segment',-1),int):raise ValueError(name+': invalid business path split')
   for key in ['url','ready_selector','row_selector']:
    if not s.get(key):raise ValueError(f'{name}.{key} required')
   if not s.get('id_attribute') and not s.get('id_selector') and not s.get('event_key_fields'):raise ValueError(f'{name}: stable event identifier required')
   if name=='jobpass':
    for key in ['detail_selector','detail_identity_selector']:
     if not s.get(key):raise ValueError(f'jobpass.{key} required')

class Application:
 def __init__(self,root):
  self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
  for folder in ['captures','logs','profiles']: (self.root/folder).mkdir(exist_ok=True)
  config_path=self.root/'config.json'
  if not config_path.exists():shutil.copyfile(ASSETS/'config.example.json',config_path)
  self.config=json.loads(config_path.read_text('utf-8'));validate_config(self.config)
  self.token_path=self.root/'admin-token.txt'
  if not self.token_path.exists():self.token_path.write_text(secrets.token_urlsafe(32),encoding='utf-8');self.token_path.chmod(0o600)
  self.token=self.token_path.read_text('utf-8').strip()
  self.store=Store(os.environ.get('BRIDGE_SQLITE_PATH',str(self.root/'incidents.db')))
  saved_mode=next((r['value'] for r in self.store.snapshot()['meta'] if r['key']=='mode'),None)
  if saved_mode and saved_mode!=self.config['mode'] and self.store.snapshot()['incidents']:
   raise ValueError('Do not mix demo/live records. Start with a separate database and --data folder.')
  def set_mode(data):
   data['meta']=[r for r in data['meta'] if r['key']!='mode']+[row('meta',key='mode',value=self.config['mode'])]
  self.store.transaction(set_mode)
  if self.config['mode']=='live' and any(str(r['channel_id']).startswith('demo-') for r in self.store.snapshot()['routes']):
   raise ValueError('Replace demo channel routes before enabling live mode.')
  self.engine=Engine(self.store,self.config)
  self.mm=Mattermost(self.config['mattermost'],self.root,self.token)
  self.dispatcher=Dispatcher(self.engine,self.mm,self.root,self.config['mode'])
  self.activity=ActivitySync(self.engine,self.mm,self.config['mode'])
  self.stop=threading.Event();self.worker_error='';self.mock_lock=threading.Lock();self.mock={'polestar':[],'jobpass':[]}
  self.issued={}
  if (self.config.get('hosted') or self.config.get('persist_demo')) and (self.root/'demo-events.json').exists():
   saved=json.loads((self.root/'demo-events.json').read_text('utf-8'))
   self.mock={name:saved.get(name,[]) for name in self.mock}
   self.issued=saved.get('_issued',{})
   for source,events in self.mock.items():
    for event in events:
     if event.get('event_id'):self.issued.setdefault(event['event_id'],source)
  self.last_maintenance=0
  if self.config['mode']=='demo' and not self.store.snapshot()['routes']:
   self.store.transaction(lambda d:d['routes'].extend([row('routes',route_id=uid(),source=s,target_key='*',channel_id='demo-'+s,enabled=True) for s in ['polestar','jobpass']]))
 def activity_background(self):
  while not self.stop.is_set():
   try:self.activity.tick()
   except Exception:logging.exception('activity synchronization failed');self.activity.error='답글·이모지 DB/통신 실패: 재시도 대기'
   self.stop.wait(3)
 def background(self):
  while not self.stop.is_set():
   try:
    self.engine.reminders();self.dispatcher.tick()
    if time.time()-self.last_maintenance>60:self.maintenance();self.last_maintenance=time.time()
    self.worker_error=''
   except Exception as exc:
    self.worker_error=f'{type(exc).__name__}: DB 연결·디스크·설정을 확인하세요.';logging.exception('background failed')
   self.stop.wait(2)
 def maintenance(self):
  if shutil.disk_usage(self.root).free<self.config['min_free_mb']*1024**2:raise OSError('Low disk space')
  # Only completed incidents' old images; retain the metadata as audit evidence.
  data=self.store.snapshot();completed={i['incident_id'] for i in data['incidents'] if i['workflow_status']=='COMPLETED' and i['completed_at'] and (datetime.now(timezone.utc)-datetime.fromisoformat(i['completed_at'])).days>=self.config['capture_retention_days']}
  for cap in data['captures']:
   if cap['incident_id'] in completed:
    p=(self.root/cap['file_path']).resolve()
    if p.is_relative_to((self.root/'captures').resolve()):p.unlink(missing_ok=True)
 def add_demo(self,source,direct=True,request_id=None):
  if self.config['mode']!='demo' and not self.config.get('local_lab'):raise Forbidden('Demo only')
  if source not in self.mock:raise ValueError('Unknown source')
  if self.config.get('local_lab') and self.config['collectors'][source]['enabled']:direct=False
  with self.mock_lock:
   if request_id:
    if request_id in self.issued:
     if self.issued[request_id]!=source:raise Conflict('다른 원본에 사용한 요청 번호입니다.')
     return {'event_id':request_id}
    if len(self.issued)>=1000 or sum(len(rows) for rows in self.mock.values())>=1000:
     raise Conflict('시연 오류 1,000건 한도에 도달했습니다.')
   events=self.mock[source]
   previous_severity=events[-1].get('severity') if events else None
   first_for_source=not events and source not in self.issued.values()
   event_id=request_id or uid()
   event=make_event(source,event_id,now(),previous_severity=previous_severity,first_for_source=first_for_source)
   self.issued[event_id]=source
   self.mock[source].append(event)
   try:self.save_demo_events()
   except OSError:
    self.mock[source].pop()
    self.issued.pop(event_id,None)
    raise
  if direct:
   rel=f'captures/{uid()}.png';shutil.copyfile(ASSETS/'web'/f'{source}_demo.png',self.root/rel)
   return self.engine.ingest(source,event['event_id'],event['job_name'],event['message'],rel,'DETAIL' if source=='jobpass' else 'LIST')
  return event
 def save_demo_events(self):
  # Caller holds mock_lock; persist source rows so a restart is not a false recovery.
  if self.config.get('hosted') or self.config.get('persist_demo'):
   target=self.root/'demo-events.json';temp=target.with_suffix('.tmp')
   temp.write_text(json.dumps(dict(self.mock,_issued=self.issued),ensure_ascii=False),'utf-8');os.replace(temp,target)
 def state(self):
  d=self.store.snapshot();d['mode']=self.config['mode'];d['errors']=[x for x in [self.store.error,self.worker_error,self.activity.error] if x];d['simulated_failure']=self.dispatcher.simulate_failure
  d['local_lab']=bool(self.config.get('local_lab'));d['hosted']=bool(self.config.get('hosted'));d['portable']=bool(self.config.get('portable'));d['heartbeat']=now();return d

class Server(ThreadingHTTPServer):daemon_threads=True
class Handler(BaseHTTPRequestHandler):
 server_version='IncidentBridge/1.0'
 def log_message(self,*args):pass # do not log query tokens or callback payloads
 @property
 def a(self):return self.server.application
 def send(self,status,body,ctype='application/json; charset=utf-8',extra=None):
  if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False).encode()
  self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','SAMEORIGIN' if urlsplit(self.path).path.startswith(('/mock/', '/ib-sources/')) else 'DENY')
  if extra:
   for k,v in extra.items():self.send_header(k,v)
  self.end_headers();self.wfile.write(body)
 def local(self):return ipaddress.ip_address(self.client_address[0]).is_loopback
 def auth(self):
  # Management stays local even when callback listener is exposed.
  if not self.local():raise Forbidden('관리 화면은 실행 PC에서만 접근 가능합니다.')
  token=self.headers.get('X-Bridge-Token','')
  if not token:
   cookies=dict(pair.strip().split('=',1) for pair in self.headers.get('Cookie','').split(';') if '=' in pair);token=cookies.get('bridge_admin','')
  if not hmac.compare_digest(token,self.a.token):raise Forbidden('관리 토큰을 입력하세요.')
 def body(self):
  size=int(self.headers.get('Content-Length','0'))
  if size<0 or size>262144:raise ValueError('Request too large')
  return json.loads(self.rfile.read(size) or '{}')
 def do_GET(self):
  path=urlsplit(self.path).path
  try:
   if path=='/health':
    states={r['collector_id']:r['health_status'] for r in self.a.store.snapshot()['collector_state']}
    active={name:states.get(name,'STARTING') for name,c in self.a.config['collectors'].items() if c['enabled']}
    healthy=not(self.a.store.error or self.a.worker_error or self.a.activity.error) and all(value=='OK' for value in active.values())
    self.send(200 if healthy else 503,{'status':'ok' if healthy else 'degraded','collectors':active,'time':now()});return
   if path in ['/','/app.js','/style.css']:
    if not self.local():raise Forbidden('Local management only')
    name={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[path];mime={'/':'text/html; charset=utf-8','/app.js':'text/javascript; charset=utf-8','/style.css':'text/css; charset=utf-8'}[path]
    self.send(200,(ASSETS/'web'/name).read_bytes(),mime);return
   if path in ['/wall','/sql']:
    if path=='/wall' and self.a.config['mode']=='live' and not self.a.config.get('local_lab'):raise Forbidden('Demo only')
    if not self.local():raise Forbidden('Local only')
    self.send(200,(ASSETS/'web'/('sql.html' if path=='/sql' else 'wall.html')).read_bytes(),'text/html; charset=utf-8');return
   if path.startswith('/mock/'):
    if not self.local() or (self.a.config['mode']!='demo' and not self.a.config.get('local_lab')):raise Forbidden('Demo only')
    source=path.rsplit('/',1)[-1]
    if path=='/mock/source-ui.js':return self.send(200,(ASSETS/'web'/source).read_bytes(),'text/javascript; charset=utf-8')
    if source not in self.a.mock:raise ValueError('Unknown source')
    self.send(200,replica_page(source),'text/html; charset=utf-8');return
   if path.startswith('/mock-data/'):
    if not self.local() or (self.a.config['mode']!='demo' and not self.a.config.get('local_lab')):raise Forbidden('Demo only')
    with self.a.mock_lock:result=copy.deepcopy(self.a.mock[path.rsplit('/',1)[-1]])
    self.send(200,result);return
   self.auth()
   if path=='/api/state':self.send(200,self.a.state())
   elif path=='/api/config':self.send(200,self.a.config)
   elif path.startswith('/api/db/'):
    self.send(200,self.a.store.query(path.rsplit('/',1)[-1]))
   elif path.startswith('/capture/'):
    cap=self.a.store.row('captures',path.rsplit('/',1)[-1])
    if not cap:raise ValueError('Unknown capture')
    file=(self.a.root/cap['file_path']).resolve()
    if not file.is_relative_to((self.a.root/'captures').resolve()):raise Forbidden('Invalid path')
    self.send(200,file.read_bytes(),'image/png')
   else:self.send(404,{'error':'Not found'})
  except Forbidden as ex:self.send(403,{'error':str(ex)})
  except StoreError as ex:self.send(503,{'error':str(ex)})
  except FileNotFoundError:self.send(404,{'error':'파일 없음 또는 보존 기간 경과'})
  except Exception as ex:self.send(400,{'error':str(ex)})
 def do_POST(self):
  path=urlsplit(self.path).path
  try:
   body=self.body()
   if path=='/mattermost/action':
    if self.a.config['mode']!='live':raise Forbidden('Live only')
    networks=self.a.config['mattermost']['callback_allowed_ips']
    if not any(ipaddress.ip_address(self.client_address[0]) in ipaddress.ip_network(n) for n in networks):raise Forbidden('Callback source denied')
    d=self.a.store.row('deliveries',body.get('context',{}).get('delivery_id'))
    if not d:raise Forbidden('Unknown delivery')
    i=self.a.store.row('incidents',d['incident_id']);actor,action,rev=self.a.mm.authorize(body,d,i)
    item=self.a.engine.action(i['incident_id'],action,actor,rev,admin=actor in self.a.config['mattermost']['admin_user_ids'])
    # An interactive update without file_ids can detach existing images in Mattermost.
    # Acknowledge only; Dispatcher updates both posts with their persisted file_ids.
    self.send(200,{});return
   # Custom header is mandatory for all local mutations; cross-origin forms cannot set it.
   if not hmac.compare_digest(self.headers.get('X-Bridge-Token',''),self.a.token):raise Forbidden('관리 토큰이 필요합니다.')
   self.auth()
   if path=='/api/login':
    secure='; Secure' if isinstance(self.connection,ssl.SSLSocket) else ''
    self.send(200,{'ok':True},extra={'Set-Cookie':f'bridge_admin={self.a.token}; HttpOnly; SameSite=Strict; Path=/{secure}'});return
   if path=='/api/action':
    actor='LOCAL_ADMIN'
    if self.a.config.get('hosted'):
     actor=self.headers.get('X-Bridge-Actor','')
     if actor not in self.a.config['mattermost']['allowed_user_ids']:raise Forbidden('허용되지 않은 시연 계정입니다.')
    result=self.a.engine.action(body['incident_id'],body['action'],actor,body.get('revision'),body.get('note',''),True,body.get('channel',''));self.send(200,result)
   elif path=='/api/demo/add':self.send(200,self.a.add_demo(body['source'],body.get('direct',not self.a.config['collectors'][body['source']]['enabled'])))
   elif path=='/api/demo/chat-action':
    if self.a.config['mode']!='demo' and not self.a.config.get('local_lab'):raise Forbidden('Demo only')
    d=self.a.store.row('deliveries',body.get('delivery_id'))
    if not d:raise ValueError('Unknown delivery')
    i=self.a.store.row('incidents',d['incident_id'])
    if d['role']!='OWNER' or d['channel_id']!=i['channel_id'] or d['status']!='SENT' or int(d['sent_revision'])!=int(i['revision']):raise Conflict('모의 메시지 전송/갱신을 기다리세요.')
    actor=body.get('actor')
    if actor not in ['DEMO_OWNER_A','DEMO_OWNER_B']:raise Forbidden('Unknown demo actor')
    if body.get('action') not in ['ack','complete']:raise Forbidden('Invalid demo action')
    self.send(200,self.a.engine.action(i['incident_id'],body['action'],actor,body.get('revision'),admin=False))
   elif path=='/api/demo/remove':
    if self.a.config['mode']!='demo' and not self.a.config.get('local_lab'):raise Forbidden('Demo only')
    source=body['source']
    with self.a.mock_lock:
     self.a.mock[source]=[x for x in self.a.mock[source] if x['event_id']!=body['event_id']];seen={x['event_id'] for x in self.a.mock[source]}
     self.a.save_demo_events()
    self.a.engine.reconcile(source,seen,True);self.send(200,{'ok':True,'note':'두 번의 정상 조회 후 사라짐 확정'})
   elif path=='/api/demo/failure':
    if self.a.config['mode']!='demo' and not self.a.config.get('local_lab'):raise Forbidden('Demo only')
    self.a.dispatcher.simulate_failure=bool(body['enabled']);self.send(200,{'ok':True})
   elif path=='/api/routes':
    rows=body['routes']
    def op(d):d['routes']=[row('routes',route_id=r.get('route_id') or uid(),source=r['source'],target_key=r['target_key'],channel_id=r['channel_id'],enabled=bool(r.get('enabled',True))) for r in rows]
    self.a.store.transaction(op);self.send(200,{'ok':True})
   elif path=='/api/config':
    validate_config(body)
    target=self.a.root/'config.json';temp=target.with_suffix('.tmp');temp.write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,target)
    self.send(200,{'ok':True,'note':'설정 저장됨. 종료 후 재시작해야 적용됩니다.'})
   elif path=='/api/activity/retry':
    item=self.a.store.row('activity_mirrors',body['mirror_id'])
    if not item:raise ValueError('Unknown mirror')
    self.a.activity.update(item,status='PENDING',next_retry_at='',last_error='')
    self.send(200,{'ok':True})
   elif path=='/api/retry':
    did=body['delivery_id'];d=self.a.store.row('deliveries',did)
    if not d:raise ValueError('Unknown delivery')
    if d['status']=='MANUAL_REVIEW' and not body.get('reviewed'):raise ValueError('기존 게시 여부를 먼저 확인하고 reviewed=true로 요청하세요.')
    self.a.dispatcher.update(did,status='RETRY',next_retry_at='');self.send(200,{'ok':True})
   else:self.send(404,{'error':'Not found'})
  except (Forbidden,PermissionError) as ex:self.send(403,{'error':str(ex)})
  except Conflict as ex:self.send(409,{'error':str(ex)})
  except (StoreError,OSError) as ex:self.send(503,{'error':'저장/통신 실패: DB·디스크·네트워크 확인'})
  except Exception as ex:self.send(400,{'error':str(ex)})

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--data',default=str(Path(sys.executable).parent/'data' if getattr(sys,'frozen',False) else Path(__file__).parent/'data'));parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
 # OS-level exclusive lock released by process termination, including crashes.
 root=Path(args.data);root.mkdir(parents=True,exist_ok=True);lock=open(root/'instance.lock','a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
 try:
  if os.name=='nt':
   import msvcrt;msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
  else:
   import fcntl;fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
 except OSError:raise SystemExit('Another instance is using this data folder.')
 a=Application(root)
 handler=RotatingFileHandler(root/'logs'/'bridge.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8');logging.basicConfig(level=logging.INFO,handlers=[handler])
 server=Server((a.config['host'],a.config['port']),Handler);server.application=a
 cert=os.environ.get('BRIDGE_TLS_CERT');key=os.environ.get('BRIDGE_TLS_KEY')
 if cert and key:
  context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key);server.socket=context.wrap_socket(server.socket,server_side=True)
 elif a.config['host'] not in ['127.0.0.1','localhost']:raise SystemExit('External listener requires BRIDGE_TLS_CERT and BRIDGE_TLS_KEY. Or use loopback behind an approved TLS reverse proxy.')
 threading.Thread(target=a.background,daemon=True).start()
 if a.config['mode']=='live':threading.Thread(target=a.activity_background,daemon=True).start()
 collectors=[]
 for name,c in a.config['collectors'].items():
  if c['enabled']:
   collector=Collector(a.engine,a.root,name,c,a.stop);collector.start();collectors.append(collector)
 # A separate listener exposes only the authenticated Mattermost callback.
 callback_port=a.config.get('callback_port',8767)
 class CallbackHandler(Handler):
  def do_GET(self):self.send(404,{'error':'Not found'})
  def do_POST(self):
   if urlsplit(self.path).path!='/mattermost/action':self.send(404,{'error':'Not found'});return
   super().do_POST()
 callback=Server((a.config.get('callback_host','0.0.0.0'),callback_port),CallbackHandler);callback.application=a
 callback_cert=a.config.get('callback_cert');callback_key=a.config.get('callback_key')
 if callback_cert and callback_key:
  ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(callback_cert,callback_key);callback.socket=ctx.wrap_socket(callback.socket,server_side=True)
 elif not a.config.get('trusted_lan_http'):raise SystemExit('Callback TLS certificate/key required; or explicitly configure trusted LAN HTTP')
 threading.Thread(target=callback.serve_forever,daemon=True).start()
 url=f"http://127.0.0.1:{a.config['port']}"
 print('Incident Bridge 1.0 | '+a.config['mode']);print('Dashboard: '+url);print('Admin token file: '+str(a.token_path.resolve()));print('Stop: Ctrl+C')
 if not args.no_browser:webbrowser.open(url)
 try:server.serve_forever()
 except KeyboardInterrupt:pass
 finally:
  a.stop.set();server.server_close();callback.shutdown();callback.server_close()
  for collector in collectors:collector.join(timeout=12)
  lock.close()

if __name__=='__main__':main()
