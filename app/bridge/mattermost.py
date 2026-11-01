import hashlib,hmac,json,os,ssl,urllib.request,urllib.error,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from .store import now

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): return None

class Mattermost:
 def __init__(self,config,root,secret):
  self.c=config; self.root=Path(root); self.secret=secret
 def api(self,method,path,payload=None,raw=None,content_type=None):
  base=self.c['url'].rstrip('/')
  if not base: raise ValueError('Mattermost URL not configured')
  token=os.environ.get('BRIDGE_MM_TOKEN','')
  if not token: raise ValueError('BRIDGE_MM_TOKEN not configured')
  headers={'Authorization':'Bearer '+token,'Content-Type':content_type or 'application/json'}
  body=raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
  context=ssl.create_default_context(cafile=self.c.get('ca_file') or None)
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect,urllib.request.HTTPSHandler(context=context))
  req=urllib.request.Request(base+'/api/v4'+path,data=body,headers=headers,method=method)
  with opener.open(req,timeout=15) as response: return json.load(response)
 def sign(self,delivery_id,revision,action):
  return hmac.new(self.secret.encode(),f'{delivery_id}|{revision}|{action}'.encode(),hashlib.sha256).hexdigest()
 def payload(self,i,d,captures):
  state={'NEW':'미접수','IN_PROGRESS':'처리 중','COMPLETED':'담당자 처리 완료'}[i['workflow_status']]
  message=f"**[{i['incident_id']}] {i['source']} / {i['job_name']}**\n{i['message']}\n상태: {state} | 원본: {i['source_status']}\n접수자: {i['assignee_id'] or '-'} | 완료자: {i['completed_by'] or '-'}\n캡처: {i['capture_status']} | 감지: {i['detected_at']}"
  if d['role']=='PREVIOUS': message+='\n이 사건은 다른 담당 채널로 인계되었습니다.'
  if i['unmapped']: message+='\n⚠ 담당 매핑 없음'
  if i['last_reminded_at']: message+='\n⚠ 최근 지연 알림: '+i['last_reminded_at']
  actions=[]
  if d['role']=='OWNER' and d['channel_id']==i['channel_id']:
   for action,label in [('ack','접수')] if i['workflow_status']=='NEW' else [('complete','처리 완료')] if i['workflow_status']=='IN_PROGRESS' else []:
    context={'delivery_id':d['delivery_id'],'revision':i['revision'],'action':action,'signature':self.sign(d['delivery_id'],i['revision'],action)}
    actions.append({'id':action,'name':label,'type':'button','integration':{'url':self.c['callback_base'].rstrip('/')+'/mattermost/action','context':context}})
  return {'channel_id':d['channel_id'],'message':message,'props':{'bridge_delivery_id':d['delivery_id'],'attachments':[{'text':'담당 채널과 이력은 관제팀에서 /ib 도움을 입력하세요.','actions':actions}]}}
 def upload(self,path,channel):
  boundary='bridge'+uuid.uuid4().hex
  content=Path(path).read_bytes()
  body=(f'--{boundary}\r\nContent-Disposition: form-data; name="channel_id"\r\n\r\n{channel}\r\n--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="capture.png"\r\nContent-Type: image/png\r\n\r\n').encode()+content+f'\r\n--{boundary}--\r\n'.encode()
  return self.api('POST','/files',raw=body,content_type='multipart/form-data; boundary='+boundary)['file_infos'][0]['id']
 def find_post(self,d):
  # Only decide not-found after reaching posts older than this delivery.
  cutoff=datetime.fromisoformat(d['updated_at']).timestamp()*1000-60000
  for page in range(20):
   res=self.api('GET',f"/channels/{d['channel_id']}/posts?page={page}&per_page=100")
   posts=list(res.get('posts',{}).values())
   for p in posts:
    if p.get('props',{}).get('bridge_delivery_id')==d['delivery_id']:return p['id']
   if not posts or min(p.get('create_at',0) for p in posts)<cutoff:return ''
  raise ValueError('RECONCILE_LIMIT: manually reconcile before retry')
 def authorize(self,body,delivery,incident):
  c=body.get('context',{}); action=c.get('action',''); rev=c.get('revision',-1)
  if not hmac.compare_digest(str(c.get('signature','')),self.sign(delivery['delivery_id'],rev,action)):raise PermissionError('Invalid callback signature')
  if str(body.get('post_id',''))!=delivery['post_id'] or body.get('channel_id')!=delivery['channel_id']:raise PermissionError('Invalid post/channel')
  if delivery['role']!='OWNER' or delivery['channel_id']!=incident['channel_id']:raise PermissionError('Not current owner channel')
  actor=body.get('user_id','')
  if not actor or not actor.isalnum():raise PermissionError('Invalid actor')
  member=self.api('GET',f"/channels/{delivery['channel_id']}/members/{actor}")
  if member.get('user_id')!=actor:raise PermissionError('Not a member')
  allowed=self.c.get('allowed_user_ids',[])
  if actor not in allowed:raise PermissionError('User is not in allowed_user_ids')
  return actor,action,rev

 def upload_bytes(self,content,filename,content_type,channel):
  boundary='bridge'+uuid.uuid4().hex
  filename=Path(filename).name.replace('"','_').replace('\r','').replace('\n','') or 'attachment'
  content_type=content_type if '\n' not in content_type and '\r' not in content_type else 'application/octet-stream'
  body=(f'--{boundary}\r\nContent-Disposition: form-data; name="channel_id"\r\n\r\n{channel}\r\n--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n').encode()+content+f'\r\n--{boundary}--\r\n'.encode()
  return self.api('POST','/files',raw=body,content_type='multipart/form-data; boundary='+boundary)['file_infos'][0]['id']

 def copy_attachment(self,file_id,channel):
  info=self.api('GET','/files/'+file_id+'/info');limit=50*1024*1024
  if int(info.get('size',0))>limit:raise ValueError('REPLY_ATTACHMENT_OVER_50MB')
  context=ssl.create_default_context(cafile=self.c.get('ca_file') or None)
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect,urllib.request.HTTPSHandler(context=context))
  req=urllib.request.Request(self.c['url'].rstrip('/')+'/api/v4/files/'+file_id,headers={'Authorization':'Bearer '+os.environ['BRIDGE_MM_TOKEN']})
  with opener.open(req,timeout=30) as response:content=response.read(limit+1)
  if len(content)>limit:raise ValueError('REPLY_ATTACHMENT_OVER_50MB')
  return self.upload_bytes(content,info.get('name','attachment'),info.get('mime_type','application/octet-stream'),channel)

