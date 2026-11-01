import hmac
from datetime import datetime,timezone,timedelta
from .store import now,uid,row

class Conflict(ValueError): pass
class Forbidden(ValueError): pass

class Engine:
 def __init__(self,store,config): self.store=store; self.config=config
 def notify(self,d,i,kind,message):
  d['notifications'].append(row('notifications',notification_id=uid(),incident_id=i,created_at=now(),kind=kind,message=message))
 def capture_status(self,source,kinds):
  if not kinds:return 'FAILED'
  required={'LIST'}
  if source=='jobpass':
   required.add('DETAIL')
   extras=self.config.get('collectors',{}).get(source,{}).get('detail_extra_capture_selectors',[])
   required.update('DETAIL_EXTRA_'+str(n+1) for n in range(len(extras)))
  return 'OK' if required.issubset(kinds) else 'DETAIL_PENDING'
 def ingest(self,source,event_id,job,message,capture='',capture_type='LIST'):
  if not event_id or not source: raise ValueError('Stable event ID is required')
  def op(d):
   existing=next((x for x in d['incidents'] if x['source']==source and x['source_event_id']==event_id),None)
   if existing:
    existing.update(last_seen_at=now(),source_status='PRESENT',missing_count=0)
    if capture and not any(c['incident_id']==existing['incident_id'] and c['capture_type']==capture_type for c in d['captures']):
     d['captures'].append(row('captures',capture_id=uid(),incident_id=existing['incident_id'],file_path=capture,captured_at=now(),capture_type=capture_type))
     existing['capture_status']=self.capture_status(source,{c['capture_type'] for c in d['captures'] if c['incident_id']==existing['incident_id']})
     existing['revision']=int(existing['revision'])+1
    return existing
   routes=[r for r in d['routes'] if r['source'] in [source,'*'] and r['target_key'] in [job,'*'] and str(r['enabled']).lower() in ['true','1']]
   routes.sort(key=lambda r:(r['target_key']==job,r['source']==source),reverse=True)
   channel=routes[0]['channel_id'] if routes else self.config['mattermost']['monitor_channel']
   i=uid(); item=row('incidents',incident_id=i,source=source,source_event_id=event_id,job_name=job,message=message[:30000],detected_at=now(),last_seen_at=now(),workflow_status='NEW',source_status='PRESENT',channel_id=channel,revision=1,capture_status=self.capture_status(source,{capture_type} if capture else set()),missing_count=0,unmapped=not bool(routes))
   d['incidents'].append(item)
   if capture: d['captures'].append(row('captures',capture_id=uid(),incident_id=i,file_path=capture,captured_at=now(),capture_type=capture_type))
   for role,ch in [('OWNER',channel),('MONITOR',self.config['mattermost']['monitor_channel'])]:
    if ch and not any(x['incident_id']==i and x['channel_id']==ch for x in d['deliveries']):
     d['deliveries'].append(row('deliveries',delivery_id=uid(),incident_id=i,channel_id=ch,role=role,status='PENDING',attempt_count=0,sent_revision=0,updated_at=now()))
   if not routes: self.notify(d,i,'UNMAPPED','담당 매핑 없음: 관제 채널로 전달')
   return item
  return self.store.transaction(op)
 def reconcile(self,source,seen,complete=True):
  # A partial/failed scan must NEVER remove events.
  def op(d):
   for i in d['incidents']:
    if i['source']!=source: continue
    if i['source_event_id'] in seen:
     changed=i['source_status']!='PRESENT'; i.update(source_status='PRESENT',missing_count=0,last_seen_at=now())
    elif complete:
     i['missing_count']=int(i['missing_count'] or 0)+1
     changed=i['missing_count']>=self.config['absence_confirmations'] and i['source_status']!='ABSENT'
     if changed:
      i['source_status']='ABSENT'; self.notify(d,i['incident_id'],'SOURCE_ABSENT','원본 목록에서 사라짐. 처리 완료/정상화로 자동 판단하지 않음.')
    else: changed=False
    if changed: i['revision']=int(i['revision'])+1
  self.store.transaction(op)
 def health(self,source,status,message=''):
  def op(d):
   x=next((r for r in d['collector_state'] if r['collector_id']==source),None)
   if x is None: x=row('collector_state',collector_id=source); d['collector_state'].append(x)
   x.update(health_status=status,message=message[:1000])
   if status=='OK': x['last_success_at']=now()
  self.store.transaction(op)
 def action(self,iid,action,actor,expected_revision=None,note='',admin=False,channel=''):
  def op(d):
   i=next((x for x in d['incidents'] if x['incident_id']==iid),None)
   if not i: raise ValueError('Unknown incident')
   if expected_revision is not None and int(i['revision'])!=int(expected_revision): raise Conflict('상태가 변경되었습니다. 새로고침 후 다시 시도하세요.')
   state=i['workflow_status']
   if action=='ack':
    if state!='NEW': raise Conflict('이미 접수되었거나 완료된 장애입니다.')
    i.update(workflow_status='IN_PROGRESS',assignee_id=actor)
   elif action=='complete':
    if state!='IN_PROGRESS': raise Conflict('접수 후 완료할 수 있습니다.')
    if not admin and i['assignee_id']!=actor: raise Forbidden('접수자 또는 관리자만 완료할 수 있습니다.')
    i.update(workflow_status='COMPLETED',completed_by=actor,completed_at=now())
   elif action=='reopen':
    if not admin: raise Forbidden('재개는 관리자만 가능합니다.')
    if state!='COMPLETED': raise Conflict('완료된 장애만 재개할 수 있습니다.')
    if not note.strip(): raise ValueError('재개 사유가 필요합니다.')
    i.update(workflow_status='NEW',assignee_id='',completed_by='',completed_at='')
   elif action=='assign':
    if not admin or not note.strip(): raise Forbidden('관리자와 새 담당자 ID가 필요합니다.')
    if state=='COMPLETED': raise Conflict('먼저 재개하세요.')
    i.update(workflow_status='IN_PROGRESS',assignee_id=note.strip())
   elif action=='reroute':
    if not admin or not channel: raise Forbidden('관리자와 채널 ID가 필요합니다.')
    i.update(channel_id=channel,unmapped=False)
    for delivery in d['deliveries']:
     if delivery['incident_id']==iid and delivery['role']=='OWNER':
      delivery['role']='MONITOR' if delivery['channel_id']==self.config['mattermost']['monitor_channel'] else 'PREVIOUS'
    existing=next((x for x in d['deliveries'] if x['incident_id']==iid and x['channel_id']==channel),None)
    if existing: existing['role']='OWNER'
    else: d['deliveries'].append(row('deliveries',delivery_id=uid(),incident_id=iid,channel_id=channel,role='OWNER',status='PENDING',attempt_count=0,sent_revision=0,updated_at=now()))
   else: raise ValueError('Unknown action')
   i['revision']=int(i['revision'])+1
   d['actions'].append(row('actions',action_id=uid(),incident_id=iid,actor_id=actor,action_type=action,created_at=now(),note=note))
   self.notify(d,iid,'ACTION',f'{action}: {actor}')
   return i
  return self.store.transaction(op)
 def reminders(self):
  snap=self.store.snapshot(); current=datetime.now(timezone.utc)
  due=[]
  for i in snap['incidents']:
   if i['workflow_status']=='COMPLETED': continue
   base=i['last_reminded_at'] or i['detected_at']
   seconds=self.config['unack_seconds'] if i['workflow_status']=='NEW' else self.config['inprogress_seconds']
   if (current-datetime.fromisoformat(base)).total_seconds()>=seconds: due.append(i['incident_id'])
  if not due:return
  def op(d):
   for i in d['incidents']:
    if i['incident_id'] not in due or i['workflow_status']=='COMPLETED':continue
    i['last_reminded_at']=now(); i['revision']=int(i['revision'])+1
    self.notify(d,i['incident_id'],'OVERDUE','미접수 또는 장기 처리 지연: 관제 확인 필요')
  self.store.transaction(op)
