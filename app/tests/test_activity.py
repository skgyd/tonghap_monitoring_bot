import copy,json,tempfile,unittest,urllib.error
from pathlib import Path
from sql_support import reset_database
from bridge.store import Store,row,uid
from bridge.engine import Engine
from bridge.activity import ActivitySync
from tools.channels import ensure_monitor_team

class FakeMM:
 def __init__(self):
  self.c={'url':'http://localhost:8065'};self.posts={};self.react={};self.counter=0;self.fail_create=False;self.fail_thread=False;self.fail_reactions=False;self.files_copied=[]
 def add(self,pid,channel,root='',message='',user='human1',files=None):
  self.posts[pid]={'id':pid,'channel_id':channel,'root_id':root,'message':message,'user_id':user,'file_ids':files or [],'props':{},'create_at':1000+self.counter,'update_at':1000+self.counter,'delete_at':0};return self.posts[pid]
 def copy_attachment(self,fileid,channel):self.files_copied.append(fileid);return 'copy-'+fileid
 def api(self,method,path,payload=None):
  if path=='/users/me':return {'id':'bot'}
  if path.startswith('/users/') and method=='GET':return {'id':path.split('/')[-1],'username':path.split('/')[-1]}
  if path.startswith('/channels/') and method=='GET':return {'display_name':'기업회계'}
  if '/thread?' in path:
   if self.fail_thread:raise urllib.error.HTTPError('',503,'down',{},None)
   root=path.split('/')[2];posts={k:copy.deepcopy(p) for k,p in self.posts.items() if (k==root or p['root_id']==root) and not p['delete_at']}
   return {'posts':posts,'order':list(posts)}
  if method=='GET' and path.endswith('/reactions'):
   if self.fail_reactions:raise urllib.error.HTTPError('',503,'down',{},None)
   pid=path.split('/')[2];return [dict(post_id=pid,user_id=u,emoji_name=e) for u,e in self.react.get(pid,set())]
  if path=='/reactions' and method=='POST':
   self.react.setdefault(payload['post_id'],set()).add((payload['user_id'],payload['emoji_name']));return dict(payload)
  if method=='DELETE' and '/reactions/' in path:
   _,_,user,_,pid,_,emoji=path.split('/');self.react.setdefault(pid,set()).discard((user,emoji));return {'status':'OK'}
  if path=='/posts' and method=='POST':
   self.counter+=1;p=self.add('mirror'+str(self.counter),payload['channel_id'],payload['root_id'],payload['message'],'bot',payload['file_ids']);p['props']=payload['props']
   if self.fail_create:self.fail_create=False;raise TimeoutError('committed but response lost')
   return copy.deepcopy(p)
  if path.startswith('/posts/'):
   pid=path.split('/')[2]
   if pid not in self.posts or self.posts[pid]['delete_at']:raise urllib.error.HTTPError('',404,'deleted',{},None)
   if method=='GET':return copy.deepcopy(self.posts[pid])
   if method=='PUT':self.posts[pid].update(copy.deepcopy(payload));return copy.deepcopy(self.posts[pid])
   if method=='DELETE':self.posts[pid]['delete_at']=123;return {'status':'OK'}
  raise AssertionError((method,path,payload))

