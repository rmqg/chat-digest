#!/usr/bin/env python3
"""Shared, serialized image and evidence stage for daily reports and live preprocessing."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

import settings
from model_fallback import is_refused_image

ROOT = settings.ROOT
WORK = settings.RUNTIME / "digest"
STAGE_FAILURE_LOG = WORK / 'stage-failures.jsonl'
STAGE_FAILURE_LOG_MAX_BYTES = 1024 * 1024
REALTIME_INPUT_STATE = WORK / 'realtime-input-state.json'
REALTIME_REUSE_SECONDS = 3600


class StageError(RuntimeError):
    """A stage failed; label is safe to persist in state files (no private paths or URLs)."""

    def __init__(self, script: str, detail: str):
        super().__init__(f'{script} did not finish successfully ({detail})')
        self.label = f'{script}: {detail}'


def _record_stage_failure(job: Path, scope: str, script: str, returncode, stdout, stderr) -> None:
    """Realtime job directories are deleted after 10 minutes, so also keep a
    root-only log; otherwise the real reason for a stage failure is gone before anyone looks."""
    def tail(value, limit):
        if isinstance(value, bytes):
            value = value.decode('utf-8', 'replace')
        return value[-limit:] if isinstance(value, str) else ''
    entry = {'at': int(time.time()), 'scope': scope, 'job': job.name, 'script': script, 'returncode': returncode,
             'stdout_tail': tail(stdout, 2000), 'stderr_tail': tail(stderr, 4000)}
    _private_json(job / (script + '.failure.json'), entry)
    try:
        if STAGE_FAILURE_LOG.exists() and STAGE_FAILURE_LOG.stat().st_size > STAGE_FAILURE_LOG_MAX_BYTES:
            os.replace(STAGE_FAILURE_LOG, STAGE_FAILURE_LOG.with_suffix('.jsonl.1'))
        fd = os.open(STAGE_FAILURE_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError:
        pass


def _stage_finished(script: str, returncode: int, stdout: str) -> bool:
    """0 and 2 mean finished. caption-images.py also exits 1 when it ran to the end and only some
    rows ended as failed (for example a provider refusal): its structured result is still valid.
    A crash exits 1 with no result line, and that stays a failure."""
    if returncode in (0, 2):
        return True
    if script == 'caption-images.py' and returncode == 1:
        try:
            info = _last_object(stdout or '')
        except RuntimeError:
            return False
        return isinstance(info.get('images'), int) and isinstance(info.get('failed'), int) and info['failed'] > 0
    return False


def _split_understood(manifest: list, understood: dict) -> tuple[list, set]:
    """(items still waiting, uids refused). A provider refusal and an original the export marks as missing are
    terminal, so they no longer hold the whole report back; the report marks the image instead. Every other
    failure keeps waiting and is retried."""
    refused = {item['message_uid'] for item in manifest if is_refused_image(understood.get(item['message_uid']))}
    pending = [item for item in manifest if understood.get(item['message_uid'], {}).get('status') != 'success'
               and item['message_uid'] not in refused and not item.get('terminal')]
    return pending, refused


def _realtime_input_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def _reusable_realtime_result(input_hash: str):
    """Previous complete realtime result when nothing the stages consume has changed."""
    try:
        saved = json.loads(REALTIME_INPUT_STATE.read_text(encoding='utf-8'))
        result = json.loads((WORK / 'realtime-images-result.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if (not isinstance(saved, dict) or not isinstance(result, dict) or saved.get('input_hash') != input_hash
            or result.get('status') != 'complete' or time.time() - float(saved.get('at', 0)) >= REALTIME_REUSE_SECONDS):
        return None
    return result


def _own(path: Path, project: dict | None = None) -> None:
    """Directories the host creates for stage containers belong to the worker user. When the scheduler runs
    as root (cron on OpenWrt), the containers run as worker_uid and must be able to write there."""
    project = project or settings.load_project()
    try:
        os.chown(path, int(project["worker_uid"]), int(project["worker_gid"]))
    except PermissionError:
        pass  # not root: the directory already belongs to the user running the scheduler


def _private_json(path: Path, value) -> None:
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _own(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.image-stage-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.chmod(temporary, 0o600)
        project = settings.load_project()
        try:
            os.chown(temporary, project["worker_uid"], project["worker_gid"])
        except PermissionError:
            pass
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _last_object(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError('Image stage returned no structured result')


def _network(project: dict, name: str) -> str:
    return 'none' if name == 'none' else project['docker_network']


def _run_tool(image: str, script: str, arguments: list[str], *, network: str, timeout: int):
    project = settings.load_project(required=True)
    name = 'chat-digest-stage-' + uuid.uuid4().hex[:12]
    command = ['docker', 'run', '--rm', '--pull', 'never', '--name', name, '--network', _network(project, network),
               '--read-only', '--user', f"{project['worker_uid']}:{project['worker_gid']}", '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
               '--memory', '1g', '--cpus', '1', '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m,mode=1777',
               '--env', 'PYTHONPATH=' + ('/ocr-deps:' if script == 'ocr_images.py' and (settings.RUNTIME / 'ocr-deps').is_dir() else '') + '/source/agent_console:/tool',
               '--env', 'TZ=' + project['timezone'], '--env', 'CHAT_DIGEST_CONFIG=/runtime/project.json']
    for source, target, readonly in [
        (ROOT / 'deployment', '/tool', True), (ROOT / 'agent_console', '/source/agent_console', True),
        (settings.RUNTIME / 'project.json', '/runtime/project.json', True), (WORK, '/work', False),
        (settings.RUNTIME / 'gemini-model.json', '/models/gemini.json', True),
        (settings.RUNTIME / 'vision-qwen.json', '/models/qwen.json', True)]:
        command += ['--mount', f'type=bind,src={source},dst={target}' + (',readonly' if readonly else '')]
    if script in {'prepare-images.py', 'web_context.py'}:
        command += ['--mount', f'type=bind,src={settings.export_root(project)},dst=/export,readonly']
    if script == 'ocr_images.py' and (settings.RUNTIME / 'ocr-deps').is_dir():
        command += ['--mount', f'type=bind,src={settings.RUNTIME / "ocr-deps"},dst=/ocr-deps,readonly']
    command += [image, '/tool/' + script, *arguments]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except BaseException:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15)
        raise
    return result


def _container_path(path: Path) -> str:
    return str(Path('/work') / path.relative_to(WORK))


def process_window(start: int, end: int, *, max_images: int = 0, wait_for_lock: bool = True,
                   scope: str = 'daily') -> dict:
    if type(start) is not int or type(end) is not int or start >= end or max_images < 0:
        raise ValueError('Invalid image processing window or batch size')
    if scope not in {'daily', 'realtime', 'on-demand'}:
        raise ValueError('Invalid image processing scope')
    WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
    _own(WORK)
    with (WORK / 'image-stage.lock').open('a+') as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait_for_lock else fcntl.LOCK_NB))
        except BlockingIOError:
            return {'status': 'busy', 'counts': {}, 'pending_start': start, 'next_attempt_at': int(time.time()) + 15}
        return _process_locked(start, end, max_images, scope)


def _receipt_message_ids(receipt: dict) -> list[str]:
    """Platform message IDs of a confirmed send. Image fingerprints alone are not enough: the chat client
    re-encodes a sent PNG, so the exported copy has another SHA-256. Sources, in order: an explicit
    message_id; a v1.x receipt's server_id; the chat-send/v1 result kept as `confirmation`, whose
    provider-specific extra.receipt.server_id (wechat-send) is the exported message ID."""
    ids = [str(receipt[key]) for key in ('message_id', 'server_id') if receipt.get(key)]
    confirmation = receipt.get('confirmation')
    extra = confirmation.get('extra') if isinstance(confirmation, dict) else None
    platform = extra.get('receipt') if isinstance(extra, dict) else None
    if isinstance(platform, dict) and platform.get('server_id'):
        ids.append(str(platform['server_id']))
    return ids


def delivered_fingerprints(project: dict) -> dict:
    """Fingerprints of what this project already sent into the conversation, so reports exclude themselves."""
    images, texts, message_ids = [], [], []
    conversations = settings.conversation_ids(project)
    for directory in ('sent-receipts', 'on-demand-receipts', 'site-access-receipts'):
        for path in sorted((WORK / directory).glob('*.json')):
            try:
                receipt = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(receipt, dict) or receipt.get('chat_id') not in conversations:
                continue
            if receipt.get('text_sha256'):
                texts.append(receipt['text_sha256'])
            if receipt.get('status') == 'sent':
                if receipt.get('png_sha256'):
                    images.append(receipt['png_sha256'])
                message_ids.extend(_receipt_message_ids(receipt))
    return {'images': sorted(set(images)), 'texts': sorted(set(texts)), 'message_ids': sorted(set(message_ids))}


def _process_locked(start: int, end: int, max_images: int, scope: str) -> dict:
    os.umask(0o077)
    began = time.monotonic()
    now = int(time.time())
    image = settings.runtime_image('runtime')
    project = settings.load_project(required=True)
    jobs = WORK / 'image-jobs'
    jobs.mkdir(mode=0o700, exist_ok=True)
    _own(jobs, project)
    for old in jobs.iterdir():
        age = now - old.stat().st_mtime
        # Nothing reads a job directory after its run ended; keep them only briefly.
        if old.is_dir() and ((old.name.startswith('realtime-') and age > 600)
                             or (old.name.startswith('daily-') and age > 7 * 86400)
                             or (old.name.startswith('on-demand-') and age > 7 * 86400)):
            shutil.rmtree(old)
    job = jobs / f'{scope}-{now}-{uuid.uuid4().hex[:8]}'
    job.mkdir(mode=0o700)
    _own(job, project)
    manifest_path = job / 'image-manifest.json'
    output_path = job / 'image-understanding.json'
    delivered = delivered_fingerprints(project)
    notice_hashes_path = job / 'exclude-own-text-sha256.json'
    image_hashes_path = job / 'exclude-own-image-sha256.json'
    message_ids_path = job / 'exclude-message-ids.json'
    _private_json(notice_hashes_path, delivered['texts'])
    _private_json(image_hashes_path, delivered['images'])
    _private_json(message_ids_path, delivered['message_ids'])
    grace = project.get('image_pending_grace_seconds', 6 * 3600)
    if type(grace) is not int or grace < 0:
        raise ValueError('image_pending_grace_seconds must be a non-negative integer')
    timings = {}

    def timed_tool(script, args, network, timeout):
        stamp = time.monotonic()
        try:
            result = _run_tool(image, script, args, network=network, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            timings[script.removesuffix('.py') + '_seconds'] = round(time.monotonic() - stamp, 3)
            _record_stage_failure(job, scope, script, 'timeout', exc.stdout, exc.stderr)
            raise StageError(script, f'timed out after {timeout}s') from None
        timings[script.removesuffix('.py') + '_seconds'] = round(time.monotonic() - stamp, 3)
        if not _stage_finished(script, result.returncode, result.stdout):
            _record_stage_failure(job, scope, script, result.returncode, result.stdout, result.stderr)
            raise StageError(script, f'exit {result.returncode}')
        return result

    timed_tool('prepare-images.py', ['--export-root', '/export', '--runtime-dir', '/work', '--start', str(start),
               '--end', str(end), '--manifest', _container_path(manifest_path), '--pending-grace-seconds', str(grace),
               '--exclude-message-ids', _container_path(message_ids_path)],
               'none', 240)
    manifest = json.loads(manifest_path.read_text())
    caption_manifest_path = job / 'caption-manifest.json'
    _private_json(caption_manifest_path, manifest)
    ocr_path, ocr_summary_path, web_path, summary_path = (job / name for name in ('ocr.json', 'ocr-summary.json', 'web.json', 'web-summary.json'))
    # Links first: most realtime rounds have no new image or link, and then the remaining stages are reused.
    timed_tool('web_context.py', [
        '--export-root', '/export', '--start', str(start), '--end', str(end),
        '--output', _container_path(web_path), '--max-pages', str(4 if scope == 'realtime' else 0)], 'default', 900)
    input_hash = None
    if scope == 'realtime':
        input_hash = _realtime_input_hash([
            caption_manifest_path, web_path, settings.RUNTIME / 'gemini-model.json', settings.RUNTIME / 'vision-qwen.json',
            *(ROOT / 'deployment' / name for name in ('caption-images.py', 'ocr_images.py', 'ocr_summary.py', 'web_summary.py'))])
        reusable = _reusable_realtime_result(input_hash)
        if reusable is not None:
            shutil.rmtree(job, ignore_errors=True)
            return {**reusable, 'start': start, 'end': end, 'unchanged_since_last_complete': True,
                    'timings': {**timings, 'total_seconds': round(time.monotonic() - began, 3)}}
    # One stage container at a time: small servers run out of memory when three 1 GB stages overlap.
    captioned = timed_tool('caption-images.py', [
        '--manifest', _container_path(caption_manifest_path), '--output', _container_path(output_path),
        '--cache-output', '/work/vision-cache.json', '--failures', '/work/vision-failures.json',
        '--diagnostics-dir', '/work/vision-diagnostics', '--max-images', str(max_images)], 'default', 900)
    timed_tool('ocr_images.py', [
        '--manifest', _container_path(caption_manifest_path), '--output', _container_path(ocr_path),
        '--cache-dir', '/work/ocr-cache', '--max-images', str(max_images)], 'none', 900)
    # A single request may take 600 s; web_summary stops starting new ones at its own budget.
    timed_tool('web_summary.py', ['--input', _container_path(web_path), '--output', _container_path(summary_path),
               '--max-pages', str(2 if scope == 'realtime' else 0), '--max-duration-seconds', '1200'], 'default', 1500)
    timed_tool('ocr_summary.py', ['--input', _container_path(ocr_path), '--output', _container_path(ocr_summary_path),
               '--config', '/models/gemini.json', '--cache-dir', '/work/ocr-summary-cache',
               '--max-items', str(4 if scope == 'realtime' else 0), '--max-duration-seconds', '720'], 'default', 900)
    auxiliary = _build_auxiliary_context(json.loads(ocr_summary_path.read_text()), json.loads(summary_path.read_text()))
    auxiliary_pending = any(entry.get('status') in ('pending', 'failed')
                            for item in auxiliary.values() for entry in item.values())
    auxiliary_path = job / 'auxiliary-context.json'
    _private_json(auxiliary_path, auxiliary)
    info = _last_object(captioned.stdout)
    if not output_path.is_file():
        raise RuntimeError('Image caption worker did not persist its results')
    understood = json.loads(output_path.read_text())
    pending_items, refused_uids = _split_understood(manifest, understood)
    counts = {'total': len(manifest), **{status: sum(row.get('status') == status for row in understood.values())
                                      for status in ('success', 'failed', 'pending', 'unavailable')},
              'refused': len(refused_uids), 'missing': sum(item.get('source_quality') == 'missing' for item in manifest),
              'export_pending': sum(item.get('source_quality') == 'pending' for item in manifest)}
    future = [float(info['next_attempt_at'])] if isinstance(info.get('next_attempt_at'), (int, float)) and info['next_attempt_at'] > time.time() else []
    for item in pending_items:
        uid = item['message_uid']
        if item.get('source_quality') == 'pending':
            future.append(time.time() + 600)  # waiting for the exporter to download the original
        elif understood.get(uid, {}).get('status') == 'pending' and not future:
            future.append(time.time() + 1)
        elif understood.get(uid, {}).get('status') in ('failed', 'unavailable') and not future:
            future.append(time.time() + 600)
    for item in auxiliary.values():
        for entry in item.values():
            retry_at = entry.get('retry_at') if isinstance(entry, dict) else None
            if isinstance(retry_at, (int, float)) and retry_at > time.time():
                future.append(float(retry_at))
            # Web entries keep retry_at per page; a pending page without one is due soon.
            pages = entry.get('pages') if isinstance(entry, dict) else None
            for page in pages if isinstance(pages, list) else []:
                if isinstance(page, dict) and page.get('status') == 'pending':
                    page_retry = page.get('retry_at')
                    future.append(float(page_retry) if isinstance(page_retry, (int, float)) and page_retry > time.time()
                                  else time.time() + 60)
    auxiliary_pending_stamps = [item['timestamp'] for item in manifest
                                if item['message_uid'] in auxiliary and any(
                                    entry.get('status') in ('pending', 'failed')
                                    for entry in auxiliary[item['message_uid']].values())]
    pending_start = min(auxiliary_pending_stamps, default=None)
    if pending_start is None and auxiliary_pending:
        pending_start = start
    if pending_items:
        pending_timestamps = [item['timestamp'] for item in pending_items]
        if pending_start is not None:
            pending_timestamps.append(pending_start)
        pending_start = min(pending_timestamps)
    status = 'complete' if not pending_items and not auxiliary_pending else 'pending'
    result = {'status': status, 'start': start, 'end': end, 'scope': scope, 'counts': counts,
              'manifest_path': str(manifest_path), 'understanding_path': str(output_path),
              'auxiliary_path': str(auxiliary_path), 'auxiliary_pending': auxiliary_pending,
              'notice_hashes_path': str(notice_hashes_path), 'image_hashes_path': str(image_hashes_path),
              'message_ids_path': str(message_ids_path), 'pending_start': pending_start,
              'next_attempt_at': int(min(future)) if future else (int(time.time()) + 60 if pending_items or auxiliary_pending else None),
              'timings': {**timings, 'total_seconds': round(time.monotonic() - began, 3)}}
    _private_json(job / 'result.json', result)
    _private_json(WORK / f'{scope}-images-result.json', result)
    if input_hash is not None:
        _private_json(REALTIME_INPUT_STATE, {'input_hash': input_hash if status == 'complete' else None, 'at': int(time.time())})
    return result


def _build_auxiliary_context(ocr_summaries: dict, web_summaries: dict) -> dict:
    """Combine model-facing evidence, admitting summaries but never raw OCR payloads."""
    auxiliary = {}
    safe_ocr_fields = {'status', 'source_kind', 'image_sha256', 'summary', 'low_confidence', 'confidence',
                       'uncertainties', 'engine', 'engine_version', 'model', 'configured_model', 'fallback_used', 'input_sha256',
                       'input_text_sha256', 'input_characters', 'summarized_at', 'reason', 'retry_at',
                       'selected', 'selection_strategy', 'selection_windows', 'original_characters',
                       'submitted_characters'}
    for uid, entry in ocr_summaries.items():
        if not isinstance(entry, dict):
            continue
        safe = {key: value for key, value in entry.items() if key in safe_ocr_fields}
        auxiliary.setdefault(uid, {})['ocr'] = safe
    for uid, entry in web_summaries.items():
        auxiliary.setdefault(uid, {})['web'] = entry
    return auxiliary




def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=int, required=True)
    parser.add_argument('--end', type=int, required=True)
    parser.add_argument('--scope', choices=('daily', 'realtime', 'on-demand'), default='daily')
    parser.add_argument('--max-images', type=int, default=0)
    parser.add_argument('--no-wait', action='store_true')
    args = parser.parse_args()
    try:
        result = process_window(args.start, args.end, max_images=args.max_images, wait_for_lock=not args.no_wait, scope=args.scope)
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__, 'stage': getattr(exc, 'label', None)}), flush=True)
        return 1
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
