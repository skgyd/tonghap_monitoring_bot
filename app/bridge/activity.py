"""One-way, recoverable Mattermost thread/reaction mirroring; Python 3.9+.

Only registered incident threads are read. Original people are attributed in
text; all writes and native reactions belong to the bot, never to another user.
"""
import hashlib
import json
import time
import urllib.error
from datetime import datetime,timezone,timedelta
from urllib.parse import quote
from .store import now,row

class ActivitySync:
 def __init__(self,engine,mm,mode='live'):
  self.e=engine;self.store=engine.store;self.mm=mm;self.mode=mode
  self.bot_id='';self.error='';self.users={};self.channels={}

 def thread(self,post_id,channel_id):
  # v11.7 returns the complete thread with perPage=0. Refuse incomplete results.
  result=self.mm.api('GET','/posts/'+post_id+'/thread?perPage=0')
  posts=result.get('posts') if isinstance(result,dict) else None
  if not isinstance(posts,dict) or post_id not in posts or result.get('has_next'):
   raise ValueError('INCOMPLETE_THREAD: no deletion inferred')
  if len(posts)>2000:raise ValueError('THREAD_LIMIT_2000: manual review required')
  if any(p.get('channel_id')!=channel_id for p in posts.values()):raise ValueError('WRONG_THREAD_CHANNEL')
  if posts[post_id].get('delete_at'):raise ValueError('SOURCE_OR_TARGET_ROOT_DELETED')
  if any(k not in posts for k in result.get('order',[])):raise ValueError('INCOMPLETE_THREAD')
  return posts

 def label(self,user_id):
  cached=self.users.get(user_id)
  if cached and time.monotonic()-cached[0]<300:return cached[1]
  user=self.mm.api('GET','/users/'+user_id)
  name=user.get('username') or user_id
  display=(user.get('nickname') or ' '.join(x for x in (user.get('first_name'),user.get('last_name')) if x)).strip()
  value=(display+' ('+name+')') if display else name
  self.users[user_id]=(time.monotonic(),value)
  return value

 def channel_label(self,channel_id):
  if channel_id not in self.channels:self.channels[channel_id]=self.mm.api('GET','/channels/'+channel_id)['display_name']
  return self.channels[channel_id]

 def ensure(self,source,target,post_id,kind):
  mirror_id=hashlib.sha256((target['delivery_id']+'|'+post_id+'|'+kind).encode()).hexdigest()
  def op(data):
   found=next((r for r in data['activity_mirrors'] if r['mirror_id']==mirror_id),None)
   if found:return found
   item=row('activity_mirrors',mirror_id=mirror_id,incident_id=source['incident_id'],source_delivery_id=source['delivery_id'],target_delivery_id=target['delivery_id'],source_post_id=post_id,target_post_id=target['post_id'] if kind=='ROOT' else '',kind=kind,status='PENDING',source_updated_at=0,attempt_count=0,file_ids='{}',source_reactions='[]',updated_at=now())
   data['activity_mirrors'].append(item);return item
  return self.store.transaction(op)

 def update(self,item,**values):
  changed={k:v for k,v in values.items() if item.get(k)!=v}
  if not changed:return
  changed['updated_at']=now()
  def op(data):
   r=next(r for r in data['activity_mirrors'] if r['mirror_id']==item['mirror_id']);r.update(changed)
  self.store.transaction(op);item.update(changed)

 def due(self,item):
  return item['status']!='MANUAL_REVIEW' and (not item['next_retry_at'] or datetime.fromisoformat(item['next_retry_at'])<=datetime.now(timezone.utc))

 def failed(self,item,exc):
  attempts=int(item['attempt_count'])+1;code=exc.code if isinstance(exc,urllib.error.HTTPError) else ''
  manual=code in (400,403,404,413) or isinstance(exc,ValueError)
  error=type(exc).__name__+(' HTTP '+str(code) if code else (': '+str(exc) if isinstance(exc,ValueError) else ''))
  self.update(item,status='MANUAL_REVIEW' if manual else 'RETRY',attempt_count=attempts,last_error=error,next_retry_at=(datetime.now(timezone.utc)+timedelta(seconds=min(300,2**min(attempts,8)))).isoformat())

 def reactions(self,post_id):
  values=self.mm.api('GET','/posts/'+post_id+'/reactions')
  if values is None:values=[]
  if not isinstance(values,list):raise ValueError('INVALID_REACTIONS: no removal inferred')
  result=[]
  for r in values:
   if r.get('delete_at'):continue
   if r.get('post_id')!=post_id or not r.get('user_id') or not r.get('emoji_name'):raise ValueError('INVALID_REACTION')
   if r['user_id']==self.bot_id:continue
   result.append({'user_id':r['user_id'],'emoji_name':r['emoji_name'],'create_at':r.get('create_at',0)})
  return sorted(result,key=lambda r:(r['emoji_name'],r['user_id']))

 def reaction_text(self,reactions):
  groups={}
  for r in reactions:groups.setdefault(r['emoji_name'],set()).add(r['user_id'])
  if not groups:return ''
  lines=['**원문 이모지 반응 · 실제 인원**']
  for emoji,users in sorted(groups.items()):
   lines.append(':'+emoji+': **'+str(len(users))+'명** — '+', '.join(self.label(u) for u in sorted(users)))
  return '\n'.join(lines)+'\n\n※ 이곳의 반응 아이콘은 봇이 표시합니다. 원문 참여자와 인원은 위 목록입니다.'

 def sync_native(self,post_id,reactions):
  target=self.mm.api('GET','/posts/'+post_id+'/reactions') or []
  if not isinstance(target,list):raise ValueError('INVALID_TARGET_REACTIONS')
  actual={r['emoji_name'] for r in target if r.get('user_id')==self.bot_id and not r.get('delete_at')}
  desired={r['emoji_name'] for r in reactions}
  for emoji in sorted(desired-actual):self.mm.api('POST','/reactions',{'user_id':self.bot_id,'post_id':post_id,'emoji_name':emoji})
  for emoji in sorted(actual-desired):self.mm.api('DELETE','/users/'+self.bot_id+'/posts/'+post_id+'/reactions/'+quote(emoji,safe=''))

 def validate_target(self,item,post,target):
  if post.get('user_id')!=self.bot_id or post.get('channel_id')!=target['channel_id'] or post.get('root_id')!=target['post_id'] or post.get('props',{}).get('bridge_mirror_id')!=item['mirror_id']:
   raise ValueError('TARGET_OWNERSHIP_MISMATCH')

 def target_post(self,item,target,posts):
  if item['target_post_id']:
   post=posts.get(item['target_post_id'])
   if post is None:post=self.mm.api('GET','/posts/'+item['target_post_id'])
   self.validate_target(item,post,target)
   if post.get('delete_at'):raise ValueError('TARGET_POST_DELETED: manual review required')
   return post
  # Reconcile a successful POST whose response/SQL commit was lost.
  found=[p for p in posts.values() if not p.get('delete_at') and p.get('props',{}).get('bridge_mirror_id')==item['mirror_id']]
  if len(found)>1:raise ValueError('DUPLICATE_MIRROR: manual review required')
  if found:
   self.validate_target(item,found[0],target);self.update(item,target_post_id=found[0]['id']);return found[0]
  return None

 def remove(self,item,target,posts):
  if item['status']=='DELETED':return
  try:post=self.target_post(item,target,posts)
  except urllib.error.HTTPError as exc:
   if exc.code!=404:raise
   post=None
  if post:
   self.mm.api('DELETE','/posts/'+post['id']);posts.pop(post['id'],None)
  self.update(item,status='DELETED',target_post_id='',content_hash='',file_ids='{}',source_reactions='[]',last_error='',attempt_count=0,next_retry_at='')

 def write(self,item,target,posts,message,source_post,reactions):
  source_files=source_post.get('file_ids',[]) if item['kind']=='REPLY' else []
  files=json.loads(item['file_ids'] or '{}')
  for file_id in source_files:
   if file_id not in files:
    files[file_id]=self.mm.copy_attachment(file_id,target['channel_id']);self.update(item,file_ids=json.dumps(files,separators=(',',':'),sort_keys=True))
  payload={'channel_id':target['channel_id'],'root_id':target['post_id'],'message':message,
           'file_ids':[files[f] for f in source_files],
           'props':{'bridge_mirror_id':item['mirror_id'],'bridge_source_post_id':item['source_post_id'],'bridge_relay':True},'silent':True}
  digest=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
  post=self.target_post(item,target,posts)
  if not post:
   self.update(item,status='SENDING')
   post=self.mm.api('POST','/posts',payload)
   self.validate_target(item,post,target)
   self.update(item,target_post_id=post['id']);posts[post['id']]=post
  elif item['content_hash']!=digest:
   self.mm.api('PUT','/posts/'+post['id']+'/patch',{k:payload[k] for k in ('message','file_ids','props')})
  if item['kind']=='REPLY':self.sync_native(post['id'],reactions)
  self.update(item,status='SYNCED',content_hash=digest,source_updated_at=source_post.get('update_at',0),source_reactions=json.dumps(reactions,separators=(',',':'),sort_keys=True),last_error='',attempt_count=0,next_retry_at='')

 def pair(self,source,target,root_item):
  origin=self.thread(source['post_id'],source['channel_id'])
  destination=self.thread(target['post_id'],target['channel_id'])
  root=origin[source['post_id']];root_reactions=self.reactions(root['id'])
  # Multiple business threads can map to one monitor incident after rerouting.
  # Keep the union of their last confirmed reaction kinds on the monitor root.
  combined=list(root_reactions)
  for previous in self.store.snapshot()['activity_mirrors']:
   if previous['kind']=='ROOT' and previous['target_post_id']==target['post_id'] and previous['mirror_id']!=root_item['mirror_id']:
    combined.extend(json.loads(previous['source_reactions'] or '[]'))
  self.sync_native(target['post_id'],combined)
  self.update(root_item,status='SYNCED',source_updated_at=root.get('update_at',0),source_reactions=json.dumps(root_reactions,separators=(',',':'),sort_keys=True),last_error='',attempt_count=0,next_retry_at='')
  # Native reactions are bot-owned; preserve real users/counts in a separate summary.
  summary=self.ensure(source,target,root['id'],'SUMMARY')
  if self.due(summary):
   try:
    if root_reactions:
     text='**'+self.channel_label(source['channel_id'])+' · 장애 원문 반응 동기화**\n\n'+self.reaction_text(root_reactions)+'\n\n[업무 채널 원문]('+self.mm.c.get('public_url',self.mm.c['url']).rstrip('/')+'/_redirect/pl/'+root['id']+')'
     self.write(summary,target,destination,text,root,root_reactions)
    else:self.remove(summary,target,destination)
   except Exception as exc:self.failed(summary,exc)
  for post in sorted(origin.values(),key=lambda p:(p.get('create_at',0),p['id'])):
   if post['id']==root['id'] or post.get('root_id')!=root['id'] or post.get('user_id')==self.bot_id or post.get('props',{}).get('bridge_relay'):continue
   item=self.ensure(source,target,post['id'],'REPLY')
   if not self.due(item):continue
   try:
    if post.get('delete_at'):self.remove(item,target,destination);continue
    reactions=self.reactions(post['id']);actor=self.label(post['user_id'])
    timestamp=datetime.fromtimestamp(post.get('create_at',0)/1000,timezone.utc).isoformat(timespec='seconds')
    text='**업무 채널 답글 전달 · '+self.channel_label(source['channel_id'])+' · '+actor+'**\n\n'+post.get('message','')+'\n\n---\n원작성자: '+actor+' · 작성: '+timestamp+'\n[업무 채널 원문]('+self.mm.c.get('public_url',self.mm.c['url']).rstrip('/')+'/_redirect/pl/'+post['id']+')'
    if post.get('edit_at'):text+=' · 원문 수정 반영'
    if reactions:text+='\n\n'+self.reaction_text(reactions)
    self.write(item,target,destination,text,post,reactions)
   except Exception as exc:self.failed(item,exc)
  # Only a complete successful thread + a direct deletion confirmation permits deletion.
  rows=self.store.snapshot()['activity_mirrors']
  for item in rows:
   if item['source_delivery_id']!=source['delivery_id'] or item['kind']!='REPLY' or item['status']=='DELETED' or item['source_post_id'] in origin or not self.due(item):continue
   try:
    try:missing=self.mm.api('GET','/posts/'+item['source_post_id'])
    except urllib.error.HTTPError as exc:
     if exc.code!=404:raise
     missing={'delete_at':1}
    if missing.get('delete_at'):self.remove(item,target,destination)
   except Exception as exc:self.failed(item,exc)

 def tick(self):
  if self.mode!='live':return
  errors=[]
  try:
   if not self.bot_id:self.bot_id=self.mm.api('GET','/users/me')['id']
   snapshot=self.store.snapshot()
   for source in snapshot['deliveries']:
    if source['role'] not in ('OWNER','PREVIOUS') or not source['post_id']:continue
    target=next((d for d in snapshot['deliveries'] if d['incident_id']==source['incident_id'] and d['channel_id']==self.e.config['mattermost']['monitor_channel'] and d['post_id']),None)
    if not target or target['channel_id']==source['channel_id']:continue
    root_item=self.ensure(source,target,source['post_id'],'ROOT')
    if not self.due(root_item):continue
    try:self.pair(source,target,root_item)
    except Exception as exc:self.failed(root_item,exc)
   pending=[r for r in self.store.snapshot()['activity_mirrors'] if r['status'] in ('RETRY','MANUAL_REVIEW')]
   if pending:errors.append('답글·이모지 동기화 대기 '+str(len(pending))+'건: '+pending[0]['last_error'])
  except Exception as exc:errors.append('답글·이모지 동기화 연결 실패: '+type(exc).__name__)
  self.error=' / '.join(errors)
  self.e.health('mattermost-activity','ERROR' if errors else 'OK',self.error or '업무 장애 스레드 → 관제팀 동기화 정상')
