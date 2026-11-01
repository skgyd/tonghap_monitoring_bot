"""1.4.4 regression: browser route, two fresh rounds, and scoped disposal."""
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

windows = Path(__file__).resolve().parents[2] / 'windows'
if windows.is_dir():
    sys.path.insert(0, str(windows))
import portable_bootstrap as boot
from demo_sessions import Sessions
from app import Application, Server
from hosted import SourceHandler
from demo_events import FIRST_BUSINESS, SCENARIOS

BASE = 'http://127.0.0.1:8764'


def post(token, path, body=None, origin=BASE):
    headers = {'Content-Type':'application/json', 'X-Launcher-Token':token}
    if origin is not None:
        headers['Origin'] = origin
    with closing(http.client.HTTPConnection('127.0.0.1',8764,timeout=4)) as conn:
        conn.request('POST',path,json.dumps(body or {}),headers)
        r = conn.getresponse()
        return r.status, json.loads(r.read())


class LocalRoundTests(unittest.TestCase):
    def test_marker_and_symlink_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = root/'data'; external.mkdir()
            keep = external/'incidents.db';keep.write_bytes(b'original')
            sessions = Sessions(root)
            p = sessions.create()
            (p/'disk').write_bytes(b'stale')
            foreign = sessions.base/'other'; foreign.mkdir()
            sessions.clear_previous()
            self.assertFalse(p.exists())
            self.assertTrue(foreign.exists())
            self.assertEqual(keep.read_bytes(),b'original')
            q = sessions.create()
            sessions.marker(q).write_text('wrong')
            with self.assertRaises(RuntimeError):sessions.clear_previous()
            self.assertTrue(q.is_dir())
            if os.name!='nt':
                sessions.marker(q).write_text('IncidentBridge/ephemeral-session/v1/'+q.name)
                q.rmdir();q.symlink_to(external, target_is_directory=True)
                with self.assertRaises(RuntimeError):sessions.clear_previous()
                self.assertEqual(keep.read_bytes(),b'original')

    def test_portable_source_is_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Application(tmp)
            app.config.update(portable=True,local_lab=True)
            with Server(('127.0.0.1',0),SourceHandler) as server:
                server.application=app
                thread=threading.Thread(target=server.serve_forever);thread.start()
                try:
                    with closing(http.client.HTTPConnection('127.0.0.1',server.server_port)) as c:
                        c.request('POST','/ib-operator/events',b'{}',{'Authorization':'Bearer unused'})
                        self.assertEqual(c.getresponse().status,404)
                finally:server.shutdown();thread.join()

    def test_web_rounds_deduplicate_and_discard(self):
        # Exercise the actual launcher, HTTP handlers, source engine, SQLite and
        # finalizer. Only guest boot and Mattermost provisioning are substituted.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            seed=root/'seed';seed.write_bytes(b'unchanged')
            previous=root/'data';previous.mkdir()
            original=previous/'server.qcow2';original.write_bytes(b'old installation')
            def start(launcher):
                app=Application(launcher.data)
                app.config.update(portable=True,local_lab=True,persist_demo=True)
                launcher.bridge=SimpleNamespace(a=app)
                launcher.ready=True
                launcher.stop.wait(30)
                launcher.request_stop()
            with patch.object(boot,'ROOT',root), patch.object(boot,'contain_children'), patch.object(
                    boot.Launcher,'start',start), patch.dict(os.environ), patch.object(tempfile,'tempdir',None):
                old_token=''
                with ThreadPoolExecutor(max_workers=1) as pool:
                    for round_number in range(2):
                        future=pool.submit(boot.run,True)
                        deadline=time.monotonic()+8
                        while True:
                            if future.done():future.result()
                            launch=list((root/'work/sessions').glob('*/launch.json'))
                            if launch:
                                data=launch[0].parent
                                token=json.loads(launch[0].read_text())['url'].split('#token=')[1]
                                with closing(http.client.HTTPConnection('127.0.0.1',8764)) as c:
                                    c.request('GET','/api/status',headers={'X-Launcher-Token':token})
                                    if json.loads(c.getresponse().read())['ready']:break
                            if time.monotonic()>=deadline: self.fail('round failed to become ready')
                            time.sleep(.05)
                        try:
                            self.assertNotEqual(old_token,token)
                            if old_token:self.assertEqual(post(old_token,'/api/stop')[0],403)
                            self.assertFalse((data/'demo-events.json').exists())
                            with sqlite3.connect(data/'incidents.db') as db:
                                self.assertEqual(db.execute('select count(*) from incidents').fetchone()[0],0)
                            for source in ('polestar','jobpass'):
                                rid=str(uuid.uuid4());body={'source':source,'request_id':rid}
                                self.assertEqual(post(token,'/api/operator/events',body,'http://127.0.0.1:8065')[0],403)
                                self.assertEqual(post(token,'/api/operator/events',body,None)[0],403)
                                self.assertEqual(post('','/api/operator/events',body)[0],403)
                                for i in range(2):
                                    self.assertEqual(post(token,'/api/operator/events',body),(200,{'event_id':rid,'source':source}))
                                rows=json.loads((data/'demo-events.json').read_text())[source]
                                self.assertEqual(len(rows),1)
                                self.assertEqual(rows[0]['business_name'],FIRST_BUSINESS[source])
                                self.assertTrue(any(t.format(business=rows[0]['business_name']) in rows[0]['message'] for _,t,_ in SCENARIOS[source]))
                                other='polestar' if source=='jobpass' else 'jobpass'
                                self.assertEqual(post(token,'/api/operator/events',dict(body,source=other))[0],409)
                            self.assertEqual(post(token,'/api/stop',origin='http://evil.invalid')[0],403)
                        finally:
                            post(token,'/api/stop')
                            self.assertTrue(future.result(timeout=8))
                        self.assertFalse(data.exists())
                        self.assertEqual(list((root/'work/sessions').iterdir()),[])
                        self.assertEqual(seed.read_bytes(),b'unchanged')
                        self.assertEqual(original.read_bytes(),b'old installation')
                        old_token=token

    def test_detail_extra_failure_remains_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Application(tmp);engine=app.engine
            app.config['collectors']['jobpass']['detail_extra_capture_selectors']=['#log']
            for kind in ('LIST','DETAIL'):
                item=engine.ingest('jobpass','execution','CMS','error',kind+'.png',kind)
                self.assertEqual(item['capture_status'],'DETAIL_PENDING')
            item=engine.ingest('jobpass','execution','CMS','error','log.png','DETAIL_EXTRA_1')
            self.assertEqual(item['capture_status'],'OK')
