from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import tempfile
import sys
import time
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone, timedelta
from timezones import project_zone
import settings

import source_media
from report_web_style import reader_styles, site_page
import xml.etree.ElementTree as ET
_ROOT = Path(__file__).resolve().parents[1]
_RENDERER_DIR = next(path for path in (_ROOT / 'agent_console', _ROOT / 'source/agent_console')
                     if (path / 'daily_report.py').is_file())
sys.path.insert(0, str(_RENDERER_DIR))
import daily_report as report_renderer

REPORT_RENDERER_PATH = Path(report_renderer.__file__).resolve()

_PROJECT = settings.load_project()
TARGET_GROUP = _PROJECT.get('group', {}).get('conversation_id', '')
TARGET_IDS = settings.conversation_ids(_PROJECT) if TARGET_GROUP else set()
REPORT_PREFIX = _PROJECT['report_prefix']
DISPLAY_TZ = project_zone(_PROJECT['timezone'])
DAILY_HOUR = _PROJECT['daily_hour']
_DATA_URI_RE = re.compile(r"data:([\w.+-]+/[\w.+-]+)(?:;charset=[^;,]+)?;base64,([A-Za-z0-9+/=\r\n]+)", re.IGNORECASE)
_DATA_URI_HEADER_BYTES_RE = re.compile(
    rb"data:([A-Za-z0-9_.+-]+/[A-Za-z0-9_.+-]+)(?:;charset=[^;,]+)?;base64,",
    re.IGNORECASE,
)
_SCRIPT_OPEN_BYTES_RE = re.compile(rb"<script(?=\s|>)", re.IGNORECASE)
_REPORT_DATA_ID_BYTES_RE = re.compile(
    rb"""\bid\s*=\s*(?:"wechatagent-report-data"|'wechatagent-report-data')""",
    re.IGNORECASE,
)
_SCRIPT_CLOSE_BYTES_RE = re.compile(rb"</script\s*>", re.IGNORECASE)
_BASE64_BODY_RE = re.compile(rb"[A-Za-z0-9+/=\r\n]+")
_STREAM_CHUNK_SIZE = 1024 * 1024
_SPILL_THRESHOLD = 64 * 1024
_RECENT_MTIME_WINDOW_NS = 5_000_000_000
BUILD_CACHE_VERSION = 1
BUILD_CACHE_NAME = ".build-cache.json"
_EXTENSIONS = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
    "image/svg+xml": ".svg", "font/woff": ".woff", "font/woff2": ".woff2",
    "application/font-woff": ".woff", "application/x-font-ttf": ".ttf", "font/ttf": ".ttf",
    "application/vnd.ms-fontobject": ".eot", "application/octet-stream": ".bin",
}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(_STREAM_CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _atomic_write_if_changed(path: Path, data: bytes) -> None:
    try:
        if path.exists() and not path.is_symlink() and path.is_file():
            if path.stat().st_size == len(data):
                if len(data) <= 2 * 1024 * 1024:
                    if path.read_bytes() == data:
                        return
                elif _file_sha256(path) == hashlib.sha256(data).hexdigest():
                    return
    except OSError:
        pass
    _atomic_write(path, data)


def _rewrite_csp_meta(text: str) -> str:
    return re.sub(
        r"<meta\s+http-equiv=(['\"])Content-Security-Policy\1\s+content=(['\"]).*?\2\s*/?>",
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src \'self\'; font-src \'self\'">',
        text, flags=re.IGNORECASE | re.DOTALL,
    )


def _safe_stem(path: Path) -> str:
    # Keep a deterministic portable filename without trusting report metadata.
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._")
    return stem[:120] or hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:16]


def _extract_resources(source: str) -> tuple[str, dict[str, bytes]]:
    resources: dict[str, bytes] = {}

    def replace(match: re.Match[str]) -> str:
        mime, encoded = match.group(1).lower(), match.group(2)
        try:
            content = base64.b64decode(re.sub(r"\s+", "", encoded), validate=True)
        except (ValueError, base64.binascii.Error):
            return match.group(0)
        digest = hashlib.sha256(content).hexdigest()
        ext = _EXTENSIONS.get(mime, ".bin")
        name = f"{digest}{ext}"
        resources[name] = content
        return "../assets/" + name

    rewritten = _DATA_URI_RE.sub(replace, source)
    return _rewrite_csp_meta(rewritten), resources


def _find_next_marker(buf: bytes) -> tuple[int, str] | None:
    pos_script = -1
    search_from = 0
    lower_buf = buf.lower()
    while True:
        idx = lower_buf.find(b"<script", search_from)
        if idx == -1:
            break
        if idx + 7 == len(buf) or buf[idx + 7:idx + 8] in b" \t\r\n\f\v>":
            pos_script = idx
            break
        search_from = idx + 7
    pos_data = lower_buf.find(b"data:")
    if pos_script == -1 and pos_data == -1:
        return None
    if pos_script != -1 and (pos_data == -1 or pos_script < pos_data):
        return pos_script, "script"
    return pos_data, "data"


def _finalize_streamed_asset(
    mime: str,
    digest: str,
    decoded_len: int,
    mem_buf: bytearray,
    tmp_path: Path | None,
    assets_out: Path,
    written_assets: set[str],
) -> str:
    ext = _EXTENSIONS.get(mime, ".bin")
    name = f"{digest}{ext}"
    target = assets_out / name
    if name in written_assets:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        return name
    try:
        existing_ok = (
            target.exists() and not target.is_symlink() and target.is_file()
            and target.stat().st_size == decoded_len and _file_sha256(target) == digest
        )
    except OSError:
        existing_ok = False
    if existing_ok:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        written_assets.add(name)
        return name
    if tmp_path is not None:
        os.replace(tmp_path, target)
    else:
        _atomic_write(target, bytes(mem_buf))
    written_assets.add(name)
    return name


