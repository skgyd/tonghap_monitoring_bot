"""Private Mattermost slash command for the dedicated contest team."""
import hmac
import shlex
import urllib.error
from urllib.parse import quote

from bridge.store import QUERIES, row, uid

HELP = """**IncidentBridge · 관제팀 명령** (앞에 `/ib`를 붙이세요)
- `화면` · `상태` · `목록` · `사건 원본이벤트ID` · `상세 사건ID` · `이력 사건ID`
- 오류 발생은 시연자 PC의 IncidentBridge 운영자 웹에서 실행합니다. · `원본제거 사건ID`
- `채널` · `매핑` · `매핑 잡패스 CMS ~finance` · `매핑삭제 잡패스 CMS`
- `채널변경 사건ID ~corporate-accounting` · `담당변경 사건ID @monitoring02` · `재개 사건ID 사유`
- `조회 incidents 1` · `조회 verification` · `전송시험 실패` 또는 `전송시험 복구`
- `재시도 전송ID` · `재시도 전송ID 확인` (게시 여부 확인 후) · `동기화재시도 동기화ID`
접수·완료는 업무 채널의 장애 게시물 버튼을 누르세요. 사건 ID는 앞 8자 이상으로 특정할 수 있습니다.
매핑은 새 사건부터 적용합니다. 기존 사건 한 건은 채널변경을 사용하세요."""
SOURCES = {'폴스타':'polestar', '잡패스':'jobpass', 'polestar':'polestar', 'jobpass':'jobpass'}


def source_name(name):
    if name not in SOURCES:
        raise ValueError('폴스타 또는 잡패스를 선택하세요.')
    return SOURCES[name]


def lookup(items, key, prefix):
    matches = [item for item in items if item[key].startswith(prefix)]
    if len(prefix) < 8 or len(matches) != 1:
        raise ValueError('ID 앞 8자 이상 또는 전체 ID로 한 건을 지정하세요.')
    return matches[0]


def clean(value):
    return str(value or '—').replace('\n', ' ').replace('\r', ' ').replace('|', '｜').replace('`', '′')[:120]


