from sql_support import reset_database
import json,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from app import Application,Server,Handler,validate_config
from bridge.store import Store
class HTTPTests(unittest.TestCase):
 def setUp(self):
  reset_database()
  self.temp=tempfile.TemporaryDirectory();self.a=Application(self.temp.name);self.s=Server(('127.0.0.1',0),Handler);self.s.application=self.a;self.t=threading.Thread(target=self.s.serve_forever,daemon=True);self.t.start();self.base=f'http://127.0.0.1:{self.s.server_port}'
 def tearDown(self):self.s.shutdown();self.s.server_close();self.temp.cleanup()
 def call(self,path,body=None,auth=True):
  headers={'Content-Type':'application/json'}
  if auth:headers['X-Bridge-Token']=self.a.token
  req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
  with urllib.request.urlopen(req) as r:return json.load(r)
 def test_http_demo_end_to_end(self):
  self.call('/api/login',{});i=self.call('/api/demo/add',{'source':'jobpass'});self.a.dispatcher.tick();s=self.call('/api/state');self.assertEqual(len(s['captures']),1)
  self.call('/api/action',{'incident_id':i['incident_id'],'action':'ack','revision':1});self.call('/api/action',{'incident_id':i['incident_id'],'action':'complete','revision':2,'note':'tested'})
  i=self.call('/api/state')['incidents'][0];self.assertEqual(i['workflow_status'],'COMPLETED');self.assertEqual(i['source_status'],'PRESENT')
  result=self.call('/api/db/incidents');self.assertEqual(result['row_count'],1);self.assertIn('COMPLETED',result['rows'][0])
 def test_unauthenticated_access_rejected(self):
  with self.assertRaises(urllib.error.HTTPError) as e:self.call('/api/state',auth=False)
  self.assertEqual(e.exception.code,403)
 def test_unauthenticated_mutation_rejected(self):
  with self.assertRaises(urllib.error.HTTPError) as e:self.call('/api/demo/add',{'source':'jobpass'},False)
  self.assertEqual(e.exception.code,403)
 def test_callback_disabled_in_demo(self):
  with self.assertRaises(urllib.error.HTTPError) as e:self.call('/mattermost/action',{})
  self.assertEqual(e.exception.code,403)
 def test_mock_source_only(self):
  self.call('/api/demo/add',{'source':'jobpass','direct':False});self.assertEqual(len(self.call('/mock-data/jobpass',auth=False)),1);self.assertFalse(self.call('/api/state')['incidents'])
 def test_config_validation(self):
  c=self.a.config.copy();c['host']='0.0.0.0'
  with self.assertRaises(ValueError):validate_config(c)
if __name__=='__main__':unittest.main()

class CallbackAttachmentTests(unittest.TestCase):
 setUp=HTTPTests.setUp
 tearDown=HTTPTests.tearDown
 call=HTTPTests.call
 def test_callback_does_not_replace_post_without_attachments(self):
  from unittest.mock import patch
  item=self.a.add_demo('polestar');self.a.dispatcher.tick()
  delivery=next(d for d in self.a.store.snapshot()['deliveries'] if d['role']=='OWNER')
  self.a.dispatcher.update(delivery['delivery_id'],file_ids='{"capture1":"file1"}')
  self.a.config['mode']='live';self.a.config['mattermost']['callback_allowed_ips']=['127.0.0.1/32']
  with patch.object(self.a.mm,'authorize',return_value=('operator','ack',1)):
   result=self.call('/mattermost/action',{'context':{'delivery_id':delivery['delivery_id']}})
  self.assertEqual(result,{})
  self.assertEqual(self.a.store.row('deliveries',delivery['delivery_id'])['file_ids'],'{"capture1":"file1"}')
  self.assertEqual(self.a.store.row('incidents',item['incident_id'])['workflow_status'],'IN_PROGRESS')
