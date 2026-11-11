"""Shared look of the report websites: list pages, login and error pages, and reader tweaks.

The palette matches agent_console/daily_report.py. Site pages use system fonts so a list page
stays a few kilobytes; only the report pages carry the bundled rounded font.
"""
from __future__ import annotations

import html

INK, BODY, MUTED, LINE, ACCENT, DEEP = '#2B2833', '#55515E', '#6F6A7A', '#E6E3EE', '#B8A9E8', '#5F4DA8'
FONT = 'system-ui,-apple-system,"PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif'

SITE_CSS = f"""*{{box-sizing:border-box}}body{{margin:0;background:#fff;color:{INK};font:16px/1.65 {FONT};-webkit-text-size-adjust:100%}}
main{{width:min(720px,100%);margin:auto;padding:40px 24px 56px}}h1{{font-size:32px;line-height:1.25;margin:0}}.sub{{color:{MUTED};font-size:14px;margin:6px 0 0}}
.rule{{width:56px;height:2px;background:{ACCENT};margin:20px 0 8px}}a{{color:{DEEP};text-decoration-color:{ACCENT};text-underline-offset:3px}}a:focus-visible,button:focus-visible,input:focus-visible{{outline:2px solid {DEEP};outline-offset:2px}}
article{{padding:18px 0;border-bottom:1px solid {LINE};overflow-wrap:anywhere}}article h2{{font-size:19px;margin:0}}article p{{margin:4px 0 0;color:{BODY}}}article .period,article .stats{{color:{MUTED};font-size:14px}}article .editions{{font-size:14px}}
ul.reports{{list-style:none;margin:0;padding:0}}ul.reports li{{padding:14px 0;border-bottom:1px solid {LINE};color:{MUTED};font-size:14px;overflow-wrap:anywhere}}ul.reports a{{font-size:17px;font-weight:700;margin-right:6px}}
.empty,.note{{color:{MUTED}}}.note{{margin-top:12px}}.foot{{margin-top:32px;color:{MUTED};font-size:13px}}
form{{margin-top:24px;display:grid;gap:14px;max-width:340px}}label{{display:grid;gap:4px;font-size:14px;color:{BODY}}}input{{font:inherit;padding:10px 12px;border:1px solid {LINE};border-radius:6px;width:100%;color:{INK}}}input:focus{{border-color:{ACCENT}}}
button{{font:inherit;font-weight:700;padding:10px 12px;border:1px solid {DEEP};border-radius:6px;background:#fff;color:{DEEP};cursor:pointer}}.error{{color:{DEEP};font-size:14px;margin:12px 0 0}}.inline{{display:inline;margin:0}}.inline button{{padding:0;border:0;font-weight:400;text-decoration:underline;text-decoration-color:{ACCENT};text-underline-offset:3px}}
pre{{overflow-x:auto;padding:12px 0;border-top:1px solid {LINE};border-bottom:1px solid {LINE};font-size:13px;color:{BODY}}}
@media(max-width:599px){{main{{padding:28px 16px 44px}}h1{{font-size:26px}}}}"""


def site_page(title: str, body: str, *, subtitle: str = '', foot: str = '') -> bytes:
    """A complete list, login, error or help page. ``body`` and ``foot`` are trusted HTML."""
    sub = f'<p class="sub">{html.escape(subtitle)}</p>' if subtitle else ''
    tail = f'<p class="foot">{foot}</p>' if foot else ''
    page = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">'
            f'<title>{html.escape(title)}</title><style>{SITE_CSS}</style></head><body><main><h1>{html.escape(title)}</h1>{sub}'
            f'<div class="rule"></div>{body}{tail}</main></body></html>\n')
    return page.encode('utf-8')


def error_page(status: int, message: str = '') -> bytes:
    titles = {401: '需要登录', 403: '无权访问', 404: '页面不存在', 405: '不支持这个操作', 429: '尝试太频繁', 500: '服务器出错了', 503: '报告暂时不可用'}
    title = titles.get(status, '出错了')
    text = html.escape(message or {404: '这个地址没有对应的报告，可能已经被清理。'}.get(status, '请稍后再试。'))
    return site_page(title, f'<p class="note">{text}</p>', subtitle=f'错误 {status}')


