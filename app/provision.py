"""Idempotent provisioning of a dedicated contest Mattermost instance."""
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from bridge.mattermost import NoRedirect
from bridge.store import Store, row, uid
from demo_launcher import configuration

DEMO_JUDGES = {f'monitoring{i:02d}': f'Demo!{i}' for i in range(1, 6)}

class API:
    def __init__(self, base):
        self.base = base.rstrip('/')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)

    def call(self, method, path, payload=None, token=''):
        req = urllib.request.Request(self.base + '/api/v4' + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method=method)
        with self.opener.open(req, timeout=15) as response:
            raw = response.read()
            return (json.loads(raw) if raw else {}), response.headers.get('Token', '')


def require_env(name):
    value = os.environ.get(name, '')
    if not value:
        raise ValueError(name + ' is required. Run configure.py on the server first.')
    return value


def prepare(root, api=None, portable=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    api = api or API(os.environ.get('MM_INTERNAL_URL', 'http://mattermost:8065'))
    deadline = time.monotonic() + 300
    while True:
        try:
            api.call('GET', '/system/ping')
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise RuntimeError('Mattermost did not start within 5 minutes.')
            time.sleep(2)
    password = require_env('MM_ADMIN_PASSWORD')
    try:
        admin, token = api.call('POST', '/users/login', {'login_id': 'contest_admin', 'password': password})
    except urllib.error.HTTPError as exc:
        if exc.code not in (400, 401): raise
        api.call('POST', '/users', {'username': 'contest_admin', 'email': 'contest-admin@incidentbridge.example', 'password': password})
        admin, token = api.call('POST', '/users/login', {'login_id': 'contest_admin', 'password': password})
    if 'system_admin' not in admin.get('roles', '').split():
        raise RuntimeError('Use a new dedicated Mattermost database; contest_admin is not the system admin.')

    def get_or_create(path, create_path, data):
        try: return api.call('GET', path, token=token)[0]
        except urllib.error.HTTPError as exc:
            if exc.code != 404: raise
            return api.call('POST', create_path, data, token)[0]

    try:
        if portable:
            api.call('PUT', '/config/patch', {
                'PasswordSettings': {'MinimumLength': 6},
                'ServiceSettings': {'SiteURL':api.base,'EnableCommands':True,'EnableDesktopLandingPage':False,
                                    'EnableOnboardingFlow':False,'AllowedUntrustedInternalConnections':'10.0.2.2'},
                'LocalizationSettings': {'DefaultServerLocale':'ko','DefaultClientLocale':'ko'}}, token)
        team = get_or_create('/teams/name/bridge-lab', '/teams', {'name': 'bridge-lab', 'display_name': 'Incident Bridge 공모전 시연', 'type': 'O'})
        judges = []
        accounts = {}
        for number, (username, demo_password) in enumerate(DEMO_JUDGES.items(), 1):
            # Existing hosted deployments can opt into the two additional accounts.
            if not portable and number > 3 and not os.environ.get(f'JUDGE{number}_PASSWORD'):
                continue
            judge_password = demo_password if portable else require_env(f'JUDGE{number}_PASSWORD')
            user = get_or_create('/users/username/' + username, '/users', {
                'username': username, 'email': username + '@incidentbridge.example', 'password': judge_password,
                'first_name': '심사위원', 'last_name': str(number), 'locale': 'ko'})
            # Fail clearly after a password change instead of giving different portal/MM logins.
            checked, check_token = api.call('POST', '/users/login', {'login_id': username, 'password': judge_password})
            api.call('POST', '/users/logout', token=check_token)
            if checked['id'] != user['id']: raise RuntimeError('Judge identity mismatch')
            judges.append(user)
            accounts[username] = {'user_id': user['id']}
        bot = get_or_create('/users/username/incidentbridge', '/bots', {
            'username': 'incidentbridge', 'display_name': 'Incident Bridge', 'description': 'Contest demo bridge'})
        bot_id = bot.get('id') or bot['user_id']
        users = [admin, *judges, {'id': bot_id}]
        for user in users:
            get_or_create('/teams/' + team['id'] + '/members/' + user['id'], '/teams/' + team['id'] + '/members',
                          {'team_id': team['id'], 'user_id': user['id']})
        channels = {}
        for slug, display in [('monitor', '관제팀'), ('corporate-accounting', '기업회계'), ('finance', '금융거래')]:
            ch = get_or_create('/teams/' + team['id'] + '/channels/name/' + slug, '/channels',
                               {'team_id': team['id'], 'name': slug, 'display_name': display, 'type': 'O'})
            if ch['display_name'] != display:
                ch = api.call('PUT', '/channels/' + ch['id'] + '/patch', {'display_name': display}, token)[0]
            channels[display] = ch
            for user in users:
                get_or_create('/channels/' + ch['id'] + '/members/' + user['id'], '/channels/' + ch['id'] + '/members', {'user_id': user['id']})
        callback = os.environ.get('BRIDGE_CALLBACK_BASE', 'http://bridge:8767').rstrip('/')
        expected_url = callback + '/mattermost/command'
        commands = api.call('GET', '/commands?custom_only=true&team_id=' + team['id'], token=token)[0]
        command = next((item for item in commands if item['trigger'] == 'ib'), None)
        if command is None:
            command = api.call('POST', '/commands', {
                'team_id': team['id'], 'trigger': 'ib', 'method': 'P', 'url': expected_url,
                'auto_complete': True, 'display_name': 'IncidentBridge', 'auto_complete_hint': '도움',
                'auto_complete_desc': '화면, 장애 조회, 담당 채널 매핑 및 변경'}, token)[0]
        if command['url'] != expected_url or command['method'] != 'P' or not command.get('token'):
            raise RuntimeError('기존 /ib 명령의 주소·메서드·토큰을 운영자가 확인하세요.')
        monitor = channels['관제팀']
        hint = '시연 시작: /ib 도움 · 원본: /ib 화면 · 담당 채널: /ib 매핑'
        header = monitor.get('header', '')
        if hint not in header and len(header) + len(hint) < 1000:
            api.call('PUT', '/channels/' + monitor['id'] + '/patch', {'header': (header + '\n' + hint).strip()}, token)
        token_path = root / 'bot-token.txt'
        if token_path.exists():
            bot_token = token_path.read_text().strip()
            me, _ = api.call('GET', '/users/me', token=bot_token)
            if me['id'] != bot_id: raise RuntimeError('Stored bot token belongs to another user')
        else:
            bot_token = api.call('POST', '/users/' + bot_id + '/tokens', {'description': 'IncidentBridge contest'}, token)[0]['token']
            token_path.write_text(bot_token)
            token_path.chmod(0o600)
        os.environ['BRIDGE_MM_TOKEN'] = bot_token
        c = configuration(True, channels, judges[0], admin)
        c['hosted'] = True
        c['mattermost'].update(url=api.base, callback_base=os.environ.get('BRIDGE_CALLBACK_BASE', 'http://bridge:8767'),
            public_url=os.environ.get('MATTERMOST_ORIGIN', api.base),
            allowed_user_ids=[u['id'] for u in judges] + [admin['id']],
            callback_allowed_ips=[socket.gethostbyname(urlsplit(api.base).hostname) + '/32'])
        for collector in c['collectors'].values(): collector['browser_channel'] = ''
        (root / 'config.json').write_text(json.dumps(c, ensure_ascii=False, indent=2), 'utf-8')
        store = Store(root / 'incidents.db')
        def default_routes(data):
            # Restart must not overwrite a judge's mapping changes.
            if any(m['key'] == 'hosted_initialized' for m in data['meta']): return
            data['routes'] = [row('routes', route_id=uid(), source=source, target_key=business,
                channel_id=channels[business]['id'], enabled=True) for source, business in [('polestar', '기업회계'), ('jobpass', '금융거래')]]
            data['meta'].append(row('meta', key='hosted_initialized', value='1'))
        store.transaction(default_routes)
        settings = {'team': team['id'], 'monitor': monitor['id'], 'token': command['token'],
                    'judges': {name: info['user_id'] for name, info in accounts.items()},
                    'origin': os.environ.get('MATTERMOST_ORIGIN', api.base).rstrip('/')}
        return settings, channels
    finally:
        api.call('POST', '/users/logout', token=token)


def main():
    os.umask(0o077)
    root = Path(os.environ.get('BRIDGE_DATA', '/data'))
    root.mkdir(parents=True, exist_ok=True)
    import fcntl
    lock = open(root / 'hosted.lock', 'a')
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    settings, _ = prepare(root)
    from hosted import serve
    serve(root, settings)


if __name__ == '__main__': main()