def _stream_extract_html(html_path: Path, assets_out: Path, written_assets: set[str]) -> tuple[str, list[str]]:
    """Stream HTML in fixed-size chunks, stripping report JSON and writing Base64 assets directly to disk."""
    assets_out.mkdir(parents=True, exist_ok=True)
    out_chunks: list[bytes] = []
    extracted_assets: list[str] = []
    seen_assets: set[str] = set()

    in_strip_script = False
    b64_state: dict | None = None

    def abort_b64() -> bytes:
        nonlocal b64_state
        state = b64_state
        b64_state = None
        if state["tmp_fp"] is not None:
            state["tmp_fp"].close()
        if state["tmp_path"] is not None:
            state["tmp_path"].unlink(missing_ok=True)
        return bytes(state["raw_buf"])

    def feed_b64_bytes(raw_token: bytes) -> bool:
        state = b64_state
        if len(state["raw_buf"]) + len(raw_token) <= _SPILL_THRESHOLD:
            state["raw_buf"].extend(raw_token)
        else:
            state["raw_overflow"] = True
        clean = raw_token.translate(None, b"\r\n")
        if not clean:
            return True
        if state["saw_pad"]:
            if any(ch != 61 for ch in clean) or len(state["b64_tail"]) + len(clean) > 8:
                return False
            state["b64_tail"].extend(clean)
            return True
        eq_idx = clean.find(b"=")
        if eq_idx != -1:
            after_eq = clean[eq_idx:]
            if any(ch != 61 for ch in after_eq) or len(after_eq) > 2:
                return False
            state["saw_pad"] = True
            prefix_len = len(state["b64_tail"]) + eq_idx
        else:
            prefix_len = len(state["b64_tail"]) + len(clean)
        state["b64_tail"].extend(clean)
        full = (prefix_len // 4) * 4
        if full:
            block = bytes(state["b64_tail"][:full])
            del state["b64_tail"][:full]
            try:
                decoded = base64.b64decode(block, validate=True)
            except (ValueError, base64.binascii.Error):
                return False
            state["hasher"].update(decoded)
            state["decoded_len"] += len(decoded)
            if state["tmp_fp"] is not None:
                state["tmp_fp"].write(decoded)
            elif len(state["mem_buf"]) + len(decoded) > _SPILL_THRESHOLD:
                fd, tmp_name = tempfile.mkstemp(prefix=".asset.", dir=assets_out)
                fp = os.fdopen(fd, "wb")
                fp.write(state["mem_buf"])
                fp.write(decoded)
                state["mem_buf"].clear()
                state["tmp_fp"] = fp
                state["tmp_path"] = Path(tmp_name)
            else:
                state["mem_buf"].extend(decoded)
        if state["saw_pad"] and len(state["b64_tail"]) > 8:
            return False
        return True

    def finish_b64() -> bytes:
        nonlocal b64_state
        state = b64_state
        if not state["has_body"]:
            return abort_b64()
        try:
            decoded = base64.b64decode(bytes(state["b64_tail"]), validate=True) if state["b64_tail"] else b""
        except (ValueError, base64.binascii.Error):
            if state["raw_overflow"]:
                raise ValueError("invalid oversized base64 data URI")
            return abort_b64()
        state["hasher"].update(decoded)
        state["decoded_len"] += len(decoded)
        if state["tmp_fp"] is not None:
            if decoded:
                state["tmp_fp"].write(decoded)
            state["tmp_fp"].flush()
            os.fsync(state["tmp_fp"].fileno())
            state["tmp_fp"].close()
            state["tmp_fp"] = None
        else:
            state["mem_buf"].extend(decoded)
        digest = state["hasher"].hexdigest()
        name = _finalize_streamed_asset(
            state["mime"], digest, state["decoded_len"], state["mem_buf"],
            state["tmp_path"], assets_out, written_assets,
        )
        b64_state = None
        rel = "assets/" + name
        if rel not in seen_assets:
            seen_assets.add(rel)
            extracted_assets.append(rel)
        return ("../" + rel).encode("ascii")

    buf = b""
    eof = False
    try:
        with html_path.open("rb") as stream:
            while True:
                if not eof and len(buf) < _STREAM_CHUNK_SIZE // 2:
                    chunk = stream.read(_STREAM_CHUNK_SIZE)
                    if chunk:
                        buf += chunk
                    else:
                        eof = True

                if b64_state is not None:
                    if not buf and eof:
                        out_chunks.append(finish_b64())
                        break
                    body_match = _BASE64_BODY_RE.match(buf)
                    end_idx = body_match.end() if body_match is not None else 0
                    if end_idx:
                        b64_state["has_body"] = True
                        if not feed_b64_bytes(buf[:end_idx]):
                            if b64_state["raw_overflow"]:
                                raise ValueError("invalid oversized base64 data URI")
                            raw = abort_b64()
                            out_chunks.append(raw)
                            continue
                        buf = buf[end_idx:]
                    if not buf:
                        if eof:
                            out_chunks.append(finish_b64())
                            break
                        continue
                    out_chunks.append(finish_b64())
                    continue

                if in_strip_script:
                    close_match = _SCRIPT_CLOSE_BYTES_RE.search(buf)
                    if close_match is not None:
                        buf = buf[close_match.end():]
                        in_strip_script = False
                        continue
                    if eof:
                        buf = b""
                        break
                    buf = buf[-16:]
                    continue

                if not buf and eof:
                    break

                marker = _find_next_marker(buf)
                if marker is None:
                    if eof:
                        out_chunks.append(buf)
                        buf = b""
                        break
                    if len(buf) > 16:
                        out_chunks.append(buf[:-16])
                        buf = buf[-16:]
                    else:
                        chunk = stream.read(_STREAM_CHUNK_SIZE)
                        if chunk:
                            buf += chunk
                        else:
                            eof = True
                    continue

                pos, kind = marker
                if pos > 0:
                    out_chunks.append(buf[:pos])
                    buf = buf[pos:]

                if kind == "script":
                    gt = buf.find(b">")
                    if gt == -1:
                        if eof or len(buf) >= 65536:
                            out_chunks.append(buf)
                            buf = b""
                            continue
                        chunk = stream.read(_STREAM_CHUNK_SIZE)
                        if chunk:
                            buf += chunk
                        else:
                            eof = True
                        continue
                    open_tag = buf[:gt + 1]
                    if _REPORT_DATA_ID_BYTES_RE.search(open_tag):
                        in_strip_script = True
                    else:
                        out_chunks.append(open_tag)
                    buf = buf[gt + 1:]
                    continue

                # kind == "data"
                header_match = _DATA_URI_HEADER_BYTES_RE.match(buf)
                if header_match is None:
                    if not eof and len(buf) < 256 and not any(ch in buf for ch in b",\"'>)"):
                        chunk = stream.read(_STREAM_CHUNK_SIZE)
                        if chunk:
                            buf += chunk
                            continue
                        eof = True
                    out_chunks.append(buf[:5])
                    buf = buf[5:]
                    continue

                header_bytes = buf[:header_match.end()]
                mime = header_match.group(1).decode("ascii").lower()
                buf = buf[header_match.end():]
                b64_state = {
                    "mime": mime,
                    "raw_buf": bytearray(header_bytes),
                    "raw_overflow": False,
                    "has_body": False,
                    "b64_tail": bytearray(),
                    "saw_pad": False,
                    "hasher": hashlib.sha256(),
                    "decoded_len": 0,
                    "mem_buf": bytearray(),
                    "tmp_fp": None,
                    "tmp_path": None,
                }
                if not buf and not eof:
                    chunk = stream.read(_STREAM_CHUNK_SIZE)
                    if chunk:
                        buf += chunk
                    else:
                        eof = True
    except BaseException:
        if b64_state is not None:
            if b64_state["tmp_fp"] is not None:
                b64_state["tmp_fp"].close()
            if b64_state["tmp_path"] is not None:
                b64_state["tmp_path"].unlink(missing_ok=True)
        raise

    text = b"".join(out_chunks).decode("utf-8")
    return _rewrite_csp_meta(text), extracted_assets


def _report_order(item: tuple[Path, dict]) -> tuple[int, str]:
    window = item[1].get("window")
    end = window.get("end_unix_exclusive") if isinstance(window, dict) else None
    if not isinstance(end, (int, float)) or isinstance(end, bool):
        end = window.get("end_unix") if isinstance(window, dict) else 0
    if not isinstance(end, (int, float)) or isinstance(end, bool):
        end = 0
    return (-int(end), Path(item[0]).name)


def _index_html(items: list[tuple[str, dict]]) -> bytes:
    groups: dict[tuple, list[tuple[str, dict]]] = {}
    for href, report in items:
        window = report.get('window') if isinstance(report.get('window'), dict) else {}
        request = report.get('request') if isinstance(report.get('request'), dict) else None
        key = ('request', request.get('id')) if request else ('window', window.get('start_unix'), window.get('end_unix_exclusive'))
        groups.setdefault(key, []).append((href, report))
    ordered_groups = sorted(groups.values(), key=lambda group: _report_order(group[0]))
    rows = []
    for group in ordered_groups:
        group.sort(key=lambda item: (0 if item[1].get('edition') == 'brief' else 1 if item[1].get('edition') == 'review' else 2, item[0]))
        primary_href, report = next((item for item in group if item[1].get('edition') == 'brief'), group[0])
        window = report.get('window') if isinstance(report.get('window'), dict) else {}
        stats = report.get('stats') if isinstance(report.get('stats'), dict) else {}
        category_label = '自定总结' if 'request' in report else '整日总结'
        start, finish = window.get('start_unix'), window.get('end_unix_exclusive', window.get('end_unix'))
        if 'request' in report and type(start) is int and type(finish) is int:
            zone = DISPLAY_TZ
            period = datetime.fromtimestamp(start, tz=zone).strftime('%Y-%m-%d %H:%M') + ' — ' + datetime.fromtimestamp(finish, tz=zone).strftime('%Y-%m-%d %H:%M')
        else:
            period = report.get('range_text') or (f'{start} – {finish}' if start is not None and finish is not None else '')
        messages, members = stats.get('messages', 0), stats.get(
            'members', report.get('participants_count', len(report.get('participants') or [])))
        summary = report.get('one_line', '')
        links = []
        for href, item in group:
            edition = item.get('edition')
            label = {'brief': '简报', 'review': '回顾'}.get(edition, '历史原版')
            links.append('<a href="' + html.escape(href, quote=True) + '">' + label + '</a>')
        link_bar = '<p class="editions">' + ' · '.join(links) + '</p>' if len(links) > 1 or report.get('edition') else ''
        rows.append('<article><h2><a href="' + html.escape(primary_href, quote=True) + '">' + category_label + '</a></h2>'
                    '<p class="period">' + html.escape(str(period)) + '</p><p class="stats">消息 ' + html.escape(str(messages))
                    + ' 条 · 成员 ' + html.escape(str(members)) + ' 人</p><p>' + html.escape(str(summary)) + '</p>' + link_bar + '</article>')
    body = '\n'.join(rows) if rows else '<p class="empty">暂无日报。</p>'
    return site_page('群聊日报', body)

def _sent_report_hashes(reports_dir: Path) -> set[str]:
    hashes = set()
    for path in (reports_dir.parent / 'digest/sent-receipts').glob('*.json'):
        try:
            receipt = json.loads(path.read_text(encoding='utf-8'))
            if receipt.get('status') == 'sent' and receipt.get('chat_id') in (TARGET_IDS | {TARGET_GROUP}):
                hashes.add(receipt['png_sha256'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return hashes


def _daily_window(window: dict) -> bool:
    start, end = window.get('start_unix'), window.get('end_unix_exclusive')
    if type(start) is not int or type(end) is not int or end - start != 24 * 3600:
        return False
    try:
        cutoff = datetime.fromtimestamp(end, DISPLAY_TZ)
    except (ValueError, OverflowError, OSError):
        return False
    return (cutoff.hour, cutoff.minute, cutoff.second) == (DAILY_HOUR, 0, 0)


def _valid_on_demand(record: dict, stem: str) -> bool:
    request = record.get('request')
    window = record.get('window')
    if not isinstance(request, dict) or not isinstance(window, dict):
        return False
    request_id = request.get('id')
    start, end = window.get('start_unix'), window.get('end_unix_exclusive')
    edition = record.get('edition')
    expected_stem = REPORT_PREFIX + '-request-' + str(request_id)
    if edition is not None:
        if edition not in ('brief', 'review') or stem != expected_stem + '-' + edition:
            return False
    elif stem != expected_stem:
        return False
    if (request.get('kind') != 'on_demand'
            or not isinstance(request_id, str) or re.fullmatch(r'[0-9a-f]{64}', request_id) is None
            or type(start) is not int or type(end) is not int or start >= end
            or type(request.get('trigger_timestamp')) is not int or request['trigger_timestamp'] != end):
        return False
    try:
        datetime.fromtimestamp(start, timezone.utc)
        datetime.fromtimestamp(end, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return False
    return True


def _source_path(reports_dir: Path, stem: str, edition: str | None) -> Path:
    if edition in ('brief', 'review'):
        base = stem[:-(len(edition) + 1)]
        return reports_dir / f'{base}-editions.sources.json'
    return reports_dir / f'{stem}.sources.json'


class _RankMarkup(HTMLParser):
    """Locate rank contents and their own preceding heading without editing other text."""
    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.offsets = [0] + [match.end() for match in re.finditer('\n', source)]
        self.heading_start = None
        self.heading = None
        self.depth = 0
        self.blocks = []
        self.feed(source)

    def _position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag in ('h2', 'h3') and not self.depth:
            self.heading_start = self._position() + len(self.get_starttag_text())
        if tag in ('ol', 'ul'):
            if self.depth:
                self.depth += 1
            elif 'rank' in dict(attrs).get('class', '').split():
                self.depth = 1
                self.rank_start = self._position() + len(self.get_starttag_text())
                self.rank_heading = self.heading

    def handle_endtag(self, tag):
        if tag in ('h2', 'h3') and self.heading_start is not None:
            self.heading = (self.heading_start, self._position())
            self.heading_start = None
        if tag in ('ol', 'ul') and self.depth:
            self.depth -= 1
            if not self.depth:
                self.blocks.append((self.rank_start, self._position(), self.rank_heading))


class _ChartMarkup(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.offsets = [0] + [match.end() for match in re.finditer('\n', source)]
        self.depth = 0
        self.blocks = []
        self.feed(source)

    def _position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag == 'div':
            if self.depth:
                self.depth += 1
            elif 'chart' in dict(attrs).get('class', '').split():
                self.depth = 1
                self.start = self._position() + len(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag == 'div' and self.depth:
            self.depth -= 1
            if not self.depth:
                self.blocks.append((self.start, self._position()))


class _TopicMarkup(HTMLParser):
    """Locate generated topic headings, visible times and directory links."""
    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.offsets = [0] + [match.end() for match in re.finditer('\n', source)]
        self.current = None
        self.link = None
        self.blocks = []
        self.links = []
        self.feed(source)

    def _position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        position = self._position()
        after = position + len(self.get_starttag_text())
        if tag == 'article' and 'topic' in attrs.get('class', '').split():
            match = re.fullmatch(r'topic-(\d+)', attrs.get('id', ''))
            if match:
                self.current = {'index': int(match[1]) - 1, 'time': None}
        elif self.current is not None:
            if tag == 'h3':
                self.current['heading_start'] = after
            elif tag == 'p' and 'topic-time' in attrs.get('class', '').split():
                self.current['time_start'] = position
        if tag == 'a':
            match = re.fullmatch(r'#topic-(\d+)', attrs.get('href', ''))
            if match:
                self.link = (int(match[1]) - 1, after)

    def handle_endtag(self, tag):
        position = self._position()
        after = self.source.index('>', position) + 1
        if self.current is not None:
            if tag == 'h3' and 'heading_start' in self.current:
                self.current['heading'] = (self.current['heading_start'], position)
                self.current['after_heading'] = after
            elif tag == 'p' and 'time_start' in self.current:
                self.current['time'] = (self.current.pop('time_start'), after)
            elif tag == 'article':
                self.blocks.append(self.current)
                self.current = None
        if tag == 'a' and self.link is not None:
            self.links.append((*self.link, position))
            self.link = None


def _report_zone(record: dict):
    """The zone a report was written in; pages rebuilt here must show the times the report shows."""
    try:
        return project_zone(str(record['timezone'])) if record.get('timezone') else DISPLAY_TZ
    except (KeyError, ValueError):
        return DISPLAY_TZ


def _refresh_topics(source: str, record: dict) -> str:
    topics = record.get('topics') or []
    zone = _report_zone(record)
    parser = _TopicMarkup(source)
    edits = []
    for block in parser.blocks:
        index = block['index']
        if not 0 <= index < len(topics) or 'heading' not in block:
            continue
        topic = topics[index]
        title = topic['topic']
        edits.append((*block['heading'], f'<span>{index + 1:02}</span>{html.escape(title)}'))
        time_range = report_renderer.topic_time_range(topic, zone)
        time_html = f'<p class="topic-time">{html.escape(time_range)}</p>' if time_range else ''
        start, end = block['time'] or (block['after_heading'], block['after_heading'])
        edits.append((start, end, time_html))
    for index, start, end in parser.links:
        if 0 <= index < len(topics):
            edits.append((start, end, html.escape(topics[index]['topic'])))
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def _source_messages(record: dict, source_path: Path) -> list[dict]:
    snapshot = json.loads(source_path.read_text(encoding='utf-8'))
    if snapshot.get('conversation_id', snapshot.get('group_id')) != record['chat_username'] or snapshot.get('window') != record['window']:
        raise ValueError('source snapshot does not match the report')
    messages = snapshot['messages']
    if len(messages) != record.get('stats', {}).get('messages'):
        raise ValueError('source message count does not match the report')
    return messages


def _refresh_chart(source: str, record: dict, source_path: Path) -> str:
    parser = _ChartMarkup(source)
    if not parser.blocks:
        return source
    if record.get('rhythm_bucket_seconds') != 600 or 'rhythm_buckets' not in record:
        messages = _source_messages(record, source_path)
        window = record['window']
        buckets = report_renderer.build_rhythm_buckets(
            (item['timestamp'] for item in messages), window['start_unix'], window['end_unix_exclusive'])
        record = {**record, 'rhythm_buckets': buckets, 'rhythm_bucket_seconds': 600}
    contents = report_renderer.rhythm_chart_contents(record)
    for start, end in reversed(parser.blocks):
        source = source[:start] + contents + source[end:]
    return source


def _refresh_rank(source: str, record: dict, source_path: Path) -> str:
    if not record.get('participants') and source_path.is_file():
        messages = _source_messages(record, source_path)
        counts = Counter(item['author_id'] for item in messages if item.get('author_id'))
        names = {item['author_id']: item['author'] for item in messages if item.get('author_id')}
        record = {**record, 'participants': [{'username': uid, 'name': names[uid], 'count': count}
                                           for uid, count in counts.items()]}
    parser = _RankMarkup(source)
    rows = report_renderer.rank_rows_html(record)
    edits = []
    for start, end, heading in parser.blocks:
        edits.append((start, end, rows))
        if heading:
            edits.append((*heading, '活跃前20名'))
    for start, end, replacement in sorted(set(edits), reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def _edition_key(record: dict) -> tuple:
    request = record.get('request') if isinstance(record.get('request'), dict) else None
    if request:
        return ('request', request.get('id'))
    window = record.get('window') if isinstance(record.get('window'), dict) else {}
    return ('window', window.get('start_unix'), window.get('end_unix_exclusive'))
class _SourceMarkup(HTMLParser):
    """Locate source lists, classic inline excerpts, and quote-source details."""
    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.offsets = [0] + [match.end() for match in re.finditer('\n', source)]
        self.article = None
        self.quote = None
        self.quote_count = 0
        self.quote_details_depth = 0
        self.quote_start = None
        self.pending = None
        self.blocks = []
        self.legacy_topic = None
        self.legacy_count = 0
        self.legacy_p_start = None
        self.legacy_blocks = set()
        self.feed(source)

    def _pos(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'section' and 'topic' in attrs.get('class', '').split():
            self.legacy_topic = self.legacy_count
            self.legacy_count += 1
        elif tag == 'p' and self.legacy_topic is not None:
            self.legacy_p_start = self._pos()
        if tag == 'article' and 'topic' in attrs.get('class', '').split():
            match = re.fullmatch(r'topic-(\d+)', attrs.get('id', ''))
            self.article = int(match[1]) - 1 if match else None
        elif tag == 'blockquote':
            self.quote = self.quote_count
            self.quote_count += 1
        elif tag == 'details' and self.quote is not None:
            self.quote_details_depth += 1
        elif tag == 'ul' and self.pending is not None:
            self.pending[2] += 1
        elif (tag == 'ul' and self.quote is None and self.article is not None
              and 'sources' in attrs.get('class', '').split()):
            self.pending = [self._pos() + len(self.get_starttag_text()), self.article, 1]

    def handle_endtag(self, tag):
        position = self._pos()
        if tag == 'summary' and self.quote_details_depth == 1 and self.quote_start is None:
            self.quote_start = self.source.index('>', position) + 1
        elif tag == 'details' and self.quote_details_depth:
            self.quote_details_depth -= 1
            if self.quote_details_depth == 0:
                if self.quote_start is not None:
                    self.blocks.append((self.quote_start, position, None, self.quote))
                self.quote_start = None
        elif tag == 'ul' and self.pending is not None:
            self.pending[2] -= 1
            if self.pending[2] == 0:
                start, article, _ = self.pending
                self.blocks.append((start, position, article, None))
                self.pending = None
        elif tag == 'blockquote':
            self.quote = None
            self.quote_details_depth = 0
            self.quote_start = None
        elif tag == 'article':
            self.article = None
        if tag == 'p' and self.legacy_p_start is not None:
            start = self.legacy_p_start
            end = self.source.index('>', position) + 1
            if re.search(r'\[M\d{4,}\]', self.source[start:end]):
                self.blocks.append((start, end, self.legacy_topic, None))
                self.legacy_blocks.add((start, end))
            self.legacy_p_start = None
        elif tag == 'section':
            self.legacy_topic = None


def _bound_source(ref: dict, by_uid: dict, by_reference: dict) -> dict | None:
    uid = ref.get('message_uid') or ref.get('message_id')
    message = by_uid.get(uid) if isinstance(uid, str) else None
    if message is None:
        reference = ref.get('reference')
        message = by_reference.get(reference) if isinstance(reference, str) else None
    if message is None:
        return None
    return {key: message.get(key, '') for key in ('message_uid', 'reference', 'author', 'author_id', 'timestamp', 'type', 'text')}


def _refresh_sources(source: str, record: dict, source_path: Path, reports_dir: Path,
                     assets_out: Path, image_cache: dict, avatar_fallback: dict, verified_media: dict,
                     *, details: dict | None = None):
    parser = _SourceMarkup(source)
    if not parser.blocks:
        if details is not None:
            details['cited_image_uids'] = []
            details['fallback_avatar_authors'] = []
        return source, [], []
    has_snapshot = source_path.is_file()
    messages = _source_messages(record, source_path) if has_snapshot else []
    image_requests_by_uid = source_media.source_image_requests(messages) if has_snapshot else {}
    image_sources_by_uid = source_media.source_image_uids(messages, image_cache) if has_snapshot else {}
    by_uid = {m.get('message_uid'): m for m in messages if isinstance(m.get('message_uid'), str)}
    by_reference = {m.get('reference'): m for m in messages if isinstance(m.get('reference'), str)}
    refs_by_topic = []
    for topic in record.get('topics') or []:
        refs = []
        for old in topic.get('references') or []:
            if isinstance(old, dict):
                bound = _bound_source(old, by_uid, by_reference) if has_snapshot else dict(old)
            elif isinstance(old, str):
                bound = _bound_source({'reference': old}, by_uid, by_reference)
            else:
                bound = None
            if bound and bound not in refs:
                refs.append(bound)
        if not refs:
            for uid in topic.get('message_ids') or []:
                msg = by_uid.get(uid)
                if msg:
                    refs.append(_bound_source({'message_uid': uid}, by_uid, by_reference))
        topic['references'] = refs
        refs_by_topic.append(refs)
    quote_refs = []
    for quote in record.get('quotes') or []:
        old = quote.get('source') if isinstance(quote.get('source'), dict) else quote
        bound = _bound_source(old, by_uid, by_reference) if has_snapshot else report_renderer._quote_source(quote)
        quote['source'] = bound
        quote_refs.append([bound] if bound else [])
    all_refs = [ref for group in refs_by_topic for ref in group] + [ref for group in quote_refs for ref in group]
    cited_uids: set[str] = set()
    for ref in all_refs:
        uid = ref.get('message_uid')
        image_uids = image_sources_by_uid.get(uid, []) if isinstance(uid, str) else []
        ref['image_source_uids'] = image_uids
        ref['image_source_requested'] = image_requests_by_uid.get(uid, False) if isinstance(uid, str) else False
        cited_uids.update(u for u in image_uids if isinstance(u, str) and u)
        if not image_uids and ref.get('type') == 'image' and isinstance(uid, str) and uid:
            cited_uids.add(uid)
        if image_uids:
            ref.pop('image_unavailable_reason', None)
        elif ref['image_source_requested'] or ref.get('type') == 'image':
            ref['image_unavailable_reason'] = '该来源没有可验证的已归档原图，暂不可用'
    avatars = dict(record.get('avatars')) if isinstance(record.get('avatars'), dict) else {}
    fallback_authors: set[str] = set()
    ref_authors = {ref.get('author_id') for ref in all_refs if isinstance(ref.get('author_id'), str) and ref.get('author_id')}
    for author_id in ref_authors:
        existing = avatars.get(author_id)
        if not isinstance(existing, dict) or existing.get('available') is not True:
            fallback_authors.add(author_id)
    for author_id, entry in avatar_fallback.items():
        existing = avatars.get(author_id)
        if not isinstance(existing, dict) or existing.get('available') is not True:
            avatars[author_id] = entry
    avatar_assets = set()
    for author_id in {ref.get('author_id') for ref in all_refs}:
        entry = avatars.get(author_id)
        if not isinstance(entry, dict) or entry.get('available') is not True:
            continue
        uri = entry.get('data_uri')
        if not isinstance(uri, str) or not uri.startswith('data:image/'):
            continue
        uri_hash = hashlib.sha256(uri.encode('utf-8')).hexdigest()
        key = ('avatar', uri_hash, str(assets_out))
        asset_src = verified_media.get(key)
        if asset_src is None or not (assets_out / Path(asset_src).name).is_file():
            asset_src, resources = _extract_resources(uri)
            if not re.fullmatch(r'\.\./assets/[0-9a-f]{64}\.(?:png|jpe?g|gif|webp)', asset_src, re.I):
                avatars[author_id] = {'available': False}
                continue
            for name, payload in resources.items():
                _atomic_write_if_changed(assets_out / name, payload)
            verified_media[key] = asset_src
        avatars[author_id] = {**entry, 'asset_src': asset_src, 'data_uri': ''}
        avatar_assets.add('assets/' + Path(asset_src).name)
    image_src, deps, assets = source_media.publish_referenced_images(
        reports_dir, all_refs, assets_out, image_cache, verified=verified_media)
    if details is not None:
        details['cited_image_uids'] = sorted(cited_uids)
        details['fallback_avatar_authors'] = sorted(fallback_authors)
    edits = []
    for start, end, topic_index, quote_index in parser.blocks:
        refs = []
        if quote_index is not None and 0 <= quote_index < len(quote_refs):
            refs = quote_refs[quote_index]
        elif topic_index is not None and 0 <= topic_index < len(refs_by_topic):
            refs = refs_by_topic[topic_index]
        for ref in refs:
            ref['image_sources'] = [image_src[source_uid] for source_uid in ref.get('image_source_uids', [])
                                    if source_uid in image_src]
            if ref['image_sources']:
                ref.pop('image_unavailable_reason', None)
            elif ref.get('image_source_uids') or ref.get('type') == 'image':
                ref['image_unavailable_reason'] = '该来源没有可验证的已归档原图，暂不可用'
        contents = report_renderer.source_messages_html(refs, avatars, _report_zone(record))
        if (start, end) in parser.legacy_blocks:
            if refs:
                contents = (f"<details><summary>来源 · {len(refs)} 条消息</summary>"
                            "<ul class='sources'>" + contents + '</ul></details>')
            else:
                contents = re.sub(r'\[M\d{4,}\]\s*', '', source[start:end])
        elif quote_index is not None:
            contents = "<ul class='sources'>" + contents + '</ul>'
        edits.append((start, end, contents))
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source, deps, sorted(set(assets) | avatar_assets)


class _LegacyBodyText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.current = None
        self.records = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get('class', '').split())
        parent = self.stack[-1] if self.stack else None
        item = {'tag': tag, 'classes': classes, 'capture': False}
        if 'lead' in classes:
            self.current = {'lead': '', 'topics': []}
            self.records.append(self.current)
            item['capture'] = True
            item['target'] = ('lead', self.current)
        elif tag == 'section' and 'topic' in classes:
            if not self.current:
                self.current = {'lead': '', 'topics': []}
                self.records.append(self.current)
            topic = {'topic': '', 'summary': '', 'details': []}
            self.current['topics'].append(topic)
            item['topic'] = topic
        elif tag == 'b' and parent and parent['tag'] in {'div', 'header'} and 'topic-head' in parent['classes']:
            topic = next((node.get('topic_obj') for node in reversed(self.stack) if node.get('topic_obj')), None)
            if topic:
                item['capture'] = True
                item['target'] = ('topic', topic)
        elif tag == 'p' and parent and parent['tag'] == 'section' and 'topic' in parent['classes']:
            topic = parent.get('topic_obj')
            if topic:
                item['capture'] = True
                item['target'] = ('paragraph', topic)
        if tag == 'section' and 'topic' in classes:
            item['topic_obj'] = topic
        self.stack.append(item)

    def handle_endtag(self, tag):
        while self.stack:
            item = self.stack.pop()
            if item['tag'] == tag:
                break

    def handle_data(self, data):
        for item in self.stack:
            if item.get('capture'):
                kind, target = item['target']
                if kind == 'lead':
                    target['lead'] += data
                elif kind == 'topic':
                    target['topic'] += data
                else:
                    target['details'].append(data)


def _legacy_body_count(source: str) -> int | None:
    parser = _LegacyBodyText()
    parser.feed(source)
    if not parser.records or not any(item['topics'] for item in parser.records):
        return None
    view = parser.records[-1]
    return report_renderer.body_chinese_count({'one_line': view['lead'], 'topics': view['topics'], 'quotes': []})


def _refresh_header_metrics(source: str, record: dict) -> str:
    source = re.sub(r"(<small\b[^>]*>\s*高峰时段\s*</small>\s*<b\b[^>]*>).*?(</b>)",
                    lambda match: match[1] + html.escape(report_renderer.peak_time_text(record)) + match[2],
                    source, count=1, flags=re.I | re.S)
    legacy_count = _legacy_body_count(source)
    count = legacy_count if legacy_count is not None else report_renderer.body_chinese_count(record)
    badge = (f'<p class="body-chinese-count muted">正文共 '
             f'{count} 字（仅计中文）</p>')
    pattern = re.compile(r"<p\b[^>]*class=[\"'][^\"']*\bbody-chinese-count\b[^\"']*[\"'][^>]*>.*?</p\s*>", re.I | re.S)
    if pattern.search(source):
        return pattern.sub(lambda _: badge, source, count=1)
    anchor = (re.search(r'</header\s*>', source, re.I)
              or re.search(r'</h1\s*>', source, re.I)
              or re.search(r'<body\b[^>]*>', source, re.I))
    return source[:anchor.end()] + badge + source[anchor.end():] if anchor else source


def _build_env_fingerprint() -> str:
    code_files = [
        Path(__file__),
        Path(__file__).with_name("source_media.py"),
        Path(__file__).with_name("report_web_style.py"),
        REPORT_RENDERER_PATH,
        REPORT_RENDERER_PATH.with_name("report_metrics.py"),
    ]
    code_hashes = []
    for path in code_files:
        try:
            code_hashes.append((path.name, _file_sha256(path) if path.is_file() else None))
        except OSError:
            code_hashes.append((path.name, None))
    payload = {
        "version": BUILD_CACHE_VERSION,
        "target_group": TARGET_GROUP,
        "target_ids": sorted(TARGET_IDS),
        "report_prefix": REPORT_PREFIX,
        "display_tz": str(DISPLAY_TZ),
        "daily_hour": DAILY_HOUR,
        "code_hashes": code_hashes,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _path_sig(path: Path, now_ns: int) -> list | None:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        st = path.stat()
        sha = _file_sha256(path) if abs(now_ns - st.st_mtime_ns) < _RECENT_MTIME_WINDOW_NS else None
        return [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, sha]
    except OSError:
        return None


def _hash_json_obj(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _compact_meta(record: dict) -> dict:
    meta = {
        "chat_username": record.get("chat_username"),
        "window": record.get("window"),
        "day": record.get("day"),
        "range_text": record.get("range_text"),
        "stats": record.get("stats"),
        "participants_count": len(record.get("participants") or []),
        "one_line": record.get("one_line", ""),
    }
    if "request" in record:
        meta["request"] = record["request"]
    if "edition" in record:
        meta["edition"] = record["edition"]
    return meta


def _load_build_cache(cache_file: Path, env_fingerprint: str) -> dict[str, dict]:
    try:
        if cache_file.is_symlink() or not cache_file.is_file():
            return {}
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("env") != env_fingerprint:
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def _save_build_cache(cache_file: Path, env_fingerprint: str, entries: dict[str, dict]) -> None:
    payload = json.dumps({"env": env_fingerprint, "entries": entries}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix="." + cache_file.name + ".", dir=cache_file.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, cache_file)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _cache_entry_valid(
    entry: dict | None,
    *,
    html_sig: list | None,
    json_sig: list | None,
    png_sig: list | None,
    source_sig: list | None,
    peer_href: str | None,
    report_out: Path,
    output_dir: Path,
    name: str,
    now_ns: int,
    image_cache: dict,
    avatar_union_digests: dict[str, str],
) -> bool:
    if not isinstance(entry, dict):
        return False
    if (entry.get("html_sig") != html_sig or entry.get("json_sig") != json_sig
            or entry.get("png_sig") != png_sig or entry.get("source_sig") != source_sig
            or entry.get("peer_href") != peer_href):
        return False
    out_html = report_out / name
    if out_html.is_symlink() or not out_html.is_file():
        return False
    assets = entry.get("assets")
    source_deps_sigs = entry.get("source_deps_sigs")
    cited_uids = entry.get("cited_image_uids")
    fallback_authors = entry.get("fallback_avatar_authors")
    if (not isinstance(assets, list) or not isinstance(source_deps_sigs, list)
            or not isinstance(cited_uids, list) or not isinstance(fallback_authors, list)):
        return False
    for asset_rel in assets:
        if not isinstance(asset_rel, str):
            return False
        asset_path = output_dir / asset_rel
        if asset_path.is_symlink() or not asset_path.is_file():
            return False
    for dep_pair in source_deps_sigs:
        if not isinstance(dep_pair, list) or len(dep_pair) != 2:
            return False
        dep_path, dep_sig = dep_pair
        if not isinstance(dep_path, str) or _path_sig(Path(dep_path), now_ns) != dep_sig:
            return False
    cited_sig = _hash_json_obj([(uid, image_cache.get(uid)) for uid in cited_uids])
    if entry.get("cited_cache_sig") != cited_sig:
        return False
    fallback_sig = _hash_json_obj([(aid, avatar_union_digests.get(aid)) for aid in fallback_authors])
    if entry.get("fallback_avatar_sig") != fallback_sig:
        return False
    return True


def build_site(reports_dir: Path, output_dir: Path) -> dict:
    reports_dir, output_dir = Path(reports_dir), Path(output_dir)
    if not TARGET_GROUP:
        raise ValueError('explicit group.conversation_id in runtime/project.json is required to build a site')
    sent_hashes = _sent_report_hashes(reports_dir)
    discovered: list[tuple[Path, Path, Path, dict]] = []
    avatar_candidates: dict[str, tuple[tuple[int, str], dict]] = {}
    for json_path in sorted(reports_dir.glob("*.json")):
        if json_path.name == "sources.json" or json_path.name.endswith(".sources.json"):
            continue
        stem = json_path.stem
        html_path, png_path = reports_dir / f"{stem}.html", reports_dir / f"{stem}.png"
        if not all(path.is_file() and path.stat().st_size > 0 for path in (json_path, html_path, png_path)):
            continue
        try:
            record = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or record.get("chat_username") not in (TARGET_IDS | {TARGET_GROUP}):
            continue
        window = record.get("window")
        if (not isinstance(window, dict) or not isinstance(window.get("end_unix_exclusive"), (int, float))
                or isinstance(window.get("end_unix_exclusive"), bool)):
            continue
        edition = record.get('edition')
        if edition is not None:
            if edition not in ('brief', 'review'):
                continue
            if 'request' in record:
                if not _valid_on_demand(record, stem):
                    continue
            elif (re.fullmatch(re.escape(REPORT_PREFIX) + r'-\d{8}-\d{4}-(?:brief|review)', stem) is None
                  or not stem.endswith('-' + edition) or not _daily_window(window)):
                continue
        elif 'request' in record:
            if not _valid_on_demand(record, stem):
                continue
        elif re.fullmatch(re.escape(REPORT_PREFIX) + r'-\d{8}-\d{4}-(?:brief|review)', stem):
            continue
        elif not _daily_window(window):
            if _file_sha256(png_path) not in sent_hashes:
                continue
        meta = _compact_meta(record)
        order_key = _report_order((html_path, meta))
        raw_avatars = record.get('avatars') if isinstance(record.get('avatars'), dict) else {}
        for author_id, entry in raw_avatars.items():
            if isinstance(entry, dict) and entry.get('available') is True and entry.get('data_uri'):
                prev = avatar_candidates.get(author_id)
                if prev is None or order_key < prev[0]:
                    avatar_candidates[author_id] = (order_key, entry)
        discovered.append((html_path, json_path, png_path, meta))
    edition_groups: dict[str, dict[str, tuple[Path, Path, Path, dict]]] = {}
    for item in discovered:
        path, _, _, record = item
        edition = record.get('edition')
        if edition in ('brief', 'review'):
            stem = path.stem
            base = stem[:-(len(edition) + 1)]
            edition_groups.setdefault(base, {})[edition] = item
    complete_bases = set()
    for base, editions in edition_groups.items():
        if set(editions) != {'brief', 'review'}:
            continue
        brief_record, review_record = editions['brief'][3], editions['review'][3]
        keys = ('chat_username', 'window', 'request', 'day', 'range_text', 'stats')
        if all(brief_record.get(key) == review_record.get(key) for key in keys):
            complete_bases.add(base)
    reports = [item for item in discovered
               if item[3].get('edition') is None
               or item[0].stem[:-(len(item[3]['edition']) + 1)] in complete_bases]

    reports.sort(key=lambda item: _report_order((item[0], item[3])))
    edition_hrefs = {(_edition_key(record), record['edition']): 'reports/' + _safe_stem(path) + '.html'
                     for path, _, _, record in reports if record.get('edition') in ('brief', 'review')}
    output_dir.mkdir(parents=True, exist_ok=True)
    report_out, assets_out = output_dir / "reports", output_dir / "assets"
    report_out.mkdir(exist_ok=True)
    assets_out.mkdir(exist_ok=True)
    published: list[str] = []
    published_set: set[str] = set()
    source_media_files: set[str] = set()
    index_items = []
    image_cache = source_media.load_cache(reports_dir)
    verified_media = {}
    written_assets: set[str] = set()
    avatar_union = {author_id: pair[1] for author_id, pair in avatar_candidates.items()}
    avatar_union_digests = {
        author_id: hashlib.sha256(entry['data_uri'].encode('utf-8')).hexdigest()
        for author_id, entry in avatar_union.items()
    }
    del avatar_candidates
    env_fingerprint = _build_env_fingerprint()
    cache_file = output_dir / BUILD_CACHE_NAME
    cached_entries = _load_build_cache(cache_file, env_fingerprint)
    new_cache_entries: dict[str, dict] = {}
    now_ns = time.time_ns()

    for html_path, json_path, png_path, meta in reports:
        name = _safe_stem(html_path) + ".html"
        href = "reports/" + name
        edition = meta.get('edition')
        other = 'review' if edition == 'brief' else 'brief'
        peer_href = edition_hrefs.get((_edition_key(meta), other)) if edition in ('brief', 'review') else None
        source_path = _source_path(reports_dir, html_path.stem, edition)
        html_sig = _path_sig(html_path, now_ns)
        json_sig = _path_sig(json_path, now_ns)
        png_sig = _path_sig(png_path, now_ns)
        source_sig = _path_sig(source_path, now_ns)

        cached = cached_entries.get(name)
        if _cache_entry_valid(
            cached, html_sig=html_sig, json_sig=json_sig, png_sig=png_sig,
            source_sig=source_sig, peer_href=peer_href, report_out=report_out,
            output_dir=output_dir, name=name, now_ns=now_ns,
            image_cache=image_cache, avatar_union_digests=avatar_union_digests,
        ):
            for asset_rel in cached["assets"]:
                if asset_rel not in published_set:
                    published_set.add(asset_rel)
                    published.append(asset_rel)
            source_media_files.update(cached["source_deps"])
            new_cache_entries[name] = cached
            if href not in published_set:
                published_set.add(href)
                published.append(href)
            index_items.append((href, meta))
            continue

        record = json.loads(json_path.read_text(encoding="utf-8"))
        # Stream large inline fonts/images directly to assets/ before HTML parser passes.
        source, inline_assets = _stream_extract_html(html_path, assets_out, written_assets)
        refresh_details: dict = {}
        source, source_deps, source_assets = _refresh_sources(
            source, record, source_path, reports_dir, assets_out, image_cache,
            avatar_union, verified_media, details=refresh_details)
        source_media_files.update(source_deps)
        source = _refresh_rank(source, record, source_path)
        source = _refresh_topics(source, record)
        source = _refresh_header_metrics(source, record)
        source = _refresh_chart(source, record, source_path)
        rewritten, resources = _extract_resources(source)
        switch = (' · <a href="' + html.escape(PurePosixPath(peer_href).name, quote=True) + '">切换到' + ('详细回顾' if other == 'review' else '简报') + '</a>') if peer_href else ''
        back = '<p class="site-home"><a href="../index.html">返回日报首页</a>' + switch + '</p>'
        rewritten = re.sub(r"(<body(?:\s[^>]*)?>)", r"\1" + back, rewritten, count=1, flags=re.IGNORECASE)
        reader_css = reader_styles(report_renderer)
        if not re.search(r'<meta\b[^>]*name\s*=\s*["\']?viewport', rewritten, re.I):
            reader_css += '<meta name="viewport" content="width=device-width,initial-scale=1">'
        if not re.search(r'<title\b', rewritten, re.I):
            reader_css += '<title>群聊日报 · ' + html.escape(str(record.get('day', ''))) + '</title>'
        rewritten = rewritten.replace('</head>', reader_css + '</head>', 1)
        report_assets: list[str] = []
        report_assets_set: set[str] = set()
        for rel in [*inline_assets, *source_assets]:
            if rel not in report_assets_set:
                report_assets_set.add(rel)
                report_assets.append(rel)
        for resource_name, content in sorted(resources.items()):
            if resource_name not in written_assets:
                _atomic_write_if_changed(assets_out / resource_name, content)
                written_assets.add(resource_name)
            resource_relative = "assets/" + resource_name
            if resource_relative not in report_assets_set:
                report_assets_set.add(resource_relative)
                report_assets.append(resource_relative)
        for rel in report_assets:
            if rel not in published_set:
                published_set.add(rel)
                published.append(rel)
        _atomic_write_if_changed(report_out / name, rewritten.encode("utf-8"))
        if href not in published_set:
            published_set.add(href)
            published.append(href)
        index_items.append((href, meta))
        cited_uids = refresh_details.get("cited_image_uids", [])
        fallback_authors = refresh_details.get("fallback_avatar_authors", [])
        new_cache_entries[name] = {
            "html_sig": html_sig,
            "json_sig": json_sig,
            "png_sig": png_sig,
            "source_sig": source_sig,
            "peer_href": peer_href,
            "assets": report_assets,
            "source_deps": list(source_deps),
            "source_deps_sigs": [[dep, _path_sig(Path(dep), now_ns)] for dep in source_deps],
            "cited_image_uids": cited_uids,
            "cited_cache_sig": _hash_json_obj([(uid, image_cache.get(uid)) for uid in cited_uids]),
            "fallback_avatar_authors": fallback_authors,
            "fallback_avatar_sig": _hash_json_obj([(aid, avatar_union_digests.get(aid)) for aid in fallback_authors]),
        }
    # Remove stale generated report pages/assets so a sync cannot expose obsolete files.
    expected_reports = {PurePosixPath(p).name for p in published if p.startswith("reports/")}
    for path in report_out.iterdir():
        if path.is_file() and path.suffix == ".html" and path.name not in expected_reports:
            path.unlink()
    expected_assets = {PurePosixPath(p).name for p in published if p.startswith("assets/")}
    for path in assets_out.iterdir():
        if path.is_file() and re.fullmatch(r"[0-9a-f]{64}\.[a-z0-9]+", path.name) and path.name not in expected_assets:
            path.unlink()
    _atomic_write_if_changed(output_dir / "index.html", _index_html(index_items))
    _save_build_cache(cache_file, env_fingerprint, new_cache_entries)
    return {"reports": len(reports), "files": sorted([*published, "index.html"]),
            "source_media_files": sorted(source_media_files)}
