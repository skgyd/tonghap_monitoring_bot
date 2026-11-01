import unittest,threading
from bridge.collector import Collector
class Node:
 def __init__(self,data):self.data=data
 def locator(self,s):return Node(self.data[s])
 def inner_text(self):return str(self.data)
 def get_attribute(self,s):return self.data.get(s)
class FieldsTests(unittest.TestCase):
 def test_polestar_stable_key_ignores_duration(self):
  c={'field_selectors':{'system':'sys','occurred_at':'time','hostname':'host'},'event_key_fields':['occurred_at','hostname']}
  x=Collector(None,'.','polestar',c,threading.Event());a=x.read_row(Node({'sys':'업무A','time':'2026-01-01','host':'서버A'}));self.assertEqual(a[1],'업무A');self.assertIn('hostname: 서버A',a[2]);self.assertEqual(len(a[0]),64)
 def test_missing_execution_id_rejected(self):
  c={'field_selectors':{'execution_id':'id'},'event_key_fields':['execution_id']};x=Collector(None,'.','jobpass',c,threading.Event())
  with self.assertRaises(ValueError):x.read_row(Node({'id':''}))
 def test_jobpass_path_and_task(self):
  c={'id_attribute':'id','field_selectors':{'path':'path','job_name':'job','task_name':'task'}};x=Collector(None,'.','jobpass',c,threading.Event());a=x.read_row(Node({'id':'run1','path':'/업무A/','job':'작업A','task':'태스크A'}));self.assertEqual(a[1],'/업무A/');self.assertIn('태스크A',a[2])
 def test_business_from_each_product_path(self):
  for source,path,business in [('polestar','/IT운영/기업회계/','기업회계'),('jobpass','/IT운영/금융거래','금융거래')]:
   c={'id_attribute':'id','job_selector':'path','business_from_path':True,'business_segment':-1}
   value=Collector(None,'.',source,c,threading.Event()).read_row(Node({'id':'run1','path':path}))
   self.assertEqual(value[1],business);self.assertEqual(value[0],'run1')
 def test_business_path_missing_rejected(self):
  c={'id_attribute':'id','job_selector':'path','business_from_path':True}
  with self.assertRaises(ValueError):Collector(None,'.','polestar',c,threading.Event()).read_row(Node({'id':'run1','path':' / / '}))
 def test_configurable_business_segment(self):
  c={'id_attribute':'id','job_selector':'path','business_from_path':True,'business_segment':1}
  value=Collector(None,'.','jobpass',c,threading.Event()).read_row(Node({'id':'run1','path':'/IT운영/금융거래/야간배치'}))
  self.assertEqual(value[1],'금융거래')
