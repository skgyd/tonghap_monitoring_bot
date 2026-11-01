import tempfile,os
from pathlib import Path
from bridge.store import Store
_temps=[]
def reset_database():
 t=tempfile.TemporaryDirectory(prefix='bridge-sqlite-test-');_temps.append(t)
 os.environ['BRIDGE_SQLITE_PATH']=str(Path(t.name)/'test.db');return Store()