class ActivityTests(unittest.TestCase):
 def setUp(self):
  self.s=reset_database();c=json.loads((Path(__file__).resolve().parents[1]/'config.example.json').read_text());c['mattermost']['monitor_channel']='monitor'
  self.s.transaction(lambda d:d['routes'].append(row('routes',route_id=uid(),source='polestar',target_key='기업회계',channel_id='owner',enabled=True)))
  self.e=Engine(self.s,c);self.incident=self.e.ingest('polestar','run1','기업회계','error')
  def delivery(data):
   for d in data['deliveries']:d.update(post_id='root-'+d['role'],status='SENT')
  self.s.transaction(delivery)
  self.mm=FakeMM();self.mm.add('root-OWNER','owner',user='bot');self.mm.add('root-MONITOR','monitor',user='bot')
  self.a=ActivitySync(self.e,self.mm)
 def replies(self):return [p for p in self.mm.posts.values() if p['channel_id']=='monitor' and p['root_id'] and not p['delete_at']]
 def item(self,kind='REPLY'):return next(x for x in self.s.snapshot()['activity_mirrors'] if x['kind']==kind)
 def retry(self):
  def op(d):
   for r in d['activity_mirrors']:
    if r['status'] in ('RETRY','MANUAL_REVIEW'):r.update(status='PENDING',next_retry_at='')
  self.s.transaction(op)
 def test_reply_edit_delete_preserves_thread_and_author(self):
  self.mm.add('reply','owner','root-OWNER','확인하겠습니다 😀');self.a.tick()
  p=self.replies()[0];pid=p['id'];self.assertIn('확인하겠습니다 😀',p['message']);self.assertIn('human1',p['message']);self.assertEqual(p['user_id'],'bot')
  self.mm.posts['reply'].update(message='확인 완료했습니다.',update_at=2000,edit_at=2000);self.a.tick()
  self.assertEqual(len(self.replies()),1);self.assertEqual(self.replies()[0]['id'],pid);self.assertIn('확인 완료했습니다.',self.replies()[0]['message'])
  self.mm.posts['reply']['delete_at']=1;self.a.tick();self.assertEqual(self.replies(),[]);self.assertEqual(self.item()['status'],'DELETED')
 def test_hosted_reply_and_reaction_links_use_public_mattermost_origin(self):
  self.mm.c.update(url='http://mattermost:8065',public_url='https://chat.contest.example')
  self.mm.add('reply','owner','root-OWNER','외부 접속 링크 검사')
  self.mm.react['root-OWNER']={('human1','eyes')};self.a.tick()
  self.assertEqual(len(self.replies()),2)
  for post in self.replies():
   self.assertIn('https://chat.contest.example/_redirect/pl/',post['message'])
   self.assertNotIn('http://mattermost:',post['message'])
 def test_multiple_reactors_counts_and_removal_keep_monitor_users(self):
  self.mm.react['root-OWNER']={('human1','eyes'),('human2','eyes')};self.mm.react['root-MONITOR']={('monitor-user','heart')};self.a.tick()
  p=self.replies()[0];self.assertIn('**2명**',p['message']);self.assertIn('human1',p['message']);self.assertIn('human2',p['message'])
  self.assertIn(('bot','eyes'),self.mm.react['root-MONITOR'])
  self.mm.react['root-OWNER'].remove(('human1','eyes'));self.a.tick();self.assertIn('**1명**',self.replies()[0]['message']);self.assertIn(('bot','eyes'),self.mm.react['root-MONITOR'])
  self.mm.react['root-OWNER'].clear();self.a.tick();self.assertEqual(self.replies(),[]);self.assertEqual(self.mm.react['root-MONITOR'],{('monitor-user','heart')})
 def test_reply_reactions_and_attachments_are_preserved(self):
  self.mm.add('reply','owner','root-OWNER','로그 첨부',files=['fileA']);self.mm.react['reply']={('human2','white_check_mark')};self.a.tick()
  target=self.replies()[0];self.assertEqual(target['file_ids'],['copy-fileA']);self.assertIn(('bot','white_check_mark'),self.mm.react[target['id']]);self.assertIn('human2',target['message'])
  self.mm.react['reply'].clear();self.mm.posts['reply'].update(message='로그 재확인',update_at=2000);self.a.tick()
  self.assertEqual(self.mm.files_copied,['fileA']);self.assertEqual(self.replies()[0]['file_ids'],['copy-fileA']);self.assertFalse(self.mm.react[target['id']])
 def test_response_lost_after_post_is_reconciled_without_duplicate(self):
  self.mm.add('reply','owner','root-OWNER','재시도');self.mm.fail_create=True;self.a.tick();self.assertEqual(self.item()['status'],'RETRY')
  self.retry();self.a=ActivitySync(self.e,self.mm);self.a.tick();self.assertEqual(len(self.replies()),1);self.assertEqual(self.item()['status'],'SYNCED')
 def test_failed_thread_or_reactions_never_delete_existing_mirrors(self):
  self.mm.add('reply','owner','root-OWNER','유지');self.mm.react['root-OWNER']={('human1','eyes')};self.a.tick();count=len(self.replies())
  self.mm.fail_thread=True;self.a.tick();self.assertEqual(len(self.replies()),count)
  self.mm.fail_thread=False;self.retry();self.mm.fail_reactions=True;self.a.tick();self.assertEqual(len(self.replies()),count);self.assertIn(('bot','eyes'),self.mm.react['root-MONITOR'])
 def test_monitor_reply_bot_reply_and_unrelated_messages_are_not_forwarded(self):
  self.mm.add('unrelated','owner',message='업무 채널 일반 대화');self.mm.add('bot-reply','owner','root-OWNER','bot','bot');self.mm.add('monitorreply','monitor','root-MONITOR','관제팀에서 직접 작성')
  self.a.tick();self.assertEqual(len(self.replies()),1);self.assertEqual(self.replies()[0]['id'],'monitorreply')
 def test_free_text_and_emoji_do_not_change_workflow(self):
  self.mm.add('reply','owner','root-OWNER','처리 완료했습니다.');self.mm.react['root-OWNER']={('human1','white_check_mark')};self.a.tick()
  self.assertEqual(self.s.row('incidents',self.incident['incident_id'])['workflow_status'],'NEW');self.assertEqual(self.s.snapshot()['actions'],[])
 def test_restart_has_no_duplicate_replies(self):
  self.mm.add('reply','owner','root-OWNER','저장');self.a.tick();pid=self.replies()[0]['id'];ActivitySync(Engine(Store(),self.e.config),self.mm).tick()
  self.assertEqual(len(self.replies()),1);self.assertEqual(self.replies()[0]['id'],pid)
 def test_second_incident_maps_to_its_own_monitor_thread(self):
  self.e.ingest('polestar','run2','기업회계','another')
  def assign(data):
   for d in data['deliveries']:
    if not d['post_id']:d.update(post_id='second-'+d['role'],status='SENT')
  self.s.transaction(assign);self.mm.add('second-OWNER','owner',user='bot');self.mm.add('second-MONITOR','monitor',user='bot');self.mm.add('second-reply','owner','second-OWNER','두 번째 사건')
  self.a.tick();p=self.replies()[0];self.assertEqual(p['root_id'],'second-MONITOR')
 def test_reroute_to_monitor_keeps_previous_business_thread_tracked(self):
  self.e.action(self.incident['incident_id'],'reroute','admin',admin=True,channel='monitor')
  self.mm.add('reply','owner','root-OWNER','이전 업무 채널 확인');self.a.tick()
  self.assertEqual(len(self.replies()),1);self.assertEqual(self.replies()[0]['root_id'],'root-MONITOR')
 def test_rerouted_threads_reactions_are_unioned_without_overwriting(self):
  self.e.action(self.incident['incident_id'],'reroute','admin',admin=True,channel='other-owner')
  def assign(d):
   for r in d['deliveries']:
    if r['channel_id']=='other-owner':r.update(post_id='new-root',status='SENT')
  self.s.transaction(assign);self.mm.add('new-root','other-owner',user='bot')
  self.mm.react['root-OWNER']={('human1','eyes')};self.mm.react['new-root']={('human2','white_check_mark')}
  self.a.tick();self.a.tick();self.assertEqual(self.mm.react['root-MONITOR'],{('bot','eyes'),('bot','white_check_mark')})
  self.mm.react['root-OWNER'].clear();self.a.tick();self.assertEqual(self.mm.react['root-MONITOR'],{('bot','white_check_mark')})
 def test_root_deletion_requires_review_and_keeps_audit(self):
  self.mm.add('reply','owner','root-OWNER','유지');self.a.tick();self.mm.posts['root-OWNER']['delete_at']=1;self.a.tick()
  self.assertEqual(len(self.replies()),1);self.assertEqual(self.item('ROOT')['status'],'MANUAL_REVIEW')

class ChannelRenameTests(unittest.TestCase):
 def test_existing_channel_renamed_in_place_and_restart_is_idempotent(self):
  channel={'id':'same-id','display_name':'관제','name':'monitor'};credentials={'token':'secret','channels':{'관제':channel.copy()}};updates=[]
  def api(method,path,payload=None,token=None):
   if method=='PUT':updates.append(payload);channel.update(payload)
   return channel.copy(),{}
  ensure_monitor_team(api,credentials);ensure_monitor_team(api,credentials)
  self.assertEqual(updates,[{'display_name':'관제팀'}]);self.assertEqual(credentials['channels']['관제팀']['id'],'same-id');self.assertNotIn('관제',credentials['channels'])
