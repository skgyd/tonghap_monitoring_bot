from sql_support import reset_database
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from bridge.store import Store,row,uid
from bridge.engine import Engine,Conflict,Forbidden
from bridge.mattermost import Mattermost,Dispatcher
ROOT=Path(__file__).resolve().parents[1]
class BridgeTests(unittest.TestCase):
 def setUp(self):
  reset_database()
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store();self.c=json.loads((ROOT/'config.example.json').read_text());self.e=Engine(self.store,self.c)
 def tearDown(self):self.temp.cleanup()
 def add(self,key='event1'):return self.e.ingest('jobpass',key,'JOB','error')
 def test_round_trip_and_formula_injection(self):
  self.e.ingest('polestar','1','JOB','=HYPERLINK("evil")');s=Store(self.store.dsn);self.assertEqual(s.snapshot()['incidents'][0]['message'],'=HYPERLINK("evil")');self.assertEqual(s.snapshot(),self.store.snapshot())
 def test_dedup_and_new_execution(self):
  a=self.add();b=self.add();c=self.add('event2');self.assertEqual(a['incident_id'],b['incident_id']);self.assertNotEqual(a['incident_id'],c['incident_id'])
 def test_failed_scan_does_not_remove(self):
  self.add();self.e.reconcile('jobpass',set(),False);self.e.reconcile('jobpass',set(),False);self.assertEqual(self.store.snapshot()['incidents'][0]['source_status'],'PRESENT')
 def test_disappearance_is_not_completion(self):
  i=self.add();self.e.reconcile('jobpass',set());self.e.reconcile('jobpass',set());r=self.store.row('incidents',i['incident_id']);self.assertEqual(r['source_status'],'ABSENT');self.assertEqual(r['workflow_status'],'NEW')
 def test_state_machine_and_permissions(self):
  iid=self.add()['incident_id']
  with self.assertRaises(Conflict):self.e.action(iid,'complete','u')
  self.e.action(iid,'ack','u')
  with self.assertRaises(Forbidden):self.e.action(iid,'complete','v')
  self.e.action(iid,'complete','u')
  with self.assertRaises(Forbidden):self.e.action(iid,'reopen','u',note='wrong')
  self.e.action(iid,'reopen','admin',note='mistake',admin=True);self.assertEqual(self.store.row('incidents',iid)['workflow_status'],'NEW')
 def test_concurrent_ack_one_winner(self):
  i=self.add()
  def ack(user):
   try:self.e.action(i['incident_id'],'ack',user,1);return True
   except Conflict:return False
  with ThreadPoolExecutor(2) as p:result=list(p.map(ack,['u','v']))
  self.assertEqual(sum(result),1);self.assertEqual(len(self.store.snapshot()['actions']),1)
 def test_sql_failure_rolls_back_incident_and_action(self):
  import sqlite3
  from bridge.store import StoreError
  i=self.add();before=self.store.snapshot();original=self.store._write_diff
  def fail_after_sql(cur,old,new):
   original(cur,old,new);raise sqlite3.DatabaseError('simulated commit failure')
  with patch.object(self.store,'_write_diff',side_effect=fail_after_sql):
   with self.assertRaises(StoreError):self.e.action(i['incident_id'],'ack','u')
  self.assertEqual(before,Store(self.store.dsn).snapshot())
 def test_restart_preserves_pending(self):
  self.add();self.assertEqual(Store(self.store.dsn).snapshot()['deliveries'][0]['status'],'PENDING')
 def test_retry_and_delivery(self):
  self.add();d=Dispatcher(self.e,None,self.root,'demo');d.simulate_failure=True;d.tick();delivery=self.store.snapshot()['deliveries'][0];self.assertEqual(delivery['status'],'RETRY');d.update(delivery['delivery_id'],next_retry_at='');d.simulate_failure=False;d.tick();self.assertEqual(self.store.snapshot()['deliveries'][0]['status'],'SENT')
 def test_signature_and_membership(self):
  i=self.add();d=self.store.snapshot()['deliveries'][0];d.update(role='OWNER',post_id='abc');m=Mattermost(self.c['mattermost'],self.root,'secret');m.c['allowed_user_ids']=['u'];body={'user_id':'u','post_id':'abc','channel_id':d['channel_id'],'context':{'revision':1,'action':'ack','signature':m.sign(d['delivery_id'],1,'ack')}}
  with patch.object(m,'api',return_value={'user_id':'u'}):self.assertEqual(m.authorize(body,d,i)[0],'u')
  body['context']['signature']='fake'
  with self.assertRaises(PermissionError):m.authorize(body,d,i)
 def test_database_check_constraint_is_enforced(self):
  from bridge.store import StoreError
  self.add()
  with self.assertRaises(StoreError):
   with self.store.connection() as conn:
    conn.execute('UPDATE incidents SET revision=-1')
  self.assertEqual(self.store.snapshot()['incidents'][0]['revision'],1)
 def test_route_and_reroute(self):
  self.store.transaction(lambda d:d['routes'].append(row('routes',route_id=uid(),source='jobpass',target_key='JOB',channel_id='owner',enabled=True)))
  i=self.add();self.assertEqual(i['channel_id'],'owner');self.assertEqual(len(self.store.snapshot()['deliveries']),2);self.e.action(i['incident_id'],'reroute','admin',admin=True,channel='new');self.assertEqual(self.store.row('incidents',i['incident_id'])['channel_id'],'new')
 def test_duplicate_keys_rejected(self):
  self.add()
  with self.assertRaises(ValueError):self.store.transaction(lambda d:d['incidents'].append(copy.deepcopy(d['incidents'][0])))
 def test_missing_capture_retry(self):
  i=self.add();self.assertEqual(i['capture_status'],'FAILED');i=self.e.ingest('jobpass','event1','JOB','error','captures/list.png','LIST');self.assertEqual(i['capture_status'],'DETAIL_PENDING');i=self.e.ingest('jobpass','event1','JOB','error','captures/detail.png','DETAIL');self.assertEqual(i['capture_status'],'OK')
if __name__=='__main__':unittest.main()
