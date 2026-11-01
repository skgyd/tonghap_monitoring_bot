from sql_support import reset_database
import json,tempfile,threading,unittest,urllib.request,urllib.error
from app import Application,Server,Handler

class ChatTests(unittest.TestCase):
 def setUp(self):
  reset_database()
  self.temp=tempfile.TemporaryDirectory();self.a=Application(self.temp.name);self.s=Server(('127.0.0.1',0),Handler);self.s.application=self.a;threading.Thread(target=self.s.serve_forever,daemon=True).start()
  self.i=self.a.add_demo('jobpass');self.a.dispatcher.tick();self.d=next(d for d in self.a.store.snapshot()['deliveries'] if d['role']=='OWNER')
 def tearDown(self):self.s.shutdown();self.s.server_close();self.temp.cleanup()
 def act(self,action,actor,revision,delivery=None):
  payload={'delivery_id':delivery or self.d['delivery_id'],'actor':actor,'action':action,'revision':revision}
  req=urllib.request.Request('http://127.0.0.1:%s/api/demo/chat-action'%self.s.server_port,data=json.dumps(payload).encode(),headers={'X-Bridge-Token':self.a.token,'Content-Type':'application/json'})
  with urllib.request.urlopen(req) as r:return json.load(r)
 def test_channel_workflow_and_ownership(self):
  self.assertEqual(self.act('ack','DEMO_OWNER_A',1)['workflow_status'],'IN_PROGRESS')
  with self.assertRaises(urllib.error.HTTPError) as e:self.act('complete','DEMO_OWNER_A',2)
  self.assertEqual(e.exception.code,409) # channel has not been synchronized yet
  self.a.dispatcher.tick()
  with self.assertRaises(urllib.error.HTTPError) as e:self.act('complete','DEMO_OWNER_B',2)
  self.assertEqual(e.exception.code,403)
  self.assertEqual(self.act('complete','DEMO_OWNER_A',2)['workflow_status'],'COMPLETED')
 def test_monitor_channel_cannot_act(self):
  monitor=next(d for d in self.a.store.snapshot()['deliveries'] if d['role']=='MONITOR')
  with self.assertRaises(urllib.error.HTTPError) as e:self.act('ack','DEMO_OWNER_A',1,monitor['delivery_id'])
  self.assertEqual(e.exception.code,409)
 def test_demo_endpoint_disabled_in_live(self):
  self.a.config['mode']='live'
  with self.assertRaises(urllib.error.HTTPError) as e:self.act('ack','DEMO_OWNER_A',1)
  self.assertEqual(e.exception.code,403)
