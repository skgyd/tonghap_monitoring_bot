import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import urllib.error

from provision import prepare


class FakeProvisionAPI:
    base = 'http://127.0.0.1:8065'
    def __init__(self):
        self.resources = {}
        self.users = {}
        self.passwords = {}
        self.tokens = {}
        self.tokens_created = 0
        self.min_password_length = 8
    def call(self, method, path, payload=None, token=''):
        if path == '/config/patch' and method == 'PUT':
            self.min_password_length = payload['PasswordSettings']['MinimumLength']
            return {}, ''
        if path.startswith('/commands?'):
            return self.resources.get('cmds', []), ''
        if path=='/commands' and method=='POST':
            cmd=dict(payload, id='cmdid', token='command-token')
            self.resources['cmds']=[cmd]
            return cmd, ''
        if path.startswith('/channels/') and path.endswith('/patch'):
            channel=next(c for k,c in self.resources.items() if isinstance(c,dict) and c.get('id')==path.split('/')[2])
            channel.update(payload)
            return channel, ''
        if path == '/system/ping': return {'status': 'OK'}, ''
        if path == '/users/login':
            name = payload['login_id']
            if name not in self.users or self.passwords[name] != payload['password']:
                raise urllib.error.HTTPError(path, 401, 'invalid', {}, None)
            tok = 'login-' + name
            self.tokens[tok] = self.users[name]
            return self.users[name], tok
        if path == '/users/logout': return {}, ''
        if path == '/users/me': return self.tokens[token], ''
        if method == 'GET':
            if path not in self.resources: raise urllib.error.HTTPError(path, 404, 'missing', {}, None)
            return self.resources[path], ''
        if path == '/users':
            if len(payload['password']) < self.min_password_length:
                raise AssertionError('Password shorter than guest configuration allows')
            user = {**payload, 'id': payload['username'] + 'id', 'roles': 'system_admin' if not self.users else 'system_user'}
            self.users[payload['username']] = user
            self.passwords[payload['username']] = payload['password']
            self.resources['/users/username/' + payload['username']] = user
            return user, ''
        if path == '/bots':
            user = {**payload, 'id': 'botid'}
            self.users[payload['username']] = user
            self.resources['/users/username/' + payload['username']] = user
            return user, ''
        if path.endswith('/tokens'):
            self.tokens_created += 1
            self.tokens['bot-token'] = self.users['incidentbridge']
            return {'token': 'bot-token'}, ''
        if path == '/teams':
            obj = {**payload, 'id': 'teamid'}
            self.resources['/teams/name/' + payload['name']] = obj
            return obj, ''
        if path == '/channels':
            obj = {**payload, 'id': payload['name'] + 'id'}
            self.resources['/teams/teamid/channels/name/' + payload['name']] = obj
            return obj, ''
        if path.endswith('/members'):
            self.resources[path + '/' + payload['user_id']] = payload
            return payload, ''
        raise AssertionError((method, path))


class ProvisionTests(unittest.TestCase):
    def test_portable_monitoring_accounts_login_after_repeated_setup(self):
        expected = {f'monitoring{i:02d}': f'Demo!{i}' for i in range(1, 6)}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'MM_ADMIN_PASSWORD': 'AdminPassword123!'}):
            api = FakeProvisionAPI()
            for _ in range(2):
                settings, channels = prepare(tmp, api, portable=True)
                self.assertEqual(set(settings['judges']), set(expected))
                for username, password in expected.items():
                    user, _ = api.call('POST', '/users/login', {'login_id': username, 'password': password})
                    self.assertEqual(user['id'], settings['judges'][username])
                    self.assertEqual(user['roles'], 'system_user')
                    self.assertIn('/teams/' + settings['team'] + '/members/' + user['id'], api.resources)
                    for channel in channels.values():
                        self.assertIn('/channels/' + channel['id'] + '/members/' + user['id'], api.resources)

    def test_provision_real_mode_defaults_and_restart_preserves_changes(self):
        from bridge.store import Store
        env = {'MM_ADMIN_PASSWORD': 'AdminPassword123!', **{f'JUDGE{i}_PASSWORD': 'JudgePassword123!' for i in range(1, 4)}}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env):
            api = FakeProvisionAPI()
            settings, channels = prepare(tmp, api)
            self.assertEqual(len(settings['judges']), 3)
            self.assertEqual(set(channels), {'관제팀', '기업회계', '금융거래'})
            self.assertEqual(settings['token'], 'command-token')
            store = Store(Path(tmp) / 'incidents.db')
            store.transaction(lambda d: d['routes'][0].update(channel_id=channels['금융거래']['id']))
            previous = store.snapshot()['routes']
            prepare(tmp, api)
            self.assertEqual(store.snapshot()['routes'], previous)
            self.assertEqual(api.tokens_created, 1)
            self.assertEqual(len(api.resources['cmds']),1)
            self.assertEqual((Path(tmp) / 'bot-token.txt').stat().st_mode & 0o777, 0o600)