class Dispatcher:
 def __init__(self,engine,mm,root,mode='demo'):
  self.e=engine; self.store=engine.store; self.mm=mm; self.root=Path(root); self.mode=mode; self.simulate_failure=False
 def update(self,did,**values):
  def op(data):
   r=next(x for x in data['deliveries'] if x['delivery_id']==did); r.update(values)
  self.store.transaction(op)
 def tick(self):
  data=self.store.snapshot(); t=now()
  for d in data['deliveries']:
   i=next(x for x in data['incidents'] if x['incident_id']==d['incident_id'])
   if d['status']=='SENT' and int(d['sent_revision'])>=int(i['revision']):continue
   if d['next_retry_at'] and d['next_retry_at']>t:continue
   if d['status']=='MANUAL_REVIEW':continue
   try:
    if self.simulate_failure:raise ConnectionError('Simulated disconnection')
    if self.mode=='demo':
     self.update(d['delivery_id'],post_id=d['post_id'] or 'demo-'+d['delivery_id'],status='SENT',sent_revision=i['revision'],last_error='',next_retry_at='',updated_at=now()); continue
    post=d['post_id']
    if not post and d['status'] in ['SENDING','RETRY']:
     post=self.mm.find_post(d)
     if post:self.update(d['delivery_id'],post_id=post)
    payload=self.mm.payload(i,d,data['captures'])
    file_map=json.loads(d['file_ids'] or '{}')
    for cap in data['captures']:
     if cap['incident_id']!=i['incident_id'] or cap['capture_id'] in file_map:continue
     path=(self.root/cap['file_path']).resolve()
     if not path.is_relative_to(self.root.resolve()):raise ValueError('Invalid capture path')
     if path.exists():
      file_map[cap['capture_id']]=self.mm.upload(path,d['channel_id'])
      self.update(d['delivery_id'],file_ids=json.dumps(file_map))
    payload['file_ids']=list(file_map.values())[:5]
    if post:
     self.mm.api('PUT',f'/posts/{post}/patch',{k:v for k,v in payload.items() if k!='channel_id'})
    else:
     self.update(d['delivery_id'],status='SENDING',updated_at=now())
     post=self.mm.api('POST','/posts',payload)['id']
    self.update(d['delivery_id'],post_id=post,status='SENT',sent_revision=i['revision'],last_error='',next_retry_at='',updated_at=now())
   except Exception as ex:
    attempts=int(d['attempt_count'] or 0)+1
    error=f'{type(ex).__name__}: {ex}' if self.mode=='demo' else type(ex).__name__
    status='MANUAL_REVIEW' if 'RECONCILE_LIMIT' in str(ex) or isinstance(ex,urllib.error.HTTPError) and ex.code in [403,404] else 'RETRY'
    self.update(d['delivery_id'],status=status,attempt_count=attempts,last_error=error,next_retry_at=(datetime.now(timezone.utc)+timedelta(seconds=min(300,2**min(attempts,8)))).isoformat(timespec='seconds'))

