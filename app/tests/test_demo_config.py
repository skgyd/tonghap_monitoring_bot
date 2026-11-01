import unittest
from demo_launcher import configuration
from app import validate_config
class DemoConfigTests(unittest.TestCase):
 def test_quick_ready_configuration(self):
  c=configuration(False);validate_config(c);self.assertTrue(c['collectors']['jobpass']['enabled']);self.assertEqual(c['collectors']['jobpass']['detail_click'],'dblclick')
 def test_real_configuration_separates_routing_and_allowed_actor(self):
  c=configuration(True,{'관제팀':{'id':'monitor'}},{'id':'owner'},{'id':'admin'});validate_config(c);self.assertEqual(c['mattermost']['allowed_user_ids'],['owner','admin']);self.assertTrue(c['local_lab'])
