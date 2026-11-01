"""Embedded SQL storage. No DB server, pandas, openpyxl or external driver required."""
import copy,json,os,sqlite3
from contextlib import contextmanager
from pathlib import Path
from .schema import SCHEMA,now,uid,row

class StoreError(RuntimeError):pass
INTS={'revision','missing_count','attempt_count','sent_revision','source_updated_at'}
BOOLS={'enabled','unmapped'}
QUERIES={t:(t,'SELECT * FROM '+t+' ORDER BY '+cols[0]+' LIMIT 200') for t,cols in SCHEMA.items()}
QUERIES['verification']=('송수신 대조',"SELECT i.job_name,i.workflow_status,i.assignee_id,i.completed_at,(SELECT COUNT(*) FROM deliveries d WHERE d.incident_id=i.incident_id AND d.status='SENT' AND d.sent_revision=i.revision) AS synced_posts FROM incidents i ORDER BY i.detected_at LIMIT 200")
QUERIES['tables']=('테이블 구조',"SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 200")

class Store:
 def __init__(self,path=None):
  self.path=Path(path or os.environ['BRIDGE_SQLITE_PATH']);self.path.parent.mkdir(parents=True,exist_ok=True);self.error='';self.dsn=str(self.path)
  with self.connection() as c:
   c.execute('PRAGMA journal_mode=WAL')
   for table,cols in SCHEMA.items():
    declarations=[]
    for col in cols:
     typ='INTEGER' if col in INTS or col in BOOLS else 'TEXT'
     dec='"'+col+'" '+typ+(' PRIMARY KEY' if col==cols[0] else " NOT NULL DEFAULT "+('0' if typ=='INTEGER' else "''"))
     if col=='incident_id' and table!='incidents':dec+=' REFERENCES incidents(incident_id)'
     if col in ('source_delivery_id','target_delivery_id'):dec+=' REFERENCES deliveries(delivery_id)'
     if col=='workflow_status':dec+=" CHECK(workflow_status IN ('NEW','IN_PROGRESS','COMPLETED'))"
     if col=='source_status':dec+=" CHECK(source_status IN ('PRESENT','ABSENT'))"
     if col in INTS:dec+=' CHECK("'+col+'">=0)'
     declarations.append(dec)
    if table=='incidents':declarations.append('UNIQUE(source,source_event_id)')
    if table=='deliveries':declarations.append('UNIQUE(incident_id,channel_id)')
    if table=='activity_mirrors':declarations.append('UNIQUE(target_delivery_id,source_post_id,kind)')
    c.execute('CREATE TABLE IF NOT EXISTS "'+table+'" ('+','.join(declarations)+')')
   c.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','desktop-1')")
   if c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]!='desktop-1':raise StoreError('Unsupported DB version')
   c.execute('CREATE INDEX IF NOT EXISTS deliveries_status ON deliveries(status,next_retry_at)')
 @contextmanager
 def connection(self,readonly=False):
  c=None
  try:
   c=sqlite3.connect(str(self.path),timeout=10);c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=10000')
   if readonly:c.execute('PRAGMA query_only=ON')
   with c:yield c
   self.error=''
  except sqlite3.Error as exc:
   self.error='SQL 저장 실패: '+str(exc);raise StoreError(self.error) from None
  finally:
   if c is not None:c.close()
 @staticmethod
 def validate(data):
  for table,cols in SCHEMA.items():
   keys=[r[cols[0]] for r in data[table]]
   if len(keys)!=len(set(keys)) or any(not k for k in keys):raise ValueError('Duplicate/empty key: '+table)
   if any(set(r)!=set(cols) for r in data[table]):raise ValueError('Invalid columns')
  events=[(r['source'],r['source_event_id']) for r in data['incidents']]
  if len(events)!=len(set(events)):raise ValueError('Duplicate source event')
  for r in data['incidents']:
   if r['workflow_status'] not in ('NEW','IN_PROGRESS','COMPLETED') or r['source_status'] not in ('PRESENT','ABSENT'):raise ValueError('Invalid incident state')
 def _read(self,c):
  data={}
  for table,cols in SCHEMA.items():
   order=next((x for x in ('detected_at','created_at','updated_at','captured_at') if x in cols),cols[0])
   rows=c.execute('SELECT '+','.join('"'+x+'"' for x in cols)+' FROM "'+table+'" ORDER BY "'+order+'","'+cols[0]+'"').fetchall()
   data[table]=[{col:(bool(v) if col in BOOLS else '' if v is None else v) for col,v in zip(cols,r)} for r in rows]
  return data
 def snapshot(self):
  with self.connection(readonly=True) as c:
   c.execute('BEGIN');return self._read(c)
 def _write_diff(self,c,before,after):
  for table,cols in reversed(list(SCHEMA.items())):
   keep={r[cols[0]] for r in after[table]}
   for old in before[table]:
    if old[cols[0]] not in keep:c.execute('DELETE FROM "'+table+'" WHERE "'+cols[0]+'"=?',(old[cols[0]],))
  for table,cols in SCHEMA.items():
   previous={r[cols[0]]:r for r in before[table]}
   for r in after[table]:
    if previous.get(r[cols[0]])==r:continue
    values=[int(r[x] or 0) if x in INTS else int(str(r[x]).lower() in ('true','1')) if x in BOOLS else str(r[x] or '') for x in cols]
    query='INSERT INTO "'+table+'" ('+','.join('"'+x+'"' for x in cols)+') VALUES ('+','.join('?' for x in cols)+') ON CONFLICT("'+cols[0]+'") DO UPDATE SET '+','.join('"'+x+'"=excluded."'+x+'"' for x in cols[1:])
    c.execute(query,values)
 def transaction(self,operation):
  with self.connection() as c:
   c.execute('BEGIN IMMEDIATE');before=self._read(c);after=copy.deepcopy(before);result=operation(after);self.validate(after);self._write_diff(c,before,after)
  return copy.deepcopy(result)
 def row(self,table,key):return next((r for r in self.snapshot()[table] if r[SCHEMA[table][0]]==key),None)
 def query(self,name):
  if name not in QUERIES:raise ValueError('Unknown SELECT query')
  label,query=QUERIES[name]
  with self.connection(readonly=True) as c:
   cur=c.execute(query);cols=[x[0] for x in cur.description];rows=cur.fetchall()
  return {'database':self.path.name,'engine':'SQLite '+sqlite3.sqlite_version,'label':label,'sql':query+';','columns':cols,'rows':rows,'row_count':len(rows),'limit':200,'queried_at':now(),'readonly':True,'queries':[{'id':k,'label':v[0]} for k,v in QUERIES.items()]}
 def backup(self,path):
  with self.connection(readonly=True) as c:
   dest=sqlite3.connect(str(path))
   try:c.backup(dest)
   finally:dest.close()
