"""The default on-machine website: routing, login, path safety, hash-only assets, cache headers."""
import http.client
import json
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import local_site
import qq_report_site

PASSWORD = 'correct horse battery'
HASH = 'a' * 64
GROUP = '10001'


def request(port, method, path, *, cookie='', body=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    headers = {}
    if cookie:
        headers['Cookie'] = cookie
    if body is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    connection.putrequest(method, path, skip_accept_encoding=True)
    for key, value in headers.items():
        connection.putheader(key, value)
    if body is not None:
        connection.putheader('Content-Length', str(len(body)))
    connection.endheaders(body.encode() if body is not None else None)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response.status, response.headers, payload


def write_qq_report(folder, stem='day-20261029'):
    for edition in ('brief', 'review'):
        record = {'edition': edition, 'chat_username': f'qq:{GROUP}', 'range_text': '窗口',
                  'window': {'start_unix': 1, 'end_unix_exclusive': 2}, 'stats': {'messages': 3}}
        (folder / f'{stem}-{edition}.json').write_text(json.dumps(record), encoding='utf-8')
        (folder / f'{stem}-{edition}.html').write_text(f'<html><body class="digest-v2">{edition}</body></html>', encoding='utf-8')
        (folder / f'{stem}-{edition}.png').write_bytes(b'\x89PNG fixture')
    assets = folder / 'assets'
    assets.mkdir(exist_ok=True)
    (assets / f'{HASH}.jpg').write_bytes(b'jpeg')
    (assets / 'notahash.jpg').write_bytes(b'private')


class Site(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.qq = self.root / 'qq'
        self.qq.mkdir()
        write_qq_report(self.qq)
        self.build = self.root / 'build'
        (self.build / 'reports').mkdir(parents=True)
        (self.build / 'assets').mkdir()
        (self.build / 'index.html').write_text('微信列表', encoding='utf-8')
        (self.build / 'reports/day.html').write_text('微信报告', encoding='utf-8')
        (self.build / 'assets' / f'{HASH}.png').write_bytes(b'png')
        (self.root / 'secret.txt').write_text('secret', encoding='utf-8')
        self.config_path = self.root / 'site.json'
        self.verifier = qq_report_site.hash_password(PASSWORD)
        self.write_config()
        self.wechat = local_site.WeChatSite(self.root / 'reports', self.build)
        self.wechat.signature = []
        self.wechat.checked = float('inf')
        self.start(self.wechat)

    def write_config(self, **changes):
        config = {'bind': '127.0.0.1', 'port': 0, 'username': 'reader', 'password_scrypt': self.verifier,
                  'qq_groups': {GROUP: {'name': '演示群', 'reports_dir': str(self.qq)}}}
        config.update(changes)
        self.config_path.write_text(json.dumps(config), encoding='utf-8')

    def start(self, wechat):
        self.config = local_site.load_config(self.config_path)
        self.server = local_site.create_server(self.config, wechat)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def login(self):
        status, headers, _ = request(self.port, 'POST', '/login', body=f'username=reader&password={PASSWORD.replace(" ", "+")}&next=/wechat/')
        self.assertEqual((status, headers['Location']), (303, '/wechat/'))
        return headers['Set-Cookie'].split(';', 1)[0]


class AuthTests(Site):
    def test_every_content_route_requires_login(self):
        for path in ('/', '/about', '/wechat/', '/wechat/reports/day.html', f'/qq/{GROUP}/', f'/qq/{GROUP}/day-20261029-brief.html'):
            status, headers, _ = request(self.port, 'GET', path)
            self.assertEqual(status, 303, path)
            self.assertTrue(headers['Location'].startswith('/login?next='), path)
        for path in (f'/wechat/assets/{HASH}.png', f'/qq/{GROUP}/assets/{HASH}.jpg'):
            self.assertEqual(request(self.port, 'GET', path)[0], 401, path)

    def test_wrong_credentials_are_rejected_without_a_cookie(self):
        for body in ('username=reader&password=nope', 'username=other&password=' + PASSWORD.replace(' ', '+')):
            status, headers, _ = request(self.port, 'POST', '/login', body=body)
            self.assertEqual(status, 401)
            self.assertIsNone(headers['Set-Cookie'])

    def test_session_cookie_flags_and_logout(self):
        status, headers, _ = request(self.port, 'POST', '/login', body=f'username=reader&password={PASSWORD.replace(" ", "+")}')
        self.assertEqual(status, 303)
        flags = headers['Set-Cookie']
        self.assertIn('HttpOnly', flags)
        self.assertIn('SameSite=Lax', flags)
        self.assertNotIn('Secure', flags)
        cleared = request(self.port, 'POST', '/logout', body='')[1]['Set-Cookie']
        self.assertIn('Max-Age=0', cleared)

    def test_secure_cookie_can_be_required(self):
        self.write_config(cookie_secure=True)
        self.start(self.wechat)
        self.assertIn('Secure', request(self.port, 'POST', '/login', body=f'username=reader&password={PASSWORD.replace(" ", "+")}')[1]['Set-Cookie'])

    def test_forged_expired_or_old_password_tokens_fail(self):
        future = 9_999_999_999
        self.assertTrue(local_site.token_valid(local_site.make_token(self.verifier, future), self.verifier))
        self.assertFalse(local_site.token_valid(local_site.make_token(self.verifier, 100), self.verifier))
        self.assertFalse(local_site.token_valid(f'{future}.' + '0' * 64, self.verifier))
        self.assertFalse(local_site.token_valid(local_site.make_token(self.verifier, future), qq_report_site.hash_password('another password')))
        for token in ('', 'x', '1.2', f'{future}'):
            self.assertFalse(local_site.token_valid(token, self.verifier))

    def test_post_login_redirect_stays_on_this_site(self):
        for target in ('https://evil.example/', '//evil.example/', '/\\evil.example', 'relative', '/a\nb'):
            self.assertEqual(local_site.safe_next(target), '/', target)
        self.assertEqual(local_site.safe_next('/wechat/reports/day.html'), '/wechat/reports/day.html')

    def test_repeated_failures_are_throttled(self):
        with patch.object(local_site, 'LOGIN_MAX_FAILURES', 2):
            for _ in range(2):
                self.assertEqual(request(self.port, 'POST', '/login', body='username=reader&password=nope')[0], 401)
            good = f'username=reader&password={PASSWORD.replace(" ", "+")}'
            self.assertEqual(request(self.port, 'POST', '/login', body=good)[0], 429)

    def test_oversized_form_is_refused(self):
        self.assertEqual(request(self.port, 'POST', '/login', body='a=' + 'b' * 9000)[0], 400)

    def test_health_check_reveals_nothing(self):
        status, _, body = request(self.port, 'GET', '/healthz')
        self.assertEqual((status, body), (200, b'ok\n'))


class RoutingTests(Site):
    def test_reports_and_lists_are_served_after_login(self):
        cookie = self.login()
        self.assertIn('微信列表'.encode(), request(self.port, 'GET', '/wechat/', cookie=cookie)[2])
        self.assertIn('微信报告'.encode(), request(self.port, 'GET', '/wechat/reports/day.html', cookie=cookie)[2])
        listing = request(self.port, 'GET', f'/qq/{GROUP}/', cookie=cookie)[2].decode()
        self.assertIn('day-20261029-brief.html', listing)
        page = request(self.port, 'GET', f'/qq/{GROUP}/day-20261029-brief.html', cookie=cookie)[2].decode()
        self.assertIn('返回报告列表', page)
        status, headers, _ = request(self.port, 'GET', f'/qq/{GROUP}/day-20261029-brief.png', cookie=cookie)
        self.assertEqual((status, headers['Content-Type']), (200, 'image/png'))
        home = request(self.port, 'GET', '/', cookie=cookie)[2].decode()
        self.assertIn('/wechat/', home)
        self.assertIn(f'/qq/{GROUP}/', home)
        self.assertIn('站点说明', request(self.port, 'GET', '/about', cookie=cookie)[2].decode())

    def test_head_matches_get_without_a_body(self):
        cookie = self.login()
        status, headers, body = request(self.port, 'HEAD', f'/wechat/assets/{HASH}.png', cookie=cookie)
        self.assertEqual((status, headers['Content-Length'], body), (200, '3', b''))

    def test_single_source_goes_straight_to_its_list(self):
        self.write_config(qq_groups={})
        self.start(self.wechat)
        self.assertEqual(request(self.port, 'GET', '/', cookie=self.login())[1]['Location'], '/wechat/')

    def test_qq_only_deployment_has_no_wechat_routes(self):
        self.start(None)
        cookie = self.login()
        self.assertEqual(request(self.port, 'GET', '/wechat/', cookie=cookie)[0], 404)
        self.assertEqual(request(self.port, 'GET', '/', cookie=cookie)[1]['Location'], f'/qq/{GROUP}/')

    def test_unfinished_build_is_a_clear_503(self):
        (self.build / 'index.html').unlink()
        self.assertEqual(request(self.port, 'GET', '/wechat/', cookie=self.login())[0], 503)


class PathSafetyTests(Site):
    def test_traversal_and_private_files_are_not_reachable(self):
        cookie = self.login()
        os.symlink(self.root / 'secret.txt', self.build / 'reports' / 'link.html')
        os.symlink(self.root / 'secret.txt', self.build / 'assets' / f'{"b" * 64}.png')
        for path in ('/wechat/../secret.txt', '/wechat/%2e%2e/secret.txt', '/wechat/reports/..%2f..%2fsecret.txt',
                     '/wechat/reports/link.html', f'/wechat/assets/{"b" * 64}.png', '/wechat/reports/day.json',
                     f'/qq/{GROUP}/../secret.txt', f'/qq/{GROUP}/%2e%2e/secret.txt', f'/qq/{GROUP}/day-20261029-brief.json',
                     f'/qq/{GROUP}/day-20261029-brief.sources.json', '/qq/99999/', '/nothing'):
            status, _, body = request(self.port, 'GET', path, cookie=cookie)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b'secret', body)

    def test_images_are_served_only_by_content_hash(self):
        cookie = self.login()
        self.assertEqual(request(self.port, 'GET', f'/qq/{GROUP}/assets/{HASH}.jpg', cookie=cookie)[2], b'jpeg')
        for name in ('notahash.jpg', f'{"A" * 64}.jpg', f'{HASH}.exe', f'{HASH}.json', f'{HASH[:-1]}.jpg'):
            self.assertEqual(request(self.port, 'GET', f'/qq/{GROUP}/assets/{name}', cookie=cookie)[0], 404, name)
        self.assertEqual(request(self.port, 'GET', f'/wechat/assets/sub/{HASH}.png', cookie=cookie)[0], 404)


class HeaderTests(Site):
    def test_pages_are_never_cached_but_hashed_assets_are(self):
        cookie = self.login()
        for path in ('/', '/about', '/wechat/', '/wechat/reports/day.html', f'/qq/{GROUP}/', f'/qq/{GROUP}/day-20261029-brief.html'):
            headers = request(self.port, 'GET', path, cookie=cookie)[1]
            self.assertEqual(headers['Cache-Control'], 'private, no-store', path)
        self.assertEqual(request(self.port, 'GET', '/login')[1]['Cache-Control'], 'private, no-store')
        headers = request(self.port, 'GET', f'/wechat/assets/{HASH}.png', cookie=cookie)[1]
        self.assertEqual(headers['Cache-Control'], 'private, max-age=31536000, immutable')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(headers['X-Frame-Options'], 'DENY')

    def test_error_pages_use_the_site_style(self):
        status, headers, body = request(self.port, 'GET', '/nothing', cookie=self.login())
        self.assertEqual((status, headers['Content-Type']), (404, 'text/html; charset=utf-8'))
        self.assertIn('页面不存在'.encode(), body)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'runtime' / 'local-site.json'
        self.path.parent.mkdir()

    def test_init_writes_a_private_file_once_and_never_stores_the_password(self):
        created = local_site.init_config(self.path)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(created['url'], 'http://127.0.0.1:8787/')
        self.assertNotIn(created['password'], self.path.read_text(encoding='utf-8'))
        self.assertTrue(qq_report_site.verify_password(created['password'], local_site.load_config(self.path)['password_scrypt']))
        self.assertIsNone(local_site.init_config(self.path))

    def test_set_password_replaces_the_verifier_and_invalidates_sessions(self):
        created = local_site.init_config(self.path)
        before = local_site.load_config(self.path)['password_scrypt']
        token = local_site.make_token(before, 9_999_999_999)
        local_site.set_password(self.path, 'a brand new password')
        after = local_site.load_config(self.path)['password_scrypt']
        self.assertFalse(qq_report_site.verify_password(created['password'], after))
        self.assertFalse(local_site.token_valid(token, after))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_only_loopback_and_valid_values_are_accepted(self):
        good = {'username': 'reader', 'password_scrypt': qq_report_site.hash_password('long enough password')}
        bad = [{'bind': '0.0.0.0'}, {'bind': '192.0.2.1'}, {'port': 70000}, {'port': '80'}, {'username': ''}, {'username': 'a:b'},
               {'password_scrypt': 'REPLACE_WITH_VERIFIER'}, {'session_hours': 0}, {'cookie_secure': 'yes'}, {'qq_groups': []},
               {'qq_groups': {'abc': {'name': 'x', 'reports_dir': '/'}}}, {'qq_groups': {'1': {'name': 'x', 'reports_dir': '/does/not/exist'}}}]
        for change in bad:
            self.path.write_text(json.dumps({**good, **change}), encoding='utf-8')
            with self.assertRaises(ValueError, msg=str(change)):
                local_site.load_config(self.path)
        self.path.write_text(json.dumps(good), encoding='utf-8')
        loaded = local_site.load_config(self.path)
        self.assertEqual((loaded['bind'], loaded['port'], loaded['session_hours']), ('127.0.0.1', 8787, 720))

    def test_example_is_rejected_until_a_real_verifier_is_set(self):
        example = Path(__file__).resolve().parent.parent / 'examples' / 'local-site.example.json'
        with self.assertRaisesRegex(ValueError, 'password_scrypt'):
            local_site.load_config(example)


class RebuildTests(unittest.TestCase):
    def test_rebuilds_on_change_and_keeps_serving_after_a_failed_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reports, output = root / 'reports', root / 'site'
            reports.mkdir()
            output.mkdir()
            site = local_site.WeChatSite(reports, output)
            calls = []

            def build(source, target):
                calls.append(source)
                if len(calls) == 2:
                    raise RuntimeError('boom')
                (target / 'index.html').write_text('ok', encoding='utf-8')

            with patch('report_site.build_site', build), patch('sys.stderr'):
                self.assertTrue(site.ensure_built())
                self.assertEqual(len(calls), 1)
                site.checked = float('-inf')
                self.assertTrue(site.ensure_built())
                self.assertEqual(len(calls), 1, 'unchanged reports must not rebuild')
                (reports / 'new.json').write_text('{}', encoding='utf-8')
                site.checked = float('-inf')
                self.assertTrue(site.ensure_built())
                self.assertEqual(len(calls), 2)
                self.assertTrue((output / 'index.html').is_file())


if __name__ == '__main__':
    unittest.main()
