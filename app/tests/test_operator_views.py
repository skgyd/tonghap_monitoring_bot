"""Follow operator links as a separate read-only browser, without a judge session."""
from contextlib import closing
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from uuid import uuid4

windows=Path(__file__).resolve().parents[2]/'windows'
if windows.is_dir():sys.path.insert(0,str(windows))
from portable_bootstrap import Control, Launcher
from app import Application


class Markup(HTMLParser):
    def __init__(self, tag, key):
        super().__init__();self.tag=tag;self.key=key;self.items=[]
    def handle_starttag(self, name, attrs):
        if name==self.tag and self.key in dict(attrs):self.items.append(dict(attrs)[self.key])


class OperatorViews(unittest.TestCase):
    def test_open_all_views_readonly_and_match_generated_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch=Launcher(tmp)
            app=Application(tmp);app.config.update(portable=True,local_lab=True,persist_demo=True)
            launch.bridge=SimpleNamespace(a=app);launch.ready=True
            with ThreadingHTTPServer(('127.0.0.1',0),Control) as server:
                server.launcher=launch
                worker=threading.Thread(target=server.serve_forever);worker.start()
                try:self.exercise(server.server_port,launch)
                finally:server.shutdown();worker.join()

    def exercise(self, port, launch):
        def request(path, headers=None, body=None):
            h={'Host':'127.0.0.1:8764',**(headers or {})}
            if body is not None:h.update({'Content-Type':'application/json','Origin':'http://127.0.0.1:8764'})
            with closing(http.client.HTTPConnection('127.0.0.1',port,timeout=3)) as c:
                c.request('GET' if body is None else 'POST',path,body,h);r=c.getresponse()
                return r.status,dict(r.getheaders()),r.read().decode()
        base='/operator-sources/'
        code,_,html=request('/')
        nav=Markup('a','data-view');nav.feed(html)
        self.assertEqual((code,nav.items),(200,['polestar','jobpass','both']))
        auth={'X-Launcher-Token':launch.token}
        launch.ready=False
        code,headers,data=request('/api/status',auth)
        start=json.loads(data)
        self.assertEqual(code,200)
        self.assertEqual(start['accounts'],[{'username':f'monitoring{i:02d}','password':f'Demo!{i}'} for i in range(1,6)])
        self.assertNotIn('links',start)
        self.assertNotIn('Set-Cookie',headers)
        launch.ready=True
        self.assertEqual(request('/api/status')[0],403)
        self.assertEqual(request(base+'both')[0],403)
        cookie=request('/api/status',auth)[1]['Set-Cookie']
        self.assertIn('HttpOnly',cookie);self.assertIn('Path=/operator-sources/',cookie)
        viewer={'Cookie':cookie.split(';')[0]}
        code,headers,pair=request(base+'both',viewer);frames=Markup('iframe','src');frames.feed(pair)
        self.assertEqual(code,200);self.assertEqual(headers['X-Frame-Options'],'SAMEORIGIN')
        self.assertEqual(frames.items,[base+'polestar',base+'jobpass'])
        self.assertEqual(request(base+'unknown',viewer)[0],404)
        for path in frames.items:
            source=path.split('/')[-1]
            code,_,single=request(path,viewer)
            self.assertEqual(code,200);self.assertIn('data-source="'+source+'"',single)
            self.assertEqual(json.loads(request(base+'data/'+source,viewer)[2]),[])
            self.assertIn('/operator-sources/data/',request(base+'source-ui.js',viewer)[2])
            rid=str(uuid4());body=json.dumps({'source':source,'request_id':rid})
            # View cookies alone cannot generate events or stop the server.
            self.assertEqual(request('/api/operator/events',viewer,body)[0],403)
            self.assertEqual(request('/api/stop',viewer,'{}')[0],403)
            self.assertEqual(request('/api/operator/events',auth,body)[0],200)
            event=json.loads(request(base+'data/'+source,viewer)[2])[0]
            self.assertEqual(event['event_id'],rid);self.assertIn(event['business_name'],event['message'])
            if source=='jobpass':self.assertIn(rid,event['execution_log'])
        self.assertEqual(request('/api/stop',auth,'{}')[0],200)
        self.assertEqual(request(base+'data/jobpass',viewer)[0],403)
        new=Launcher(launch.data);new.ready=True;new.bridge=launch.bridge
        self.assertNotEqual(new.view_token,launch.view_token)
