"""Bundled report renderer: one lavender, type-led design for the PNG and the HTML page.

Clean-room implementation: only the look of an earlier renderer (synthetic screenshots and a
written description) was used as a reference, never its code. See THIRD_PARTY_NOTICES.md.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import re
import sys
from datetime import datetime
from functools import lru_cache
from html import escape
from pathlib import Path

try:
    from .report_metrics import body_chinese_count, body_token_count, message_body_token_count, peak_time_text
except ImportError:  # direct script execution
    from report_metrics import body_chinese_count, body_token_count, message_body_token_count, peak_time_text

try:
    from timezones import project_zone
except ImportError:  # the deployment directory is not on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deployment'))
    from timezones import project_zone

DISPLAY_TZ = project_zone('Asia/Shanghai')
DEFAULT_TITLE = '群聊日报'
TOPIC_AVATAR_LIMIT = 8

# One palette for both outputs. DEEP and MUTED pass WCAG AA for body text on white;
# ACCENT_TEXT passes AA for large text; ACCENT is for lines and decoration only.
INK = '#2B2833'
BODY = '#55515E'
MUTED = '#6F6A7A'
LINE = '#E6E3EE'
ACCENT = '#B8A9E8'
ACCENT_TEXT = '#8672D1'
DEEP = '#5F4DA8'
SOFT = '#EFEBFA'
PAPER = '#FFFFFF'
FONT_STACK = "DigestCJK,'PingFang SC','Noto Sans CJK SC','Microsoft YaHei',sans-serif"

RHYTHM_CHART_CSS = f"""
.rhythm-chart{{width:100%;max-width:600px;height:auto;display:block}}.rhythm-chart .grid{{stroke:{LINE};stroke-width:1}}
.rhythm-chart .axis{{stroke:{LINE};stroke-width:1}}.rhythm-chart .line{{fill:none;stroke:{ACCENT};stroke-width:2;stroke-linejoin:round;stroke-linecap:round}}
.rhythm-chart .rhythm-point{{fill:{ACCENT}}}.rhythm-chart .peak{{fill:{DEEP}}}.rhythm-chart text{{font:15px {FONT_STACK};fill:{MUTED}}}.rhythm-chart .axis-hour{{fill:{ACCENT}}}
"""
SOURCE_MESSAGES_CSS = f"""
 .avatar{{width:32px;height:32px;border-radius:50%;object-fit:cover;background:{SOFT};flex:none}}.avatar-fallback{{display:inline-grid;place-items:center;color:{DEEP};font-size:13px;font-weight:700}}
 .sources{{list-style:none;padding:0;margin:10px 0 0}}.source{{display:flex;gap:10px;align-items:flex-start;padding:12px 0;border-top:1px solid {LINE}}}.source-body{{min-width:0;flex:1}}
 .source-body>b{{display:block;font-weight:700;font-size:14px;color:{INK}}}.source-meta{{margin:1px 0 4px;color:{MUTED};font-size:13px}}.source-text{{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65;color:{BODY};margin:0;font-size:15px}}
 .source-quote{{margin:8px 0 0;padding:0 0 0 12px;border-left:2px solid {ACCENT};color:{MUTED}}}.source-quote p{{margin:0;font-size:14px;overflow-wrap:anywhere}}
 .source-gallery{{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}}.source-image-link{{display:block}}.source-image{{display:block;max-width:min(100%,420px);max-height:360px;object-fit:contain;border-radius:6px}}.source-unavailable{{color:{MUTED};font-size:13px}}
 details>summary{{cursor:pointer;color:{DEEP};font-size:14px}}