# Reports written by an earlier renderer keep their own markup; these rules keep them readable on
# the site. Reports from the current renderer mark <body class="digest-v2"> and style themselves.
_OLD = 'body:not(.digest-v2)'
READER_STYLE = f"""<style>
.site-home{{max-width:680px;margin:0 auto;padding:16px 24px 0;font:14px/1.6 {FONT};color:{MUTED}}}.site-home a{{color:{DEEP};text-decoration-color:{ACCENT};text-underline-offset:3px}}
{_OLD}{{font-size:18px;width:100%}}{_OLD} main,{_OLD} main.page{{width:min(1000px,100%);padding:24px;margin:auto}}{_OLD} h1{{font-size:56px}}{_OLD} h2{{font-size:30px}}
{_OLD} .meta,{_OLD} .muted{{font-size:16px}}{_OLD} .stats{{grid-template-columns:repeat(4,minmax(0,1fr))}}{_OLD} .stats small{{font-size:14px}}{_OLD} .stats b{{font-size:30px}}
{_OLD} .lede,{_OLD} .summary,{_OLD} .topic li,{_OLD} blockquote p{{font-size:18px}}{_OLD} nav a{{font-size:18px}}{_OLD} .topic h3{{font-size:24px}}{_OLD} .topic h3 span{{font-size:18px}}
{_OLD} blockquote footer,{_OLD} .sources p{{font-size:16px}}{_OLD} details,{_OLD} .sources b,{_OLD} .sources small{{font-size:14px}}
{_OLD} .topic-time{{font-size:14px;line-height:1.5;color:{MUTED};margin:8px 0 14px}}
.digest-v2 .rank,{_OLD} .rank{{grid-template-rows:repeat(var(--rows,10),auto);grid-auto-flow:column}}
{_OLD} .rank li{{grid-template-columns:40px 48px minmax(0,1fr) 80px;font-size:18px}}{_OLD} .rank li b{{grid-column:auto;font-size:18px}}
{_OLD} nav>ol{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 40px;margin:18px 0 0;padding-inline-start:28px}}
{_OLD} nav>ol>li{{min-width:0;margin:0;padding-left:4px}}{_OLD} nav>ol>li::marker{{color:{MUTED};font-variant-numeric:tabular-nums}}
{_OLD} nav>ol>li>a{{display:block;min-height:44px;padding:6px 10px;line-height:1.6;text-decoration:none;overflow-wrap:anywhere;border-radius:8px}}
{_OLD} nav>ol>li>a:hover{{background:#F4F1FB;color:{DEEP}}}{_OLD} nav>ol>li>a:focus-visible{{outline:2px solid {DEEP};outline-offset:2px}}
@media(max-width:760px){{.site-home{{padding:12px 16px 0}}{_OLD} nav>ol{{grid-template-columns:minmax(0,1fr);gap:10px;margin-top:16px;padding-inline-start:26px}}
{_OLD} main,{_OLD} main.page{{padding:16px}}{_OLD} h1{{font-size:40px}}{_OLD} .stats{{grid-template-columns:repeat(2,minmax(0,1fr))}}.digest-v2 .rank,{_OLD} .rank{{grid-template-columns:minmax(0,1fr);grid-template-rows:none;grid-auto-flow:row}}{_OLD} .rank li{{grid-template-columns:30px 40px minmax(0,1fr) 60px;gap:8px}}{_OLD} .rank .avatar{{width:36px;height:36px}}{_OLD} .topic,{_OLD} blockquote,{_OLD} nav{{padding:16px}}{_OLD} .topic ul{{padding-left:22px}}{_OLD} .leader-grid{{grid-template-columns:1fr}}}}
</style>"""


def reader_styles(renderer=None):
    css = READER_STYLE
    if renderer is not None:
        css += '<style>' + renderer.RHYTHM_CHART_CSS + renderer.SOURCE_MESSAGES_CSS + '</style>'
    return css
