from sql_support import reset_database
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from bridge.store import Store,row,uid
from bridge.engine import Engine
from bridge.mattermost import Mattermost,Dispatcher
ROOT=Path(__file__).resolve().parents[1]
class DeliveryTests(unittest.TestCase):
 def setUp(self):
  reset_database()
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.c=json.loads((ROOT/'config.example.json').read_text());self.c['mattermost']['callback_base']='https://bridge.example';self.s=Store();self.e=Engine(self.s,self.c);self.mm=Mattermost(self.c['mattermost'],self.root,'secret');self.dispatch=Dispatcher(self.e,self.mm,self.root,'live');self.posts={};self.count=0
  self.s.transaction(lambda d:d['routes'].append(row('routes',route_id=uid(),source='jobpass',target_key='*',channel_id='owner',enabled=True)))
 def tearDown(self):self.tmp.cleanup()
 def api(self,method,path,payload=None,**kw):
  if method=='POST' and path=='/posts':
   self.count+=1;post=dict(payload,id=f'post{self.count}',create_at=9999999999999);self.posts[post['id']]=post;return post
  if method=='GET':return {'posts':{k:v for k,v in self.posts.items() if v['channel_id']==path.split('/')[2]}}
  if method=='PUT':self.posts[path.split('/')[2]].update(payload);return self.posts[path.split('/')[2]]
  raise AssertionError((method,path))
 def test_two_channels_receive_state_update_and_buttons_only_owner(self):
  i=self.e.ingest('jobpass','e1','JOB','err')
  with patch.object(self.mm,'api',side_effect=self.api):
   self.dispatch.tick();self.assertEqual(self.count,2)
   owner=next(p for p in self.posts.values() if p['channel_id']=='owner');monitor=next(p for p in self.posts.values() if p['channel_id']!='owner')
   self.assertEqual(owner['props']['attachments'][0]['actions'][0]['id'],'ack');self.assertFalse(monitor['props']['attachments'][0]['actions'])
   self.e.action(i['incident_id'],'ack','user');self.dispatch.tick();self.assertIn('처리 중',owner['message']);self.assertIn('처리 중',monitor['message']);self.assertEqual(owner['props']['attachments'][0]['actions'][0]['id'],'complete')
 def test_reroute_preserves_old_and_monitor_attachments_and_enables_only_new_owner(self):
  (self.root/'image.png').write_bytes(b'proof')
  i=self.e.ingest('jobpass','event','JOB','err','image.png','DETAIL')
  with patch.object(self.mm,'api',side_effect=self.api), patch.object(self.mm,'upload',side_effect=lambda path,ch:'file-'+ch):
   self.dispatch.tick()
   original_ids={post['channel_id']:post['id'] for post in self.posts.values()}
   self.e.action(i['incident_id'],'reroute','judge',i['revision'],note='~new',admin=True,channel='new')
   self.dispatch.tick()
   self.dispatch.tick()
   self.assertEqual(len(self.posts),3)
   for post in self.posts.values():
    channel=post['channel_id']
    self.assertEqual(post['file_ids'],['file-'+channel])
    if channel in original_ids:self.assertEqual(post['id'],original_ids[channel])
    buttons=post['props']['attachments'][0]['actions']
    self.assertEqual([btn['id'] for btn in buttons],['ack'] if channel=='new' else [])
 def test_uncertain_post_reconciles_without_duplicate(self):
  self.e.ingest('jobpass','e1','JOB','err');failed=[False]
  def unstable(method,path,payload=None,**kw):
   result=self.api(method,path,payload,**kw)
   if method=='POST' and not failed[0]:failed[0]=True;raise TimeoutError('response lost after commit')
   return result
  with patch.object(self.mm,'api',side_effect=unstable):self.dispatch.tick()
  pending=next(d for d in self.s.snapshot()['deliveries'] if d['status']=='RETRY');self.dispatch.update(pending['delivery_id'],next_retry_at='')
  with patch.object(self.mm,'api',side_effect=self.api):self.dispatch.tick()
  self.assertEqual(self.count,2);self.assertTrue(all(d['status']=='SENT' for d in self.s.snapshot()['deliveries']))
if __name__=='__main__':unittest.main()
