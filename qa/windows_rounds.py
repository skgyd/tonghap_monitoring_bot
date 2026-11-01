"""Run from VERIFY-WINDOWS.cmd with the demo stopped. Starts two dedicated rounds."""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(Path(__file__).resolve().parent)]
from portable_bootstrap import check_ports, browser_channel, HTTP
from mm_check import Client, verify
from playwright.sync_api import sync_playwright

BASE='http://127.0.0.1:8764'


def control(token,path,body=None):
    req=urllib.request.Request(BASE+path,
        headers={'X-Launcher-Token':token,'Origin':BASE,'Content-Type':'application/json'},
        data=None if body is None else json.dumps(body).encode())
    with HTTP.open(req,timeout=8) as response:return json.load(response)


def exercise(again, old_token=''):
    check_ports()
    proc=subprocess.Popen([sys.executable,'-B',str(ROOT/'portable_bootstrap.py'),'--no-browser'],cwd=ROOT)
    data=None; token=None; ready=None
    try:
        deadline=time.monotonic()+1100
        while time.monotonic()<deadline and proc.poll() is None:
            for f in (ROOT/'work/sessions').glob('*/launch.json'):
                info=json.loads(f.read_text('utf-8'))
                if info.get('pid')==proc.pid:
                    data=f.parent;token=info['url'].split('#token=')[1]
                    status=control(token,'/api/status')
                    if status['ready']:ready=status
            if ready:break
            time.sleep(1)
        if not ready:raise RuntimeError('서버 초기화 실패 또는 준비 시간 초과')
        if old_token:
            try:control(old_token,'/api/status')
            except urllib.error.HTTPError as exc:assert exc.code==403
            else:raise RuntimeError('이전 운영자 자격이 유효합니다.')
        accounts = {a['username']:a['password'] for a in ready['accounts']}
        assert accounts == {f'monitoring{i:02d}': f'Demo!{i}' for i in range(1, 6)}
        for name, password in accounts.items():
            client = Client()
            user = client.api('POST','/users/login',{'login_id':name,'password':password})
            assert user['username']==name
            client.api('POST','/users/logout')
        cred=next(a for a in ready['accounts'] if a['username']=='monitoring01')
        for key in ('TMP','TEMP','TMPDIR'):
            os.environ[key]=str(data/'temp')
        import tempfile
        tempfile.tempdir=str(data/'temp')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel=browser_channel() or None)
            try:
                ctx=browser.new_context()
                page=ctx.new_page()
                page.goto(info['url'])
                page.wait_for_function("document.getElementById('phase').textContent==='시연 준비 완료'", timeout=15000)
                assert page.get_by_text('아래 계정을 이용해서 시연하세요',exact=True).is_visible()
                assert page.locator('#judges details').count()==0
                assert page.locator('#accounts tr').all_text_contents()==[name+pw for name,pw in accounts.items()]
                # No Mattermost cookies or login in this browser context yet.
                for view, sources in [('polestar',['polestar']),('jobpass',['jobpass']),('both',['polestar','jobpass'])]:
                    with ctx.expect_page() as opened:
                        page.locator('[data-view="'+view+'"]').click()
                    window=opened.value
                    try:
                        for system in sources:
                            pane=window.frame_locator('iframe[src$="/'+system+'"]') if view=='both' else window
                            pane.locator('#ready').wait_for()
                            assert pane.locator('#count').inner_text()=='0'
                    finally:window.close()
                if again:
                    c=Client()
                    c.token=''
                    c.api('POST','/users/login',{'login_id':cred['username'],'password':cred['password']})
                    try:
                        c.team=c.api('GET','/teams/name/bridge-lab')['id']; c.monitor=c.channel('monitor')
                        assert '등록된 장애가 없습니다' in c.cmd('목록')
                        assert 'qa-check' not in c.cmd('매핑')
                        for source in ('polestar','jobpass'):
                            assert not json.loads(c.source('/ib-sources/data/'+source))
                        try:c.channel('qa-check')
                        except urllib.error.HTTPError as exc:assert exc.code==404
                        else:raise RuntimeError('지난 회차의 채널이 남아 있습니다.')
                    finally:c.api('POST','/users/logout')
                else:
                    c=Client()
                    def fire(source):
                        with page.expect_response(lambda res:res.url==BASE+'/api/operator/events') as pending:
                            page.locator('#'+source).click()
                        result=pending.value.json()
                        assert result['source']==source
                        event=result['event_id']
                        ctx.add_cookies([{'name':'MMAUTHTOKEN','value':c.token,'url':'http://127.0.0.1:8780'}])
                        source_page=ctx.new_page()
                        try:
                            source_page.goto('http://127.0.0.1:8780/ib-sources/both')
                            pane=source_page.frame_locator('iframe[src$="/'+source+'"]')
                            row=pane.locator('[data-event-id="'+event+'"]')
                            row.wait_for()
                            if source=='jobpass':
                                row.dblclick();assert event in pane.locator('#detail-log').inner_text()
                            else:
                                assert row.locator('.critical-chip').inner_text()=='critical'
                                assert row.locator('td').last.evaluate("td => getComputedStyle(td).backgroundColor")=='rgb(248, 198, 213)'
                                assert pane.locator('#critical-count').inner_text()=='1'
                        finally:source_page.close()
                        return event
                    checked=verify(c,cred['username'],cred['password'],fire)
                    assert len(checked['verified_incidents'])==2
                    c.token=''
                    c.api('POST','/users/login',{'login_id':cred['username'],'password':cred['password']})
                    try:
                        c.api('POST','/channels',{'team_id':c.team,'name':'qa-check','display_name':'QA Check','type':'O'})
                        assert '신규 사건' in c.cmd('매핑 잡패스 qa-check ~finance')
                    finally:c.api('POST','/users/logout')
                page.once('dialog',lambda dialog:dialog.dismiss())
                page.locator('#stop').click()
                assert control(token,'/api/status')['ready']
                page.once('dialog',lambda dialog:dialog.accept())
                with page.expect_response(lambda res:res.url==BASE+'/api/stop') as stop:
                    page.locator('#stop').click()
                assert stop.value.ok
            finally:browser.close()
    finally:
        if proc.poll() is None and token:
            try:control(token,'/api/stop',{})
            except (OSError,ValueError):pass
        try:code=proc.wait(timeout=180)
        except subprocess.TimeoutExpired:
            proc.terminate();proc.wait(timeout=20);raise
        if code or data is None or data.exists():raise RuntimeError('종료 또는 회차 정리 실패')
    return token


if __name__=='__main__':
    import tempfile
    from unittest.mock import patch
    with patch.dict(os.environ),patch.object(tempfile,'tempdir',None):
        token=exercise(False)
    with patch.dict(os.environ),patch.object(tempfile,'tempdir',None):
        exercise(True,token)
    print('PASS: web buttons, Mattermost delivery/actions, two clean rounds and discarded data.')
