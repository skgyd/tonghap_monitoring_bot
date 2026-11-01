import os,unittest,tempfile,json
from pathlib import Path
from sql_support import reset_database
from bridge.store import Store,StoreError
from bridge.engine import Engine
class DesktopTests(unittest.TestCase):
 def setUp(self):self.store=reset_database()
 def test_sqlite_backup_is_reopenable(self):
  cfg=json.loads((Path(__file__).resolve().parents[1]/'config.example.json').read_text());Engine(self.store,cfg).ingest('polestar','event','기업회계','failure')
  dest=self.store.path.parent/'backup.db';self.store.backup(dest);self.assertEqual(Store(dest).snapshot(),self.store.snapshot())
 def test_readonly_select_blocks_writes(self):
  with self.assertRaises(StoreError):
   with self.store.connection(readonly=True) as c:c.execute("DELETE FROM meta")
 def test_no_arbitrary_sql_query(self):
  with self.assertRaises(ValueError):self.store.query('DROP TABLE incidents')
 def test_sqlite_foreign_key_blocks_orphan(self):
  with self.assertRaises(StoreError):
   with self.store.connection() as c:c.execute("INSERT INTO actions(action_id,incident_id) VALUES ('x','missing')")
 def test_non_windows_never_saves_plaintext_token(self):
  from bridge.vault import save
  if os.name=='nt':self.skipTest('Windows DPAPI integration must be tested on Windows')
  with self.assertRaises(RuntimeError):save(self.store.path.parent/'token','secret')
  self.assertFalse((self.store.path.parent/'token').exists())
