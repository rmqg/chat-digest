#!/usr/bin/env python3
"""Private report website served directly from this machine (the default site mode).

Reads the reports under runtime/ and any configured QQ report directories. Nothing is published,
synced or tunnelled. It listens on the loopback address only and requires a password; to reach
it from elsewhere, put your own reverse proxy or tunnel in front (see docs/INSTALL.md).
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import stat
import sys
import threading
import time
from collections import deque
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

import qq_report_site
import settings
from report_web_style import error_page, site_page

CONFIG_NAME = 'local-site.json'
DEFAULT_PORT = 8787
COOKIE = 'chat_digest_session'
MAX_FORM_BYTES = 8 * 1024
LOGIN_WINDOW_SECONDS = 60
LOGIN_MAX_FAILURES = 10
REBUILD_CHECK_SECONDS = 5
CHUNK_SIZE = 64 * 1024
HTML_TYPE = 'text/html; charset=utf-8'
HASHED_ASSET = re.compile(r'[0-9a-f]{64}\.(png|jpg|jpeg|gif|webp|svg|woff|woff2|ttf|otf|eot)\Z')
REPORT_PAGE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.html\Z')
ASSET_TYPES = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp',
               'svg': 'image/svg+xml', 'woff': 'font/woff', 'woff2': 'font/woff2', 'ttf': 'font/ttf', 'otf': 'font/otf',
               'eot': 'application/vnd.ms-fontobject'}
NO_STORE = 'private, no-store'
IMMUTABLE = 'private, max-age=31536000, immutable'
QQ_BACK_LINK = '<p class="site-home"><a href="./">返回报告列表</a></p>'.encode('utf-8')
ABOUT_BODY = f'''<p class="note">这个网站运行在你自己的机器上，直接读取本机生成的日报，不经过任何第三方服务。</p>
<article><h2>怎么看报告</h2><p>首页列出所有报告，每期有「简报」和「回顾」两版。话题和金句下面的「来源」可以展开原始消息，点图片能看原图。</p></article>
<article><h2>从别的设备访问</h2><p>站点默认只监听本机回环地址。需要外网访问时，请自己配置反向代理或隧道，并保留本站的登录，方法见项目的 docs/INSTALL.md。</p></article>
<article><h2>换密码</h2><p>在项目目录运行下面的命令，按提示输入新密码，所有已登录的设备都会退出。</p><pre>./bin/chat-digest site --set-password</pre></article>'''


def config_path() -> Path:
    return Path(os.environ.get('CHAT_DIGEST_SITE_CONFIG', settings.RUNTIME / CONFIG_NAME)).expanduser()


def _valid_verifier(encoded) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split('$')
        return (scheme == 'scrypt' and (int(n), int(r), int(p)) == (2**14, 8, 1)
                and len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32)
    except (AttributeError, ValueError):
        return False


def load_config(path) -> dict:
    path = Path(path)
    config = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(config, dict) or config.get('bind', '127.0.0.1') != '127.0.0.1':
        raise ValueError('bind must be exactly 127.0.0.1; use a reverse proxy or tunnel for outside access')
    port = config.get('port', DEFAULT_PORT)
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('port must be an integer from 0 to 65535')
    username = config.get('username')
    if not isinstance(username, str) or not username or ':' in username:
        raise ValueError('username must be non-empty and must not contain a colon')
    if not _valid_verifier(config.get('password_scrypt')):
        raise ValueError('password_scrypt must be a verifier written by `chat-digest site --set-password`')
    hours = config.get('session_hours', 720)
    if type(hours) is not int or not 1 <= hours <= 8760:
        raise ValueError('session_hours must be an integer from 1 to 8760')
    if type(config.get('cookie_secure', False)) is not bool:
        raise ValueError('cookie_secure must be true or false')
    groups = config.get('qq_groups', {})
    if not isinstance(groups, dict):
        raise ValueError('qq_groups must be an object')
    validated = {}
    for group_id, group in groups.items():
        if not isinstance(group_id, str) or not qq_report_site._GROUP_ID.fullmatch(group_id) or not isinstance(group, dict):
            raise ValueError(f'invalid QQ group entry: {group_id!r}')
        name, reports_dir = group.get('name'), group.get('reports_dir')
        if not isinstance(name, str) or not name or not isinstance(reports_dir, str) or not reports_dir:
            raise ValueError(f'QQ group {group_id} requires name and reports_dir')
        root = Path(reports_dir).expanduser()
        root = (root if root.is_absolute() else settings.ROOT / root).resolve()
        if not root.is_dir():
            raise ValueError(f'reports_dir for QQ group {group_id} must be an existing directory')
        validated[group_id] = {'name': name, 'reports_dir': root}
    return {'bind': '127.0.0.1', 'port': port, 'username': username, 'password_scrypt': config['password_scrypt'],
            'session_hours': hours, 'cookie_secure': config.get('cookie_secure', False), 'qq_groups': validated}


def _write_private(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{secrets.token_hex(4)}')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    os.replace(temporary, path)


def init_config(path=None, *, owner: tuple[int, int] | None = None) -> dict | None:
    """Create the default site account once. Returns the one-time credentials, or None if configured."""
    path = Path(path) if path else config_path()
    if path.exists():
        return None
    password = secrets.token_urlsafe(12)
    _write_private(path, {'bind': '127.0.0.1', 'port': DEFAULT_PORT, 'username': 'reader',
                          'password_scrypt': qq_report_site.hash_password(password),
                          'session_hours': 720, 'cookie_secure': False, 'qq_groups': {}})
    if owner and os.geteuid() == 0:
        os.chown(path, *owner)
    return {'url': f'http://127.0.0.1:{DEFAULT_PORT}/', 'username': 'reader', 'password': password}


class WeChatSite:
    """Keeps a private built copy of the WeChat reports, rebuilt in the background when the report files change.

    A build can take minutes on a small machine with a long history, far longer than a reverse proxy waits for
    an answer, so no request ever waits for one: the previous pages stay available meanwhile.
    """

    def __init__(self, reports_dir: Path, output_dir: Path):
        self.reports_dir, self.output_dir = Path(reports_dir), Path(output_dir)
        self.lock = threading.Lock()
        self.signature = None
        self.checked = 0.0
        self.builder = None

    def _current_signature(self):
        seen = []
        for folder in (self.reports_dir, self.reports_dir.parent / 'digest' / 'sent-receipts'):
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if entry.is_file(follow_symlinks=False):
                            info = entry.stat(follow_symlinks=False)
                            seen.append((entry.path, info.st_size, info.st_mtime_ns))
            except OSError:
                continue
        return sorted(seen)

    def _build(self, current) -> None:
        try:
            import report_site
            report_site.build_site(self.reports_dir, self.output_dir)
            with self.lock:
                self.signature = current
        except Exception as exc:
            print(f'local_site: report build failed: {type(exc).__name__}', file=sys.stderr)
        finally:
            with self.lock:
                self.builder = None
                self.checked = time.monotonic()  # a failing build is retried at the check interval, not in a loop

    def ensure_built(self) -> bool:
        """Start a rebuild if the reports changed. True when there are pages to serve; a failed build keeps the old ones."""
        with self.lock:
            now = time.monotonic()
            if self.builder is None and (self.signature is None or now - self.checked >= REBUILD_CHECK_SECONDS):
                self.checked = now
                current = self._current_signature()
                if current != self.signature:
                    self.builder = threading.Thread(target=self._build, args=(current,), name='report-build', daemon=True)
                    self.builder.start()
            return (self.output_dir / 'index.html').is_file()

    def wait(self, timeout=None) -> None:
        """Block until a running build ends. For tests and startup checks; requests never call this."""
        builder = self.builder
        if builder is not None:
            builder.join(timeout)


def wechat_enabled() -> bool:
    import report_site
    group = str(report_site.TARGET_GROUP or '')
    return bool(group) and not group.startswith('REPLACE_')  # the placeholder `init` writes is not a configured group


def _session_key(verifier: str) -> bytes:
    return hashlib.sha256(b'chat-digest-site-session\0' + verifier.encode()).digest()


def make_token(verifier: str, expires: int) -> str:
    return f'{expires}.' + hmac.new(_session_key(verifier), str(expires).encode(), hashlib.sha256).hexdigest()


def token_valid(token: str, verifier: str, now: float | None = None) -> bool:
    match = re.fullmatch(r'([0-9]{1,12})\.[0-9a-f]{64}', token or '')
    if not match or int(match[1]) <= (time.time() if now is None else now):
        return False
    return hmac.compare_digest(token, make_token(verifier, int(match[1])))


def safe_next(target: str | None) -> str:
    """Only same-site absolute paths may be a post-login destination."""
    if not target or not target.startswith('/') or target.startswith('//') or '\\' in target or any(ord(c) < 32 for c in target):
        return '/'
    return target


class LoginGuard:
    """Global cap on failed logins and on concurrent scrypt checks (each uses about 16 MB)."""

    def __init__(self):
        self.failures: deque[float] = deque()
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(2)

    def blocked(self) -> bool:
        with self.lock:
            limit = time.monotonic() - LOGIN_WINDOW_SECONDS
            while self.failures and self.failures[0] < limit:
                self.failures.popleft()
            return len(self.failures) >= LOGIN_MAX_FAILURES

    def record_failure(self) -> None:
        with self.lock:
            self.failures.append(time.monotonic())


def make_handler(config: dict, wechat: WeChatSite | None):
    guard = LoginGuard()
    groups = config['qq_groups']

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'ChatDigestSite'
        sys_version = ''

        def log_message(self, fmt, *args):
            return

        def _headers(self, status, content_type, length, cache=NO_STORE, extra=()):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(length))
            self.send_header('Cache-Control', cache)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            for key, value in extra:
                self.send_header(key, value)
            self.end_headers()

        def _send(self, status, body=b'', content_type=HTML_TYPE, extra=()):
            self._headers(status, content_type, len(body), extra=extra)
            if self.command != 'HEAD':
                self.wfile.write(body)

        def _error(self, status, message=''):
            self._send(status, error_page(status, message))

        def _redirect(self, location, extra=()):
            self._send(303, extra=(('Location', location), *extra))

        def _file(self, directory: Path, name: str, content_type: str, cache: str):
            """Open one regular file by name inside ``directory``, never following symlinks."""
            root_fd = file_fd = None
            started = False
            try:
                root_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
                info = os.fstat(file_fd)
                if not stat.S_ISREG(info.st_mode):
                    return self._error(404)
                self._headers(200, content_type, info.st_size, cache)
                started = True
                if self.command != 'HEAD':
                    while True:
                        data = os.read(file_fd, CHUNK_SIZE)
                        if not data:
                            break
                        self.wfile.write(data)
            except OSError:
                if started:  # the status line is gone; only dropping the connection is honest now
                    self.close_connection = True
                else:
                    self._error(404)
            finally:
                for descriptor in (file_fd, root_fd):
                    if descriptor is not None:
                        os.close(descriptor)

        def _asset(self, directory: Path, name: str):
            match = HASHED_ASSET.fullmatch(name)
            if not match:
                return self._error(404)
            self._file(directory, name, ASSET_TYPES[match[1]], IMMUTABLE)

        def _authenticated(self):
            try:
                cookie = SimpleCookie(self.headers.get('Cookie', ''))
            except CookieError:
                return False
            return COOKIE in cookie and token_valid(cookie[COOKIE].value, config['password_scrypt'])

        def _login_page(self, target='/', error=''):
            message = f'<p class="error">{html.escape(error)}</p>' if error else ''
            body = (f'{message}<form method="post" action="/login"><input type="hidden" name="next" value="{html.escape(safe_next(target), quote=True)}">'
                    '<label>用户名<input name="username" autocomplete="username" required autofocus></label>'
                    '<label>密码<input name="password" type="password" autocomplete="current-password" required></label>'
                    '<button type="submit">登录</button></form>')
            return site_page('登录', body, subtitle='群聊日报')

        def _nav(self):
            return ('<a href="/">全部报告</a> · <a href="/about">站点说明</a> · '
                    '<form class="inline" method="post" action="/logout"><button type="submit">退出</button></form>')

        def _home(self):
            sources = []
            if wechat is not None:
                sources.append(('/wechat/', '微信群日报'))
            sources.extend((f'/qq/{group_id}/', group['name']) for group_id, group in groups.items())
            if len(sources) == 1:
                return self._redirect(sources[0][0])
            items = ''.join(f'<li><a href="{html.escape(href, quote=True)}">{html.escape(name)}</a></li>' for href, name in sources)
            body = f'<ul class="reports">{items}</ul>' if items else '<p class="empty">还没有配置任何报告来源。</p>'
            self._send(200, site_page('群聊日报', body, foot=self._nav()))

        def _wechat(self, tail):
            if wechat is None:
                return self._error(404)
            if not wechat.ensure_built():
                return self._send(503, error_page(503, '报告正在整理，第一次可能要几分钟，页面会自动刷新。'),
                                  extra=(('Retry-After', '15'), ('Refresh', '15')))
            if tail in ('', 'index.html'):
                return self._file(wechat.output_dir, 'index.html', HTML_TYPE, NO_STORE)
            kind, _, name = tail.partition('/')
            if kind == 'assets' and '/' not in name:
                return self._asset(wechat.output_dir / 'assets', name)
            if kind == 'reports' and REPORT_PAGE.fullmatch(name):
                return self._file(wechat.output_dir / 'reports', name, HTML_TYPE, NO_STORE)
            self._error(404)

        def _qq(self, group_id, tail):
            group = groups.get(group_id)
            if group is None:
                return self._error(404)
            if tail in ('', 'index.html'):
                return self._send(200, qq_report_site._index_html(group_id, group))
            root = group['reports_dir']
            kind, separator, name = tail.partition('/')
            if separator and kind == 'assets' and '/' not in name:
                return self._asset(root / 'assets', name)
            if not separator and qq_report_site._SAFE_STEM.fullmatch(tail) and tail.endswith(('.html', '.png')):
                stem, suffix = tail.rsplit('.', 1)
                entry = next((item for item in qq_report_site._report_entries(group_id, group) if item['stem'] == stem), None)
                if entry is not None and suffix == 'png':
                    return self._file(root, tail, 'image/png', NO_STORE)
                if entry is not None:
                    return self._qq_report(entry['html'])
            self._error(404)

        def _qq_report(self, target: Path):
            try:
                page = target.read_bytes()
            except OSError:
                return self._error(404)
            self._send(200, re.sub(rb'(<body(?:\s[^>]*)?>)', lambda match: match[1] + QQ_BACK_LINK, page, count=1, flags=re.I))

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            parts = urlsplit(self.path)
            try:
                path = unquote(parts.path, errors='strict')
            except (UnicodeError, ValueError):
                return self._error(404)
            if path == '/healthz':
                return self._send(200, b'ok\n', 'text/plain; charset=utf-8')
            if path == '/login':
                if self._authenticated():
                    return self._redirect('/')
                return self._send(200, self._login_page(parse_qs(parts.query).get('next', ['/'])[0]))
            if '\x00' in path or '\\' in path or '..' in path.split('/'):
                return self._error(404)
            if not self._authenticated():
                if path == '/' or path.endswith(('/', '.html')) or path == '/about':
                    target = path + (f'?{parts.query}' if parts.query else '')
                    return self._redirect('/login?next=' + quote(target, safe=''))
                return self._error(401, '请先登录。')
            if path == '/':
                return self._home()
            if path == '/about':
                return self._send(200, site_page('站点说明', ABOUT_BODY, foot=self._nav()))
            if path == '/wechat':
                return self._redirect('/wechat/')
            if path.startswith('/wechat/'):
                return self._wechat(path[len('/wechat/'):])
            match = re.fullmatch(r'/qq/([0-9]{1,32})(?:/(.*))?', path, re.S)
            if match:
                if match[2] is None:
                    return self._redirect(f'/qq/{match[1]}/')
                return self._qq(match[1], match[2])
            self._error(404)

        def _form(self):
            length = self.headers.get('Content-Length', '')
            if not length.isdigit() or int(length) > MAX_FORM_BYTES:
                self.close_connection = True
                return None
            try:
                return parse_qs(self.rfile.read(int(length)).decode('utf-8'), max_num_fields=8)
            except (UnicodeError, ValueError):
                return None

        def _set_cookie(self, value, max_age):
            secure = '; Secure' if config['cookie_secure'] else ''
            return ('Set-Cookie', f'{COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure}')

        def do_POST(self):
            path = urlsplit(self.path).path
            form = self._form()
            if form is None:
                return self._error(400, '请求内容无效。')
            if path == '/logout':
                return self._redirect('/login', [self._set_cookie('', 0)])
            if path != '/login':
                return self._error(405)
            target = safe_next(form.get('next', ['/'])[0])
            if guard.blocked():
                return self._send(429, self._login_page(target, '尝试次数太多，请一分钟后再试。'))
            username, password = form.get('username', [''])[0], form.get('password', [''])[0]
            with guard.slots:
                correct = qq_report_site.verify_password(password, config['password_scrypt'])
            if not (correct and hmac.compare_digest(username.encode(), config['username'].encode())):
                guard.record_failure()
                return self._send(401, self._login_page(target, '用户名或密码不对。'))
            max_age = config['session_hours'] * 3600
            token = make_token(config['password_scrypt'], int(time.time()) + max_age)
            self._redirect(target, [self._set_cookie(token, max_age)])

    return Handler


def create_server(config: dict, wechat: WeChatSite | None):
    server = ThreadingHTTPServer((config['bind'], config['port']), make_handler(config, wechat))
    server.daemon_threads = True
    return server


def default_wechat_site() -> WeChatSite | None:
    if not wechat_enabled():
        return None
    return WeChatSite(settings.RUNTIME / 'reports', settings.RUNTIME / 'local-site')


def set_password(path: Path, password: str) -> None:
    config = json.loads(path.read_text(encoding='utf-8'))
    config['password_scrypt'] = qq_report_site.hash_password(password)
    _write_private(path, config)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, help=f'site configuration (default: runtime/{CONFIG_NAME})')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--serve', action='store_true', help='run the website')
    mode.add_argument('--check-config', action='store_true', help='validate the configuration and exit')
    mode.add_argument('--init', action='store_true', help='create the configuration with a generated password')
    mode.add_argument('--set-password', action='store_true', help='read a new password and replace the stored verifier')
    args = parser.parse_args(argv)
    path = args.config or config_path()
    if args.init:
        created = init_config(path)
        print(json.dumps(created or {'status': 'already configured', 'config': str(path)}, ensure_ascii=False))
        return 0
    try:
        if args.set_password:
            if not path.is_file():
                parser.error(f'{path} does not exist; run with --init first')
            password = getpass.getpass('新密码: ') if sys.stdin.isatty() else sys.stdin.readline().rstrip('\r\n')
            if len(password) < 8:
                parser.error('password must have at least 8 characters')
            set_password(path, password)
            print('password updated; existing sessions are signed out')
            return 0
        config = load_config(path)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'local_site: {exc}\n')
    if args.check_config:
        print('configuration OK')
        return 0
    wechat = default_wechat_site()
    server = create_server(config, wechat)
    print(f'report site: http://127.0.0.1:{server.server_address[1]}/', flush=True)
    if wechat is not None:
        wechat.ensure_built()  # the first visitor should not be the one to start it
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