"""
PAGE_CSS = f"""
*{{box-sizing:border-box}}body.digest-v2{{margin:0;background:{PAPER};color:{INK};font:17px/1.7 {FONT_STACK};-webkit-text-size-adjust:100%}}
.digest-v2 .site-home{{max-width:680px;margin:0 auto;padding:16px 24px 0;font-size:14px}}.digest-v2 .page{{max-width:680px;margin:0 auto;padding:44px 24px 64px}}.digest-v2 a{{color:{DEEP};text-decoration-color:{ACCENT};text-underline-offset:3px}}
.digest-v2 .chat{{display:flex;align-items:center;gap:10px;font-size:15px;font-weight:700;color:{DEEP};overflow-wrap:anywhere}}
.digest-v2 h1{{font-size:40px;line-height:1.2;margin:8px 0 6px;overflow-wrap:anywhere}}.digest-v2 h1 .edition{{white-space:nowrap}}
.digest-v2 .meta,.digest-v2 .body-chinese-count{{margin:0;color:{MUTED};font-size:14px}}.digest-v2 .rule{{width:56px;height:2px;background:{ACCENT};margin:24px 0}}
.digest-v2 .stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}}.digest-v2 .stat{{display:flex;flex-direction:column-reverse;justify-content:flex-end;min-width:0}}
.digest-v2 .stat small{{color:{MUTED};font-size:13px}}.digest-v2 .stat b{{color:{DEEP};font-size:32px;line-height:1.15;overflow-wrap:anywhere}}.digest-v2 .stat b.peak{{font-size:22px;line-height:1.7}}.digest-v2 .stat .unit{{font-size:13px;font-weight:400;margin-left:3px;color:{MUTED}}}
.digest-v2 .lede{{font-size:21px;line-height:1.65;margin:32px 0 0;overflow-wrap:anywhere}}.digest-v2 h2{{font-size:17px;color:{DEEP};margin:40px 0 12px}}
.digest-v2 .toc ol{{margin:0;padding-left:26px;font-size:15px}}.digest-v2 .toc li{{padding:2px 0}}.digest-v2 .toc li::marker{{color:{MUTED}}}
.digest-v2 .topic{{position:relative;border-top:1px solid {LINE};padding:20px 0}}.digest-v2 .topic h3{{font-size:21px;line-height:1.35;margin:0;overflow-wrap:anywhere}}.digest-v2 .topic h3 span{{color:{ACCENT_TEXT};margin-right:10px}}
.digest-v2 .topic-time{{margin:2px 0 6px;color:{MUTED};font-size:14px}}.digest-v2 .topic-summary,.digest-v2 .summary{{margin:6px 0 0;color:{BODY};white-space:pre-wrap;overflow-wrap:anywhere}}
.digest-v2 .topic-details{{margin:8px 0 0;padding-left:20px;color:{BODY};font-size:16px}}.digest-v2 .topic-details li{{padding:2px 0;overflow-wrap:anywhere}}.digest-v2 .topic-details li::marker{{color:{ACCENT}}}
.digest-v2 .people{{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:10px;color:{MUTED};font-size:13px}}.digest-v2 .people .avatar{{width:26px;height:26px;font-size:11px}}.digest-v2 .topic details{{margin-top:10px}}
.digest-v2 blockquote{{position:relative;margin:18px 0 0;padding-left:34px}}.digest-v2 blockquote::before{{content:'“';position:absolute;right:calc(100% - 28px);top:-6px;font-size:52px;line-height:1;font-weight:700;color:{ACCENT}}}
.digest-v2 blockquote p{{margin:0;font-size:20px;line-height:1.55;overflow-wrap:anywhere}}.digest-v2 blockquote footer{{margin-top:4px;color:{MUTED};font-size:14px}}.digest-v2 blockquote details{{margin-top:2px}}
.digest-v2 .rank{{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2px 28px;counter-reset:rank}}.digest-v2 .rank li{{display:flex;align-items:center;gap:10px;min-width:0;padding:5px 0;counter-increment:rank}}
.digest-v2 .rank li::before{{content:counter(rank,decimal-leading-zero);width:20px;flex:none;color:{MUTED};font-size:13px}}.digest-v2 .rank-name{{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.digest-v2 .rank strong{{flex:none;font-weight:400;font-size:14px;color:{DEEP}}}
.digest-v2 .generated{{margin-top:44px;padding-top:14px;border-top:1px solid {LINE};color:{MUTED};font-size:13px}}
@media(min-width:600px){{.digest-v2 .topic h3{{padding-right:128px}}.digest-v2 .topic-time{{position:absolute;right:0;top:22px;margin:0}}}}
@media(max-width:599px){{body.digest-v2{{font-size:16px}}.digest-v2 .page{{padding:28px 16px 48px}}.digest-v2 h1{{font-size:32px}}.digest-v2 .stats{{grid-template-columns:repeat(2,minmax(0,1fr))}}.digest-v2 .lede{{font-size:19px}}.digest-v2 .rank{{grid-template-columns:minmax(0,1fr)}}}}
"""
CONTENT_SECURITY_POLICY = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:"


def _report_timezone(report=None):
    """Use report metadata timezone, then CHAT_DIGEST_TIMEZONE, then Asia/Shanghai."""
    name = (report or {}).get('timezone') or os.environ.get('CHAT_DIGEST_TIMEZONE')
    if not name:
        return DISPLAY_TZ
    try:
        return project_zone(str(name))
    except (KeyError, ValueError) as exc:
        raise ValueError(f'Unsupported report timezone: {name}') from exc


def time_text(value, fmt='%m-%d %H:%M', timezone=None):
    try:
        return datetime.fromtimestamp(float(value), timezone or DISPLAY_TZ).strftime(fmt)
    except (TypeError, ValueError, OSError, OverflowError):
        return ''


def topic_time_range(topic, timezone=None):
    start, end = topic.get('start_unix'), topic.get('end_unix')
    if start is None or end is None:
        return ''
    first, last = time_text(start, '%H:%M', timezone), time_text(end, '%H:%M', timezone)
    return f'{first}–{last}' if first and last else ''


def _font_candidates():
    configured = os.environ.get('CHAT_DIGEST_FONT')
    bundled = Path(__file__).resolve().parent.parent / 'assets' / 'fonts'
    system = [
        Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
        Path('/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc'),
        Path('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'),
        Path('/System/Library/Fonts/PingFang.ttc'),
        Path('/System/Library/Fonts/STHeiti Light.ttc'),
        Path('/Library/Fonts/Arial Unicode.ttf'),
        Path('/usr/share/fonts/truetype/arphic/uming.ttc'),
    ]
    return ([Path(configured)] if configured else []) + [
        bundled / 'ResourceHanRoundedCN-Regular.woff2',
        bundled / 'ResourceHanRoundedCN-Regular.ttf',
    ] + system


def rounded_webfont_css():
    """Inline the configured or bundled full CJK webfont for portable HTML."""
    regular = _font_candidates()
    bundled = Path(__file__).resolve().parent.parent / 'assets' / 'fonts'
    bold = [bundled / 'ResourceHanRoundedCN-Bold.woff2', bundled / 'ResourceHanRoundedCN-Bold.ttf']
    declarations = []
    for weight, candidates in ((400, regular), (700, bold)):
        for candidate in candidates:
            try:
                if candidate.is_file():
                    data = base64.b64encode(candidate.read_bytes()).decode('ascii')
                    suffix = candidate.suffix.lower()
                    fmt = 'woff2' if suffix == '.woff2' else 'truetype' if suffix in ('.ttf', '.ttc') else 'opentype'
                    mime = 'font/woff2' if fmt == 'woff2' else 'font/ttf' if fmt == 'truetype' else 'font/otf'
                    declarations.append(f"@font-face{{font-family:DigestCJK;src:url(data:{mime};base64,{data}) format('{fmt}');font-weight:{weight}}}")
                    break
            except OSError:
                continue
    return ''.join(declarations)


def font_path():
    for candidate in _font_candidates():
        if candidate.suffix.lower() == '.woff2':
            continue
        if candidate.is_file():
            return candidate
    raise RuntimeError('No CJK font found. Set CHAT_DIGEST_FONT to an installed Pillow-compatible .ttf/.ttc/.otf font.')


def bold_font_path():
    """The bundled bold face, unless the deployment chose its own font."""
    bundled = Path(__file__).resolve().parent.parent / 'assets' / 'fonts' / 'ResourceHanRoundedCN-Bold.ttf'
    return bundled if not os.environ.get('CHAT_DIGEST_FONT') and bundled.is_file() else font_path()


def build_rhythm_buckets(timestamps, start, end):
    start, end = int(start), int(end)
    if end <= start:
        return []
    first = (start // 600) * 600
    buckets = []
    cursor = first
    while cursor < end:
        left, right = max(cursor, start), min(cursor + 600, end)
        buckets.append({'start_unix': left, 'end_unix_exclusive': right, 'count': 0,
                        'label': time_text(left, '%Y-%m-%d %H:%M')})
        cursor += 600
    for stamp in timestamps:
        try:
            stamp = float(stamp)
        except (TypeError, ValueError):
            continue
        if start <= stamp < end:
            idx = min(int((stamp - first) // 600), len(buckets) - 1)
            buckets[idx]['count'] += 1
    return buckets


def _bucket_count(bucket):
    try:
        return max(0, int(bucket.get('count', 0) or 0))
    except (TypeError, ValueError):
        return 0


def _axis_bounds(values):
    start = int(values[0].get('start_unix', 0))
    end = int(values[-1].get('end_unix_exclusive', start + len(values) * 600))
    return start, max(end, start + 1)


def _rhythm_svg(report):
    timezone = _report_timezone(report)
    values = report.get('rhythm_buckets') or []
    width, height, left, right, top, bottom = 520, 170, 30, 8, 12, 48
    plot_w, plot_h = width - left - right, height - top - bottom
    counts = [_bucket_count(item) for item in values]
    y_max = max(1, max(counts or [0]))
    y = lambda v: top + plot_h * (1 - v / y_max)
    parts = [f'<svg class="rhythm-chart" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="消息节奏">']
    for v in sorted({0, y_max // 2, y_max}):
        yy = y(v)
        parts.append(f'<line class="grid" x1="{left}" y1="{yy:.2f}" x2="{width-right}" y2="{yy:.2f}"/><text x="{left-8}" y="{yy+5:.2f}" text-anchor="end">{v}</text>')
    if not values:
        parts.append('</svg>')
        return ''.join(parts)
    axis_start, axis_end = _axis_bounds(values)
    duration = axis_end - axis_start
    x_at = lambda stamp: left + plot_w * (float(stamp) - axis_start) / duration
    points = [(x_at(item.get('start_unix', axis_start)), y(count)) for item, count in zip(values, counts)]
    parts.append('<polyline class="line" points="' + ' '.join(f'{px:.2f},{py:.2f}' for px, py in points) + '"/>')
    peak = max(counts)
    for (px, py), count in zip(points, counts):
        parts.append(f'<circle class="rhythm-point" cx="{px:.2f}" cy="{py:.2f}" r="{1.8 if count else 0}"/>')
    if peak:
        px, py = points[counts.index(peak)]
        parts.append(f'<circle class="peak" cx="{px:.2f}" cy="{py:.2f}" r="3.5"/>')
    hour = ((axis_start + 3599) // 3600) * 3600
    while hour <= axis_end:
        px = x_at(hour)
        parts.append(f'<circle class="axis-hour" cx="{px:.2f}" cy="{height-bottom}" r="1.2"/>')
        local = datetime.fromtimestamp(hour, timezone)
        if local.minute == 0 and local.hour % 2 == 0:
            parts.append(f'<text class="chart-hour" x="{px:.2f}" y="{height-bottom+18}" text-anchor="middle">{local:%H}</text>')
        hour += 3600
    parts.append(f'<text x="{left}" y="{height-6}" text-anchor="start">{escape(time_text(axis_start, "%Y-%m-%d", timezone))}</text>')
    parts.append(f'<text x="{width-right}" y="{height-6}" text-anchor="end">{escape(time_text(axis_end, "%Y-%m-%d", timezone))}</text>')
    parts.append('</svg>')
    return ''.join(parts)


def rhythm_chart_contents(report):
    return _rhythm_svg(report)


def _safe_asset(value):
    if not isinstance(value, str):
        return ''
    if re.fullmatch(r'(?:\.\./assets/|\./assets/|assets/)?[A-Za-z0-9_-]{8,128}\.(?:png|jpe?g|webp|gif)', value, re.I):
        return value
    if value.startswith('data:image/') and len(value) <= 2_000_000 and re.fullmatch(r'data:image/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+', value):
        return value
    return ''


def _initial(name):
    return next((char for char in str(name or '') if not char.isspace()), '？')


def _avatar_html(author_id, author, avatars):
    avatar = (avatars or {}).get(str(author_id or ''), {})
    src = ''
    if isinstance(avatar, dict) and avatar.get('available'):
        src = _safe_asset(avatar.get('asset_src')) or _safe_asset(avatar.get('data_uri'))
    if src:
        return f"<img class='avatar source-avatar' src='{escape(src, quote=True)}' alt='' loading='lazy'>"
    return f"<span class='avatar avatar-fallback' aria-label='{escape(str(author), quote=True)}'>{escape(_initial(author))}</span>"


def _quote_source(quote):
    """Resolve quote provenance without exposing internal citation identifiers."""
    source = quote.get('source') if isinstance(quote, dict) else None
    if isinstance(source, dict):
        return source
    quote = quote if isinstance(quote, dict) else {}
    identity = quote.get('speaker_id') or quote.get('author_id')
    return {
        'author': quote.get('speaker') or quote.get('author') or '未知成员',
        'author_id': identity,
        'speaker_id': identity,
        'time': quote.get('time') or '',
        'timestamp': quote.get('timestamp'),
        'type': 'text',
        'text': quote.get('text') or '',
    }


def _format_inline_quote(text):
    text = str(text or '')
    match = re.search(r'(?:收到\s*[&＆]?\s*)?引用\s*[^:：\n]{0,80}[:：]\s*(.+)$', text, re.S)
    if not match:
        return escape(text), '', False
    main = text[:match.start()].strip().rstrip('；;:：').strip()
    return escape(main), escape(text[match.start():].strip()), True


def _source_images_html(images):
    entries = []
    for raw in images or []:
        src = _safe_asset(raw)
        if src:
            safe_src = escape(src, quote=True)
            entries.append(f"<a class='source-image-link' href='{safe_src}' target='_blank' rel='noopener'><img class='source-image' src='{safe_src}' alt='聊天图片' loading='lazy'></a>")
    return "<div class='source-gallery'>" + ''.join(entries) + '</div>' if entries else ''


def source_messages_html(messages, avatars=None, timezone=None):
    rendered = []
    for message in messages or []:
        author = str(message.get('author') or message.get('speaker') or '未知成员')
        author_id = message.get('author_id') or message.get('speaker_id')
        timestamp = message.get('timestamp', message.get('time_unix'))
        time = time_text(timestamp, '%m.%d %H:%M', timezone) if timestamp is not None else str(message.get('time') or '')
        text = str(message.get('text') or '')
        images = message.get('image_sources') or ([message.get('image_src')] if message.get('image_src') else [])
        image_html = _source_images_html(images)
        is_image_placeholder = bool(re.fullmatch(r'[【\[]图片[】\]]', text.strip()))
        main_html, quote_text, quoted = _format_inline_quote(text) if text and not is_image_placeholder else ('', '', False)
        quote_html = ("<div class='source-quote' role='blockquote'><p>" + quote_text + '</p>' + image_html + '</div>') if quoted else ''
        unavailable = message.get('image_unavailable_reason')
        unavailable_html = f"<div class='source-unavailable'>{escape(str(unavailable))}</div>" if unavailable and not image_html else ''
        if not (main_html or quote_html or image_html or unavailable_html):
            continue
        time_html = f"<div class='source-meta'>{escape(time)}</div>" if time else ''
        main = f"<p class='source-text'>{main_html}</p>" if main_html else ''
        body = main + quote_html + ('' if quoted else image_html) + unavailable_html
        rendered.append("<li class='source'>" + _avatar_html(author_id, author, avatars) + "<div class='source-body'><b>" + escape(author) + '</b>' + time_html + body + '</div></li>')
    return ''.join(rendered)


def _topic_refs(topic):
    return topic.get('references') if isinstance(topic.get('references'), list) else []


def _row_identity(row):
    return (row.get('username') or row.get('id') or row.get('participant_id') or row.get('account_id')
            or row.get('user_id') or row.get('author_id') or row.get('speaker_id') or '')


def _row_name(row):
    return str(row.get('name') or row.get('display_name') or row.get('author') or '未知成员')


def ranked_participants(report):
    rows = report.get('participants') or report.get('active_rank') or []
    def order(row):
        value = row.get('tokens', row.get('count', row.get('messages', 0))) or 0
        return -int(value), str(_row_identity(row))
    return sorted(rows, key=order)[:20]


def rank_value(row):
    return int(row.get('tokens', row.get('count', row.get('messages', 0))) or 0), ('token' if 'tokens' in row else '条')


def rank_rows_html(report):
    avatars = report.get('avatars') or {}
    result = []
    for row in ranked_participants(report):
        name = _row_name(row)
        value, unit = rank_value(row)
        result.append(f"<li>{_avatar_html(_row_identity(row), name, avatars)}<span class='rank-name'>{escape(name)}</span><strong>{escape(str(value))} {escape(unit)}</strong></li>")
    return ''.join(result)


def report_title(report):
    """The title part before the edition: report data first, then a neutral configurable default."""
    return str(report.get('title_prefix') or report.get('report_kind')
               or os.environ.get('CHAT_DIGEST_REPORT_TITLE') or DEFAULT_TITLE)


def _edition_label(report):
    return '简报' if report.get('edition') == 'brief' else '回顾'


def _chat_name(report):
    return str(report.get('chat_display') or report.get('chat_username') or '聊天日报')


def _generated_text(report, timezone):
    raw = str(report.get('generated_at') or '')
    try:
        moment = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return raw
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone)
    return moment.strftime('%Y.%m.%d %H:%M')


def _stat_cells(report):
    """(label, value, unit) in display order; a missing number stays visibly missing."""
    stats = report.get('stats') if isinstance(report.get('stats'), dict) else {}
    def number(key):
        return str(stats[key]) if stats.get(key) is not None else '—'
    return [('消息总量', number('messages'), '条'), ('参与成员', number('members'), '人'),
            ('高峰时段', peak_time_text(report), ''), ('讨论话题', str(len(report.get('topics') or [])), '个')]


def _quotes(report):
    return [quote for quote in report.get('quotes') or [] if isinstance(quote, dict) and quote.get('text')]


def _people_names(report):
    names = {}
    for row in [*(report.get('active_rank') or []), *(report.get('participants') or [])]:
        names.setdefault(str(_row_identity(row)), _row_name(row))
    return names


def _topic_people(topic, names):
    """Unique (id, name) pairs for a topic, falling back to the authors it cites."""
    people = {}
    for identity in topic.get('participant_ids') or []:
        people.setdefault(str(identity), names.get(str(identity), '群友'))
    if not people:
        for ref in _topic_refs(topic):
            if isinstance(ref, dict) and ref.get('author_id'):
                people.setdefault(str(ref['author_id']), str(ref.get('author') or '群友'))
    return list(people.items())


def _topic_html(index, topic, report, names, timezone):
    avatars = report.get('avatars') or {}
    parts = [f'<article class="topic" id="topic-{index}"><h3><span>{index:02}</span>'
             + escape(str(topic.get('topic') or '未命名话题')) + '</h3>']
    time_range = topic_time_range(topic, timezone)
    if time_range:
        parts.append(f'<p class="topic-time">{escape(time_range)}</p>')
    if topic.get('summary'):
        parts.append(f'<p class="topic-summary summary">{escape(str(topic["summary"]))}</p>')
    details = topic.get('details')
    details = details if isinstance(details, list) else [details] if details else []
    if details:
        parts.append('<ul class="topic-details details">' + ''.join(f'<li>{escape(str(item))}</li>' for item in details) + '</ul>')
    people = _topic_people(topic, names) if report.get('edition') != 'brief' else []
    if people:
        shown = ''.join(_avatar_html(identity, name, avatars) for identity, name in people[:TOPIC_AVATAR_LIMIT])
        extra = f'<span>+{len(people) - TOPIC_AVATAR_LIMIT}</span>' if len(people) > TOPIC_AVATAR_LIMIT else ''
        parts.append(f'<div class="people">{shown}{extra}</div>')
    refs = _topic_refs(topic)
    if refs:
        label = f'来源 · {len(refs)} 条消息' + (f' · {time_range}' if time_range else '')
        parts.append(f'<details><summary>{escape(label)}</summary><ul class="sources">'
                     + source_messages_html(refs, avatars, timezone) + '</ul></details>')
    parts.append('</article>')
    return ''.join(parts)


def _quote_html(quote, report, timezone):
    avatars = report.get('avatars') or {}
    source = _quote_source(quote)
    speaker = str(quote.get('speaker') or source.get('author') or '群友')
    when = quote.get('time') or source.get('time')
    if not when and source.get('timestamp') is not None:
        when = time_text(source.get('timestamp'), '%m.%d %H:%M', timezone)
    footer = escape(speaker) + (f' · {escape(str(when))}' if when else '')
    if source.get('text') or source.get('image_src') or source.get('image_sources'):
        footer += '<details><summary>查看来源</summary><ul class="sources">' + source_messages_html([source], avatars, timezone) + '</ul></details>'
    return '<blockquote><p>' + escape(str(quote['text'])) + '</p><footer>' + footer + '</footer></blockquote>'


def render_html(report):
    timezone = _report_timezone(report)
    prefix, edition, chat = report_title(report), _edition_label(report), _chat_name(report)
    topics = [topic for topic in report.get('topics') or [] if isinstance(topic, dict)]
    names = _people_names(report)
    parts = ["<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
             f'<meta http-equiv="Content-Security-Policy" content="{CONTENT_SECURITY_POLICY}"><title>',
             escape(f'{prefix} · {edition} · {chat}'), '</title><style>', rounded_webfont_css(),
             PAGE_CSS, RHYTHM_CHART_CSS, SOURCE_MESSAGES_CSS,
             '</style></head><body class="digest-v2"><main class="page"><header class="hero"><div class="chat">',
             f"<span class='avatar avatar-fallback' aria-hidden='true'>{escape(_initial(chat))}</span><span>{escape(chat)}</span></div>",
             f'<h1>{escape(prefix)} <span class="edition">· {edition}</span></h1>',
             '<p class="meta">', escape(str(report.get('range_text') or report.get('day') or '')), '</p>',
             f'<p class="body-chinese-count muted">正文共 {body_chinese_count(report)} 字（仅计中文）</p></header><div class="rule"></div><section class="stats" aria-label="统计">']
    for label, value, unit in _stat_cells(report):
        peak = ' class="peak"' if label == '高峰时段' else ''
        unit_html = f'<span class="unit">{unit}</span>' if unit else ''
        parts.append(f'<div class="stat"><small>{label}</small><b{peak}>{escape(value)}{unit_html}</b></div>')
    parts.append('</section>')
    if report.get('one_line'):
        parts.append(f'<p class="lede">{escape(str(report["one_line"]))}</p>')
    if topics:
        parts.append('<nav class="toc"><h2>话题目录</h2><ol>' + ''.join(
            f'<li><a href="#topic-{index}">{escape(str(topic.get("topic") or "未命名话题"))}</a></li>'
            for index, topic in enumerate(topics, 1)) + '</ol></nav>')
    parts.append(f'<section><h2>消息节奏</h2><div class="chart">{rhythm_chart_contents(report)}</div></section>')
    if topics:
        parts.append('<section><h2>话题讨论</h2>' + ''.join(
            _topic_html(index, topic, report, names, timezone) for index, topic in enumerate(topics, 1)) + '</section>')
    quotes = _quotes(report)
    if quotes:
        parts.append('<section><h2>群友金句</h2>' + ''.join(_quote_html(quote, report, timezone) for quote in quotes) + '</section>')
    if ranked_participants(report):
        parts.append('<section><h2>活跃前20名</h2><ol class="rank">' + rank_rows_html(report) + '</ol></section>')
    generated = _generated_text(report, timezone)
    if generated:
        parts.append(f'<footer class="generated">生成于 {escape(generated)}</footer>')
    parts.append('</main></body></html>')
    return ''.join(parts)


# ---- PNG ---------------------------------------------------------------------------------------
PNG_WIDTH = 1080
PNG_MARGIN = 72
_NO_LINE_START = set('，。！？、；：）》」』】〉”’…—～·.,!?;:)]}%')
_NO_LINE_END = set('（《「『【〈“‘([{')
_TOKEN = re.compile(r"[A-Za-z0-9_./:%+#@&'-]+|\s+|.", re.S)


@lru_cache(maxsize=None)
def _font(path, size):
    from PIL import ImageFont
    return ImageFont.truetype(str(path), size)


@lru_cache(maxsize=None)
def _glyph_available(path, char):
    """Pillow cannot fall back between fonts, so glyphs the font lacks would print as empty boxes."""
    font = _font(path, 32)
    return bytes(font.getmask(char)) != bytes(font.getmask('\U0010FFFF'))


def _printable(text, path):
    return ''.join(char for char in str(text) if char.isspace() or (char.isprintable() and _glyph_available(path, char)))


def _wrap_text(text, font, max_width):
    """Greedy wrap: Latin words stay whole, CJK breaks anywhere, punctuation obeys line-start rules."""
    lines = []
    for paragraph in str(text).splitlines() or ['']:
        line = ''
        for token in _TOKEN.findall(paragraph):
            if not line and token.isspace():
                continue
            width = font.getlength(line + token)
            # Closing punctuation may hang past the edge by about one character, not more: a run such as
            # "——————" or "？？？？？？" would otherwise leave the canvas.
            if width <= max_width or (token in _NO_LINE_START and line and width <= max_width + font.size):
                line += token
                continue
            if font.getlength(token) > max_width:  # a single word wider than the line
                for char in token:
                    if line and font.getlength(line + char) > max_width:
                        lines.append(line)
                        line = ''
                    line += char
                continue
            carry = ''
            while line and line[-1] in _NO_LINE_END:
                carry, line = line[-1] + carry, line[:-1]
            if line:
                lines.append(line.rstrip())
            line = carry + ('' if token.isspace() else token)
        lines.append(line.rstrip())
    return lines


def _ellipsize(text, font, max_width):
    text = str(text)
    if font.getlength(text) <= max_width:
        return text
    while text and font.getlength(text + '…') > max_width:
        text = text[:-1]
    return text + '…'


def _circle_mask(size):
    from PIL import Image, ImageDraw
    mask = Image.new('L', (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def _decode_avatar(entry, size):
    from PIL import Image, ImageOps
    if not isinstance(entry, dict) or not entry.get('available'):
        return None
    uri = _safe_asset(entry.get('data_uri'))
    if not uri.startswith('data:image/'):
        return None
    try:
        with Image.open(io.BytesIO(base64.b64decode(uri.split(',', 1)[1], validate=True))) as source:
            source.load()
            return ImageOps.fit(ImageOps.exif_transpose(source).convert('RGB'), (size, size), Image.Resampling.LANCZOS)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


class _PngPage:
    """Lays the report out top to bottom, then draws once the exact height is known."""

    def __init__(self, report):
        self.report = report
        self.timezone = _report_timezone(report)
        self.regular, self.bold = font_path(), bold_font_path()
        self.width = PNG_WIDTH
        self.left, self.right = PNG_MARGIN, PNG_WIDTH - PNG_MARGIN
        self.content = self.right - self.left
        self.y = 64
        self.ops = []

    def face(self, size, bold=False):
        return _font(self.bold if bold else self.regular, size)

    def clean(self, text, bold=False):
        return _printable(text, self.bold if bold else self.regular)

    def visible_name(self, name):
        """A nickname made only of characters the font lacks (often emoji) must not leave a blank."""
        return self.clean(name).strip() or '群友'

    def write(self, x, baseline, text, font, fill, anchor='ls'):
        self.ops.append(lambda canvas, draw: draw.text((x, baseline), text, font=font, fill=fill, anchor=anchor))

    def paragraph(self, text, size, fill, line_height, *, bold=False, x=None, width=None):
        x = self.left if x is None else x
        font = self.face(size, bold)
        lines = _wrap_text(self.clean(text, bold), font, self.content if width is None else width)
        for line in lines:
            self.write(x, self.y + round(line_height * 0.76), line, font, fill)
            self.y += line_height

    def rule(self, x0, x1, y, fill=LINE, thickness=1):
        self.ops.append(lambda canvas, draw: draw.rectangle((x0, y, x1, y + thickness - 1), fill=fill))

    def avatar(self, x, y, size, identity, name):
        entry = (self.report.get('avatars') or {}).get(str(identity or ''))
        picture = _decode_avatar(entry, size)
        if picture is not None:
            self.ops.append(lambda canvas, draw: canvas.paste(picture, (x, y), _circle_mask(size)))
            return
        self.initial(x, y, size, name)

    def initial(self, x, y, size, name):
        from PIL import Image
        font = self.face(round(size * 0.46), True)
        letter = _initial(self.clean(name, True))
        disc = Image.new('RGB', (size, size), SOFT)
        def op(canvas, draw):
            canvas.paste(disc, (x, y), _circle_mask(size))
            draw.text((x + size / 2, y + size / 2), letter, font=font, fill=DEEP, anchor='mm')
        self.ops.append(op)

    def section(self, title, note=''):
        self.y += 52
        font = self.face(36, True)
        self.write(self.left, self.y + 36, title, font, DEEP)
        if note:
            self.write(self.right, self.y + 36, note, self.face(27), MUTED, 'rs')
        self.y += 66

    def header(self):
        chat = _chat_name(self.report)
        self.initial(self.left, self.y, 52, chat)
        name_font = self.face(31, True)
        self.write(self.left + 68, self.y + 38, _ellipsize(self.clean(chat, True).strip() or '聊天日报', name_font, self.content - 68), name_font, DEEP)
        self.y += 68
        title = f'{report_title(self.report)} · {_edition_label(self.report)}'
        size = next((candidate for candidate in (76, 68, 60) if self.face(candidate, True).getlength(self.clean(title, True)) <= self.content), 54)
        self.paragraph(title, size, INK, round(size * 1.24), bold=True)
        self.y += 8
        meta = '　'.join(item for item in (str(self.report.get('range_text') or self.report.get('day') or ''),
                                          f'正文共 {body_chinese_count(self.report)} 字') if item)
        self.paragraph(meta, 29, MUTED, 42)
        self.y += 34
        self.rule(self.left, self.left + 95, self.y, ACCENT, 4)
        self.y += 42

    def stats(self):
        cell = self.content / 4
        for column, (label, value, _unit) in enumerate(_stat_cells(self.report)):
            match = re.fullmatch(r'(\d{1,2}-\d{1,2})\s+(\d{1,2}:\d{2})', value) if label == '高峰时段' else None
            if match:
                label, value = f'{label} {match[1]}', match[2]
            x = round(self.left + column * cell)
            room = cell - 18
            size = next((candidate for candidate in (64, 54, 44) if self.face(candidate, True).getlength(value) <= room), 34)
            value_font = self.face(size, True)
            self.write(x, self.y + 62, _ellipsize(self.clean(value, True), value_font, room), value_font, DEEP)
            label_font = self.face(27)
            self.write(x, self.y + 106, _ellipsize(label, label_font, room), label_font, MUTED)
        self.y += 118

    def one_line(self):
        if self.report.get('one_line'):
            self.y += 40
            self.paragraph(self.report['one_line'], 42, INK, 66)

    def rhythm(self):
        values = self.report.get('rhythm_buckets') or []
        if not values:
            return
        from PIL import Image, ImageDraw
        counts = [_bucket_count(item) for item in values]
        peak = max(counts)
        seconds = self.report.get('rhythm_bucket_seconds') or 600
        self.section('消息节奏', f'每 {max(1, int(seconds) // 60)} 分钟 · 最高 {peak} 条')
        top, plot, scale = self.y, 150, 3
        axis_start, axis_end = _axis_bounds(values)
        duration = axis_end - axis_start
        x_at = lambda stamp: self.content * (float(stamp) - axis_start) / duration
        points = [(x_at(item.get('start_unix', axis_start)), plot - plot * count / max(peak, 1) + 6)
                  for item, count in zip(values, counts)]
        left, right = self.left, self.right
        def op(canvas, draw):
            layer = Image.new('RGBA', ((right - left + 8) * scale, (plot + 12) * scale), (255, 255, 255, 0))
            pen = ImageDraw.Draw(layer)
            scaled = [((px + 4) * scale, py * scale) for px, py in points]
            if len(scaled) > 1:
                pen.line(scaled, fill=ACCENT, width=3 * scale, joint='curve')
            if peak:
                px, py = scaled[counts.index(peak)]
                pen.ellipse((px - 7 * scale, py - 7 * scale, px + 7 * scale, py + 7 * scale), fill=DEEP)
            layer = layer.resize((right - left + 8, plot + 12), Image.Resampling.LANCZOS)
            canvas.paste(layer, (left - 4, top), layer)
        self.rule(self.left, self.right, top + plot + 6)
        self.ops.append(op)
        hour_font = self.face(26)
        hour = ((axis_start + 3599) // 3600) * 3600
        while hour <= axis_end:
            local = datetime.fromtimestamp(hour, self.timezone)
            if local.minute == 0 and local.hour % 2 == 0:
                x = round(self.left + x_at(hour))
                width = hour_font.getlength(f'{local:%H}')
                x = min(max(x, self.left + round(width / 2)), self.right - round(width / 2))
                self.write(x, top + plot + 44, f'{local:%H}', hour_font, MUTED, 'ms')
            hour += 3600
        self.y = top + plot + 56

    def topics(self):
        topics = [topic for topic in self.report.get('topics') or [] if isinstance(topic, dict)]
        if not topics:
            return
        self.section('话题讨论')
        names = _people_names(self.report)
        for index, topic in enumerate(topics, 1):
            self.rule(self.left, self.right, self.y)
            self.y += 26
            self.topic(index, topic, names)
            self.y += 26

    def topic(self, index, topic, names):
        number_font, title_font, time_font = self.face(42, True), self.face(42, True), self.face(27)
        number = f'{index:02}'
        indent = 78
        time_range = topic_time_range(topic, self.timezone)
        title = self.clean(topic.get('topic') or '', True).strip() or '未命名话题'
        self.write(self.left, self.y + 45, number, number_font, ACCENT_TEXT)
        reserve = time_font.getlength(time_range) + 24 if time_range else 0
        one_line = title_font.getlength(title) <= self.content - indent - reserve
        if time_range and one_line:
            self.write(self.right, self.y + 45, time_range, time_font, MUTED, 'rs')
        self.paragraph(title, 42, INK, 60, bold=True, x=self.left + indent, width=self.content - indent)
        if time_range and not one_line:
            self.paragraph(time_range, 27, MUTED, 40, x=self.left + indent, width=self.content - indent)
        if topic.get('summary'):
            self.y += 4
            self.paragraph(topic['summary'], 37, BODY, 56)
        details = topic.get('details')
        for detail in (details if isinstance(details, list) else [details] if details else []):
            self.y += 6
            dot_y = self.y + 24
            self.ops.append(lambda canvas, draw, y=dot_y: draw.ellipse((self.left + 4, y - 5, self.left + 14, y + 5), fill=ACCENT))
            self.paragraph(detail, 34, BODY, 50, x=self.left + 34, width=self.content - 34)
        people = _topic_people(topic, names) if self.report.get('edition') != 'brief' else []
        if people:
            self.y += 14
            x = self.left
            for identity, name in people[:TOPIC_AVATAR_LIMIT]:
                self.avatar(x, self.y, 44, identity, name)
                x += 54
            if len(people) > TOPIC_AVATAR_LIMIT:
                self.write(x + 4, self.y + 32, f'+{len(people) - TOPIC_AVATAR_LIMIT}', self.face(27), MUTED)
            self.y += 44

    def quotes(self):
        quotes = _quotes(self.report)
        if not quotes:
            return
        self.section('群友金句')
        mark = self.face(96, True)
        for position, quote in enumerate(quotes):
            if position:
                self.y += 34
            source = _quote_source(quote)
            self.write(self.left + 50, self.y + 74, '”'.replace('”', '“'), mark, ACCENT, 'rs')  # CJK quote glyphs sit at the right of their cell
            self.paragraph(quote['text'], 40, INK, 60, x=self.left + 60, width=self.content - 60)
            self.y += 10
            speaker = str(quote.get('speaker') or source.get('author') or '群友')
            when = quote.get('time') or source.get('time')
            if not when and source.get('timestamp') is not None:
                when = time_text(source.get('timestamp'), '%m.%d %H:%M', self.timezone)
            self.avatar(self.left + 60, self.y, 44, quote.get('speaker_id') or source.get('author_id'), speaker)
            font = self.face(27)
            line = self.visible_name(speaker) + self.clean(f' · {when}' if when else '')
            self.write(self.left + 118, self.y + 32, _ellipsize(line, font, self.content - 118), font, MUTED)
            self.y += 44

    def rank(self):
        rows = ranked_participants(self.report)
        if not rows:
            return
        unit = rank_value(rows[0])[1]
        self.section('活跃前20名', '按发言 token 数' if unit == 'token' else f'按发言{unit}数')
        gap = 48
        cell = (self.content - gap) // 2
        name_font, value_font, number_font = self.face(33), self.face(29), self.face(26)
        half = (len(rows) + 1) // 2
        for position, row in enumerate(rows):
            column, line = (0, position) if position < half else (1, position - half)
            x, y = self.left + column * (cell + gap), self.y + line * 66
            name = _row_name(row)
            self.write(x, y + 41, f'{position + 1:02}', number_font, MUTED)
            self.avatar(x + 44, y + 5, 48, _row_identity(row), name)
            value = str(rank_value(row)[0])
            self.write(x + cell, y + 41, value, value_font, DEEP, 'rs')
            room = cell - 106 - value_font.getlength(value) - 12
            self.write(x + 106, y + 41, _ellipsize(self.visible_name(name), name_font, room), name_font, INK)
        self.y += half * 66

    def save(self, output_path):
        from PIL import Image, ImageDraw
        self.header()
        self.stats()
        self.one_line()
        self.rhythm()
        self.topics()
        self.quotes()
        self.rank()
        height = math.ceil(self.y) + 64
        canvas = Image.new('RGB', (self.width, height), PAPER)
        draw = ImageDraw.Draw(canvas)
        for op in self.ops:
            op(canvas, draw)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(path, format='PNG', optimize=True)


def render_png_pillow(report, output_path):
    try:
        import PIL  # noqa: F401
    except ImportError as exc:
        raise RuntimeError('Pillow is required for PNG report output') from exc
    _PngPage(report).save(output_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Render complete daily-report metadata as HTML or PNG.')
    parser.add_argument('report', type=Path, help='UTF-8 JSON file containing a report metadata object')
    parser.add_argument('-o', '--output', required=True, type=Path, help='Output file path')
    parser.add_argument('--format', choices=('html', 'png'), help='Output format (otherwise inferred from output extension)')
    args = parser.parse_args(argv)
    output_format = args.format or args.output.suffix.lower().lstrip('.')
    if output_format not in ('html', 'png'):
        parser.error('output format must be html or png (use --format)')
    try:
        with args.report.open(encoding='utf-8') as source:
            report = json.load(source)
        if not isinstance(report, dict):
            raise ValueError('report JSON must contain an object')
        if output_format == 'html':
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(render_html(report), encoding='utf-8')
        else:
            render_png_pillow(report, args.output)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f'error: {exc}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
