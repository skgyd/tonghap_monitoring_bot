import http.cookiejar, json, re, time, urllib.error, urllib.request, uuid

class Client:
    def __init__(self):
        self.origin = 'http://127.0.0.1:8065'
        handlers = [urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),urllib.request.ProxyHandler({})]
        self.http=urllib.request.build_opener(*handlers)
        self.token=''
    def api(self, method, path, body=None):
        headers={'Content-Type':'application/json', 'X-Requested-With':'XMLHttpRequest'}
        if self.token: headers['Authorization']='Bearer '+self.token
        request=urllib.request.Request(self.origin+'/api/v4'+path,headers=headers,method=method,
                  data=None if body is None else json.dumps(body).encode())
        with self.http.open(request,timeout=20) as response:
            token=response.headers.get('Token')
            if token:self.token=token
            data=response.read()
            return json.loads(data) if data else {}
    def cmd(self, words):
        return self.api('POST','/commands/execute',{'team_id':self.team,'channel_id':self.monitor,
                                                 'command':'/ib '+words})['text']
    def source(self, path):
        with self.http.open('http://127.0.0.1:8780'+path,timeout=10) as response:return response.read()
    def channel(self,slug):
        return self.api('GET',f'/teams/{self.team}/channels/name/{slug}')['id']
    def post(self, pid):
        return self.api('GET','/posts/'+pid)
    def thread(self, pid):
        return self.api('GET','/posts/'+pid+'/thread')['posts'].values()

def wait(fn, label):
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        result=fn()
        if result:return result
        time.sleep(2)
    raise RuntimeError('시간 초과: '+label)

def require(value, label):
    if not value:raise RuntimeError(label)
    return value

def identifier(text):
    hit=re.search(r'`([a-f0-9-]{36})`',text)
    return hit.group(1) if hit else ''

def delivery(c,iid,role):
    match=re.search(r'^'+role+r' · SENT · \[게시물\]\(.*/_redirect/pl/([a-z0-9]+)\)',c.cmd('상세 '+iid),re.M)
    return c.post(match.group(1)) if match else None

def buttons(post):
    return [a['id'] for block in post.get('props',{}).get('attachments',[]) for a in block.get('actions') or []]

def verify(c, username, password, fire):
    user=c.api('POST','/users/login',{'login_id':username,'password':password})
    try:
        c.team=c.api('GET','/teams/name/bridge-lab')['id']
        c.monitor=c.channel('monitor')
        require('/ib-sources/both' in c.cmd('화면'),'원본 링크')
        require(b'iframe' in c.source('/ib-sources/both'),'원본 화면 세션')
        for path in ('/sql','/welcome','/api/state','/mattermost/command'):
            try:c.source(path)
            except urllib.error.HTTPError as exc:
                if exc.code==404:continue
                raise
            raise RuntimeError('금지 경로가 공개됨: '+path)
        key='qa-'+uuid.uuid4().hex
        try:
            for target in ('finance','corporate-accounting'):
                require('신규 사건' in c.cmd(f'매핑 잡패스 {key} ~{target}'),'신규 매핑 변경')
                require(f'{key} → ~{target}' in c.cmd('매핑'),'신규 매핑 조회')
        finally:
            c.cmd('매핑삭제 잡패스 '+key)
        checked=[]
        for source in ('폴스타','잡패스'):
            require('운영자 웹' in c.cmd('발생 '+source),'채팅 발생 차단')
            event=fire('jobpass' if source=='잡패스' else 'polestar')
            iid=wait(lambda: identifier(c.cmd('사건 '+event)),source+' 수집')
            root=wait(lambda:delivery(c,iid,'OWNER'),source+' 담당 채널 게시')
            if root['channel_id'] == c.monitor:
                require('담당 →' in c.cmd('채널변경 '+iid+' ~finance'),'미분류 사건 채널 지정')
                finance=c.channel('finance')
                def routed():
                    post=delivery(c,iid,'OWNER')
                    return post if post and post['channel_id']==finance else None
                root=wait(routed,source+' 업무 채널 이관')
            monitor=wait(lambda:delivery(c,iid,'MONITOR'),source+' 관제팀 게시')
            pid,mid=root['id'],monitor['id']
            expected_files=3 if source=='잡패스' else 1
            wait(lambda:len(c.post(pid).get('file_ids',[]))>=expected_files,'캡처 파일')
            before=set(c.post(pid)['file_ids'])
            for action in ('ack','complete'):
                wait(lambda:action in buttons(c.post(pid)),action+' 버튼')
                c.api('POST','/posts/'+pid+'/actions/'+action,{})
            wait(lambda:'담당자 처리 완료' in c.post(mid)['message'],'완료 반영')
            require(before.issubset(c.post(pid)['file_ids']),'첨부 유지')
            require(len(c.post(mid).get('file_ids',[]))>=expected_files, '관제팀 첨부')
            reply=c.api('POST','/posts',{'channel_id':root['channel_id'],'root_id':pid,'message':'검증 '+iid})
            rid=reply['id']
            mirror=wait(lambda:next((p['id'] for p in c.thread(mid) if p.get('props',{}).get('bridge_source_post_id')==rid),None),'답글 전달')
            c.api('PUT','/posts/'+rid+'/patch',{'message':'수정검증 '+iid})
            wait(lambda:'수정검증' in c.post(mirror)['message'],'답글 수정')
            c.api('POST','/reactions',{'user_id':user['id'],'post_id':pid,'emoji_name':'eyes'})
            def has_reaction():
                return any(r['emoji_name']=='eyes' for r in (c.api('GET','/posts/'+mid+'/reactions') or []))
            wait(has_reaction,'이모지 반영')
            c.api('DELETE',f"/users/{user['id']}/posts/{pid}/reactions/eyes")
            wait(lambda:not has_reaction(),'이모지 취소')
            c.api('DELETE','/posts/'+rid)
            wait(lambda:all(p['id']!=mirror or p.get('delete_at') for p in c.thread(mid)),'답글 삭제')
            require('재개했습니다' in c.cmd('재개 '+iid+' 채널변경검증'),'재개')
            new_slug='finance' if root['channel_id']!=c.channel('finance') else 'corporate-accounting'
            require('담당 →' in c.cmd('채널변경 '+iid+' ~'+new_slug),'사건 채널 변경')
            def moved():
                new=delivery(c,iid,'OWNER')
                return new and new['channel_id']==c.channel(new_slug) and 'ack' in buttons(new) and not buttons(c.post(pid))
            wait(moved,'새 채널 버튼 활성 및 이전 버튼 제거')
            require(new_slug in c.cmd('이력 '+iid),'채널변경 이력')
            checked.append(iid)
        require('workflow_status' in c.cmd('조회 incidents'),'SQLite 결과')
        return {'verified_incidents':checked,'scope':'HTTP/API on this client','manual':['Windows 11에서 원본 두 화면 렌더링','브라우저 WSS 실시간 반영','두 심사 계정의 권한·재접속','Windows 심사망 시연 확인']}
    finally:
        if c.token:c.api('POST','/users/logout')

