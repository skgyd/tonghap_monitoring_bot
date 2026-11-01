import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from app import Application, Server
from bridge.engine import Conflict
from bridge.mattermost import Mattermost
from bridge.schema import now, uid
from commands import Commands
from hosted import Callback, SourceHandler


class MattermostInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.dict(os.environ, {'BRIDGE_SQLITE_PATH':str(Path(self.tmp.name)/'incidents.db')}):
            self.a = Application(self.tmp.name)
        self.a.config['mattermost'].update(monitor_channel='monitor', callback_allowed_ips=['127.0.0.1/32'])
        self.a.config.update(local_lab=True,persist_demo=True)
        self.settings = {'token':'secret','team':'team','monitor':'monitor','judges':{'monitoring01':'j1','monitoring02':'j2'}, 'origin':'https://contest.org'}
        self.commands = Commands(self.a, self.settings)
        self.channels = [{'id':n,'name':n,'display_name':l,'team_id':'team','type':'O'} for n,l in
                         [('monitor','관제팀'),('finance','금융거래'),('accounting','기업회계'),('new-target','추가채널')]]
        self.a.mm.api = Mock(side_effect=lambda method,path: self.channels if path.endswith('/channels') else {'user_id':path.rsplit('/',1)[-1]})
        self.a.store.transaction(lambda d: d['routes'].clear())
        self.fields = dict(token='secret',command='/ib',team_id='team',user_id='j1',channel_id='monitor')
    def cmd(self, words):
        return self.commands.execute(dict(self.fields,text=words))
    def incident(self):
        return self.a.engine.ingest('jobpass',uid(),'금융거래','test')
    def http(self,handler):
        srv=Server(('127.0.0.1',0),handler)
        srv.application=self.a
        srv.commands=self.commands
        srv.api=Mock()
        srv.api.call.return_value=({'id':'j1'},'')
        srv.settings=self.settings
        threading.Thread(target=srv.serve_forever,daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        def request(method,path,body=None,headers=None):
            conn=http.client.HTTPConnection('127.0.0.1',srv.server_port,timeout=3)
            conn.request(method,path,body,headers or {})
            r=conn.getresponse()
            result=(r.status,r.read(),dict(r.getheaders()))
            conn.close()
            return result
        return srv,request
    def test_callback_uses_form_secret_actor_team_and_monitor(self):
        _,req=self.http(Callback)
        def post(**updates):
            f=dict(self.fields,text='채널');f.update(updates)
            return req('POST','/mattermost/command',urlencode(f),{'Content-Type':'application/x-www-form-urlencoded'})
        for args in ({'token':'bogus'},{'team_id':'bad'},{'user_id':'spoof'}):
            self.assertEqual(post(**args)[0],403)
        self.assertIn('관제팀 채널',json.loads(post(channel_id='finance')[1])['text'])
        body=json.loads(post()[1]);self.assertEqual(body['response_type'],'ephemeral')
        self.assertIn('~finance',body['text'])
    def test_source_views_only_for_mattermost_session(self):
        srv,req=self.http(SourceHandler)
        self.assertEqual(req('GET','/ib-sources/both')[0],401)
        auth={'Cookie':'MMAUTHTOKEN=opaque'}
        for path in ('/ib-sources/both','/ib-sources/polestar','/ib-sources/jobpass','/ib-sources/data/jobpass','/ib-sources/source-ui.js'):
            status,body,headers=req('GET',path,headers=auth)
            self.assertEqual(status,200)
            self.assertNotIn(b'opaque',body)
            self.assertEqual('SAMEORIGIN',headers['X-Frame-Options'])
        self.assertEqual(req('GET','/ib-sources/source-ui.js')[0],401)
        for name, marker in [('polestar', b'id="polestar"'), ('jobpass', b'id="running"')]:
            page=req('GET','/ib-sources/'+name,headers=auth)[1]
            self.assertIn(marker,page)
            self.assertIn(b'<script src="source-ui.js" defer>',page)
            self.assertNotIn(b'<!--SOURCE-PANEL-->',page)
        srv.api.call.assert_called_with('GET','/users/me',token='opaque')
        srv.api.call.return_value=({'id':'outsider'},'')
        self.assertEqual(req('GET','/ib-sources/both',headers=auth)[0],403)
        for path in ('/sql','/welcome','/api/state','/api/db/incidents','/mattermost/command','/session/login'):
            self.assertEqual(req('GET',path,headers=auth)[0],404)
            self.assertEqual(req('POST',path,'{}',auth)[0],404)
    def test_mapping_existing_incident_and_future_incident_are_independent(self):
        self.cmd('매핑 잡패스 금융거래 ~finance')
        a=self.incident()
        self.cmd('매핑 잡패스 금융거래 기업회계')
        self.assertIn('~accounting',self.cmd('매핑'))
        b=self.incident()
        self.assertEqual(self.a.store.row('incidents',a['incident_id'])['channel_id'],'finance')
        self.assertEqual(b['channel_id'],'accounting')
        self.cmd('채널변경 '+a['incident_id'][:8]+' ~new-target')
        self.assertEqual(self.incident()['channel_id'],'accounting')
        ds=[x for x in self.a.store.snapshot()['deliveries'] if x['incident_id']==a['incident_id']]
        self.assertEqual({x['channel_id']:x['role'] for x in ds},{'monitor':'MONITOR','finance':'PREVIOUS','new-target':'OWNER'})
        current=self.a.store.row('incidents',a['incident_id'])
        old=next(x for x in ds if x['role']=='PREVIOUS')
        old.update(post_id='old')
        mm=Mattermost(self.a.config['mattermost'],self.tmp.name,'secret')
        payload=mm.payload(current,old,[])
        self.assertFalse(payload['props']['attachments'][0]['actions'])
        ctx=dict(action='ack',revision=a['revision'],signature=mm.sign(old['delivery_id'],a['revision'],'ack'))
        with self.assertRaisesRegex(PermissionError,'current owner'):
            mm.authorize(dict(context=ctx,post_id='old',channel_id='finance'),old,current)
        self.assertIn('~new-target',self.cmd('이력 '+a['incident_id']))
        self.cmd('매핑삭제 잡패스 금융거래')
        unrouted=self.incident()
        self.assertTrue(unrouted['unmapped'])
        self.cmd('채널변경 '+unrouted['incident_id']+' ~finance')
        ds=[x for x in self.a.store.snapshot()['deliveries'] if x['incident_id']==unrouted['incident_id']]
        self.assertEqual({x['role'] for x in ds},{'OWNER','MONITOR'})
    def test_channels_must_be_public_on_same_team_with_membership(self):
        self.channels.extend([dict(self.channels[1],id='secret',name='secret',type='P'),
                              dict(self.channels[1],id='other',name='other',team_id='other')])
        for channel in ('monitor','secret','other','unknown'):
            with self.assertRaises(ValueError):self.cmd('매핑 폴스타 업무 '+channel)
        self.a.mm.api.side_effect=lambda method,path:self.channels if path.endswith('/channels') else {'user_id':'outsider'}
        with self.assertRaises(PermissionError):self.cmd('매핑 폴스타 업무 finance')
        self.assertFalse(self.a.store.snapshot()['routes'])
    def test_readonly_sql_and_reassign_and_reopen(self):
        i=self.incident();iid=i['incident_id']
        self.assertIn(iid,self.cmd('조회 incidents'));self.assertIn(iid,self.cmd('목록'))
        with self.assertRaises(ValueError):self.cmd('조회 DELETE')
        self.cmd('담당변경 '+iid+' @monitoring02')
        with self.assertRaises(ValueError):self.a.engine.action(iid,'complete','j1')
        self.a.engine.action(iid,'complete','j2')
        self.cmd('재개 '+iid+' 재발')
        self.assertEqual(self.a.store.row('incidents',iid)['workflow_status'],'NEW')
        self.assertIn('재발', self.cmd('이력 '+iid))
    def test_active_assignee_must_be_able_to_use_destination(self):
        self.cmd('매핑 잡패스 금융거래 ~finance')
        iid=self.incident()['incident_id']
        self.cmd('담당변경 '+iid+' @monitoring02')
        self.a.mm.api.side_effect=lambda method,path:self.channels if path.endswith('/channels') else {'user_id':'j1'}
        with self.assertRaises(PermissionError): self.cmd('채널변경 '+iid+' ~accounting')
        self.assertEqual(self.a.store.row('incidents',iid)['channel_id'],'finance')

    def test_duplicate_source_event_keeps_its_original_route_and_mapping_persists(self):
        self.cmd('매핑 잡패스 금융거래 ~finance')
        i=self.incident()
        self.cmd('매핑 잡패스 금융거래 ~accounting')
        repeat=self.a.engine.ingest('jobpass',i['source_event_id'],'금융거래','test')
        self.assertEqual(repeat['incident_id'],i['incident_id'])
        self.assertEqual(repeat['channel_id'],'finance')
        from bridge.store import Store
        reopened=Store(self.a.store.path)
        self.assertEqual(reopened.snapshot()['routes'][0]['channel_id'],'accounting')
        self.assertEqual(len(reopened.snapshot()['incidents']),1)

    def test_source_generation_is_not_direct_ingestion(self):
        result=self.cmd('발생 잡패스')
        self.assertIn('IncidentBridge 운영자 웹', result)
        self.assertFalse(self.a.mock['jobpass'])
        self.assertFalse(self.a.store.snapshot()['incidents'])
        self.assertIn('/ib-sources/both', self.cmd('화면'))


class TriggerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.a = Application(self.root)
        self.a.config.update(local_lab=True, persist_demo=True)
        (self.root/'config.json').write_text(json.dumps(self.a.config))
        self.commands=None; self.settings={}
        self.srv, self.req = MattermostInterfaceTests.http(self, SourceHandler)
        self.srv.operator_key = 'e'*64
        self.rid = uid()

    def fire(self, src='jobpass', auth=None, extra=None):
        headers = {'Content-Type':'application/json', 'Authorization':'Bearer '+'e'*64}
        if auth is not None:headers['Authorization'] = auth
        headers.update(extra or {})
        return self.req('POST','/ib-operator/events',json.dumps({'source':src,'request_id':self.rid}),headers)[0]

    def test_requires_operator_credential_and_rejects_browser_posts(self):
        self.assertEqual(self.fire(auth='',extra={'Cookie':'MMAUTHTOKEN=opaque'}),403)
        self.assertEqual(self.fire(extra={'Origin':'https://contest.org'}),403)
        self.assertEqual(self.fire(extra={'Content-Type':'text/plain'}),415)
        self.assertEqual(self.fire('unknown'),400)
        self.a.config['local_lab']=False
        self.assertEqual(self.fire(),403)
        self.assertFalse(any(self.a.mock.values()))

    def test_retry_across_threads_restart_and_removal(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(lambda _:self.fire(),range(4))),[200]*4)
        self.assertEqual(len(self.a.mock['jobpass']),1)
        self.assertEqual(self.a.mock['jobpass'][0]['business_name'],'금융거래')
        self.assertFalse(self.a.store.snapshot()['incidents'])
        self.assertEqual(self.fire('polestar'),409)
        with self.a.mock_lock:
            self.a.mock['jobpass'].clear()
            self.a.save_demo_events()
        self.srv.application = self.a = Application(self.root)
        self.assertEqual(self.fire(),200)
        self.assertFalse(any(self.a.mock.values()))
        self.rid = uid()
        self.assertEqual(self.fire(),200)
        from demo_events import BUSINESSES
        self.assertIn(self.a.mock['jobpass'][0]['business_name'],BUSINESSES)
        self.rid = uid()
        self.assertEqual(self.fire('polestar'),200)
        self.assertEqual(self.a.mock['polestar'][0]['business_name'],'기업회계')

    def test_concurrent_new_clicks_use_the_default_business_only_once_per_source(self):
        from concurrent.futures import ThreadPoolExecutor
        from demo_events import BUSINESSES
        for source, first in [('polestar','기업회계'),('jobpass','금융거래')]:
            with ThreadPoolExecutor(max_workers=5) as pool:
                events=list(pool.map(lambda _: self.a.add_demo(source,direct=False),range(5)))
            self.assertEqual(sum(e['business_name']==first for e in events),1)
            self.assertEqual(self.a.mock[source][0]['business_name'],first)
            self.assertTrue(all(e['business_name'] in BUSINESSES for e in self.a.mock[source][1:]))
            self.assertTrue(self.a.mock[source][0]['group_path'].endswith('/'+first))

    def test_failed_write_can_be_retried(self):
        with patch.object(self.a,'save_demo_events',side_effect=OSError('full')):
            self.assertEqual(self.fire(),503)
        self.assertFalse(self.a.issued)
        self.assertFalse(any(self.a.mock.values()))
        self.assertEqual(self.fire(),200)
        self.assertEqual(self.a.mock['jobpass'][0]['business_name'],'금융거래')


    def test_first_request_is_selected_once_and_persisted_across_restart(self):
        from concurrent.futures import ThreadPoolExecutor
        from demo_events import make_event
        with patch('app.make_event', wraps=make_event) as factory:
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(list(pool.map(lambda _: self.fire(), range(4))), [200] * 4)
            factory.assert_called_once()
            event = dict(self.a.mock['jobpass'][0])
            self.assertEqual(event['job_name'], '금융거래')
            self.assertIn(event['message'], event['execution_log'])
            self.srv.application = self.a = Application(self.root)
            self.assertEqual(self.fire(), 200)
            factory.assert_called_once()
            self.assertEqual(self.a.mock['jobpass'], [event])