class Commands:
    def __init__(self, app, settings):
        self.a = app
        self.s = settings

    def execute(self, fields):
        if (not self.s.get('token') or not hmac.compare_digest(fields.get('token', '').encode(), self.s['token'].encode())
                or fields.get('team_id') != self.s['team'] or fields.get('command') != '/ib'
                or fields.get('user_id') not in self.s['judges'].values()):
            raise PermissionError('인증되지 않은 요청입니다.')
        if fields.get('channel_id') != self.s['monitor']:
            return '관제팀 채널에서 /ib 명령을 사용하세요. 업무 채널에서는 장애 게시물의 접수·완료 버튼을 사용합니다.'
        words = shlex.split(fields.get('text', ''))
        op, args = (words[0], words[1:]) if words else ('도움', [])
        return self.run(op, args, fields['user_id'])

    def channels(self):
        data = self.a.mm.api('GET', '/users/me/teams/' + self.s['team'] + '/channels')
        return [ch for ch in data if ch['type'] == 'O' and ch.get('team_id') == self.s['team']
                and not ch.get('delete_at') and ch['id'] != self.s['monitor']]

    def target(self, name, actor):
        name = name.lstrip('~')
        matches = [ch for ch in self.channels() if name in (ch['name'], ch['display_name'], ch['id'])]
        if len(matches) != 1:
            raise ValueError('대상 채널을 특정할 수 없습니다. /ib 채널에서 봇이 참여한 공개 채널의 ~이름을 확인하세요.')
        ch = matches[0]
        self.require_member(ch['id'], actor, '대상 채널 참여자만 지정할 수 있습니다.')
        return ch

    def require_member(self, channel, user, hint):
        try:
            member = self.a.mm.api('GET', '/channels/' + channel + '/members/' + user)
        except urllib.error.HTTPError as exc:
            if exc.code not in (403,404):
                raise
            raise ValueError(hint) from exc
        if member.get('user_id') != user:
            raise PermissionError(hint)

    def routing(self, op, args, actor):
        if op == '채널' and not args:
            return '\n'.join('~' + ch['name'] + ' · ' + ch['display_name'] for ch in self.channels()) or '지정 가능한 채널이 없습니다.'
        if op == '매핑' and not args:
            names = {ch['id']: '~' + ch['name'] for ch in self.channels()}
            return '\n'.join(f"{r['source']} / {clean(r['target_key'])} → {names.get(r['channel_id'], r['channel_id'])}" for r in self.a.store.snapshot()['routes'] if r['enabled']) or '등록된 매핑이 없습니다.'
        if len(args) != (3 if op == '매핑' else 2):
            raise ValueError('예: /ib 매핑 잡패스 CMS ~finance 또는 /ib 매핑삭제 잡패스 CMS. 띄어쓴 업무명은 큰따옴표로 감싸세요.')
        source, key = source_name(args[0]), args[1].strip()
        if not key or len(key) > 100 or key == '*':
            raise ValueError('그룹경로에서 읽을 정확한 업무명을 입력하세요.')
        channel = self.target(args[2], actor) if op == '매핑' else None
        def change(d):
            routes = d['routes']
            existing = next((r for r in routes if r['source'] == source and r['target_key'] == key), None)
            if channel:
                if existing:
                    existing.update(channel_id=channel['id'], enabled=True)
                elif len(routes) < 100:
                    routes.append(row('routes', route_id=uid(), source=source, target_key=key, channel_id=channel['id'], enabled=True))
                else:
                    raise ValueError('매핑 한도는 100개입니다.')
            elif existing:
                routes.remove(existing)
            else:
                raise ValueError('삭제할 매핑이 없습니다.')
        self.a.store.transaction(change)
        return f"{source} / {clean(key)} → " + ('~' + channel['name'] if channel else '매핑 삭제') + ' · 다음 신규 사건부터 적용'

    def run(self, op, args, actor):
        a = self.a
        if op in ('도움', 'help'):
            return HELP
        if op == '화면' and not args:
            return '[폴스타(왼쪽) · 잡패스(오른쪽) 열기](' + self.s.get('source_origin', self.s['origin']) + '/ib-sources/both)'
        if op in ('매핑', '매핑삭제') or (op == '채널' and not args):
            return self.routing(op, args, actor)
        if op == '조회' and not args:
            return '조회 가능: ' + ', '.join(QUERIES) + ' · /ib 조회 incidents 1'
        if op == '조회' and len(args) in (1, 2):
            result = a.store.query(args[0])
            page = int(args[1]) if len(args) == 2 else 1
            size = 5
            pages = max(1, (result['row_count'] + size - 1) // size)
            if not 1 <= page <= pages:
                raise ValueError(f'페이지 범위: 1~{pages}')
            rows = result['rows'][(page-1)*size:page*size]
            if not rows:
                return '조회 결과 0행'
            return f"{result['label']} · {result['row_count']}행 중 {page}/{pages}쪽\n" + '\n'.join(
                ' · '.join(f'{k}=`{clean(v)}`' for k,v in zip(result['columns'], entry)) for entry in rows)
        if op == '발생':
            return '시연자 PC의 IncidentBridge 운영자 웹에서 폴스타 또는 잡패스 오류 발생을 누르세요.'
        if op == '상태' and not args:
            d = a.state()
            return '전송 실패 시험 ' + ('켜짐' if d['simulated_failure'] else '꺼짐') + '\n' + '\n'.join(
                f"{r['collector_id']} · {r['health_status']} · {r['message']}" for r in d['collector_state']) + '\n' + '\n'.join(d['errors'])
        if op == '전송시험' and args in (['실패'], ['복구']):
            a.dispatcher.simulate_failure = args == ['실패']
            return '전송 시험 설정: ' + args[0]
        if op in ('재시도','동기화재시도') and len(args) in (1,2):
            if op == '동기화재시도':
                m = lookup(a.store.snapshot()['activity_mirrors'], 'mirror_id', args[0])
                if m['status'] not in ('RETRY','MANUAL_REVIEW'):
                    raise ValueError('실패한 동기화만 재시도할 수 있습니다.')
                a.activity.update(m, status='PENDING',next_retry_at='',last_error='')
            else:
                d = lookup(a.store.snapshot()['deliveries'], 'delivery_id', args[0])
                if d['status'] not in ('RETRY','MANUAL_REVIEW'):
                    raise ValueError('실패한 전송만 재시도할 수 있습니다.')
                if d['status']=='MANUAL_REVIEW' and args[1:] != ['확인']:
                    raise ValueError('채널의 기존 게시물을 확인한 뒤 끝에 확인을 붙이세요.')
                a.dispatcher.update(d['delivery_id'],status='RETRY',next_retry_at='')
            return '재시도를 예약했습니다.'
        data = a.store.snapshot()
        if op == '목록' and not args:
            return '\n'.join(f"`{i['incident_id']}` · {clean(i['job_name'])} · {i['workflow_status']}" for i in sorted(data['incidents'],key=lambda i:i['detected_at'],reverse=True)[:20]) or '등록된 장애가 없습니다.'
        if op == '사건' and len(args)==1:
            i = lookup(data['incidents'],'source_event_id',args[0])
            return '사건 ID `' + i['incident_id'] + '`'
        if op not in ('상세','이력','채널변경','담당변경','재개','원본제거') or not args:
            raise ValueError(HELP)
        i = lookup(data['incidents'],'incident_id',args[0])
        iid = i['incident_id']
        if op == '상세' and len(args)==1:
            return f"`{iid}` · {clean(i['message'])}\n처리 {i['workflow_status']} · 원본 {i['source_status']} · 담당 {i['assignee_id'] or '미지정'}\n" + '\n'.join(
                f"{d['role']} · {d['status']} · " + (f"[게시물]({self.s['origin']}/_redirect/pl/{quote(d['post_id'])})" if d['post_id'] else d['delivery_id']) for d in data['deliveries'] if d['incident_id']==iid)
        if op == '이력' and len(args)==1:
            return '\n'.join(f"{v['created_at']} · {v['action_type']} · {v['actor_id']} · {clean(v['note'])}" for v in sorted(data['actions'],key=lambda v:v['created_at'],reverse=True) if v['incident_id']==iid) or '처리 이력이 없습니다.'
        if op == '원본제거' and len(args)==1:
            with a.mock_lock:
                a.mock[i['source']] = [e for e in a.mock[i['source']] if e['event_id'] != i['source_event_id']]
                a.save_demo_events()
            return '원본 목록에서 제거했습니다. 처리 상태는 별도로 유지됩니다.'
        if op == '채널변경' and len(args)==2:
            ch = self.target(args[1], actor)
            if ch['id'] == i['channel_id']:
                return '이미 해당 채널이 담당합니다.'
            if i['workflow_status'] == 'IN_PROGRESS' and i['assignee_id']:
                self.require_member(ch['id'], i['assignee_id'], '현재 담당자를 새 채널에 초대하거나 양쪽 채널에 참여한 담당자를 먼저 지정하세요.')
            a.engine.action(iid,'reroute',actor,i['revision'],note='~'+ch['name'],admin=True,channel=ch['id'])
            return f"사건 `{iid}` 담당 → ~{ch['name']} · 신규 사건 매핑은 그대로입니다."
        if op == '담당변경' and len(args)==2:
            target = self.s['judges'].get(args[1].lstrip('@'))
            if not target:
                raise ValueError('등록된 심사 계정을 지정하세요.')
            self.require_member(i['channel_id'], target, '담당자가 업무 채널에 참여한 뒤 지정하세요.')
            a.engine.action(iid,'assign',actor,i['revision'],note=target,admin=True)
            return '담당자를 변경했습니다.'
        if op == '재개' and len(args)>=2:
            a.engine.action(iid,'reopen',actor,i['revision'],note=' '.join(args[1:]),admin=True)
            return '장애를 재개했습니다.'
        raise ValueError(HELP)
