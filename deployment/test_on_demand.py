import importlib.util
import json
import hashlib
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
from types import SimpleNamespace

import fixture_chat_send as fcs


MODULE_PATH = Path(__file__).with_name("on-demand.py")
SPEC = importlib.util.spec_from_file_location("on_demand", MODULE_PATH)
on_demand = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(on_demand)


TEST_GROUP_ID = "g_fixture_on_demand"
TEST_GROUP_NAME = "Synthetic On-Demand Fixture"
TEST_BOT_ID = "fixture-bot-id"
TEST_BOT_NAME = "FixtureBot"
TEST_PREFIX = "fixture-report"

class OnDemandQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.jobs = self.root / "jobs"
        self.work = self.root / "summary-commands"
        project = {"group": {"conversation_id": TEST_GROUP_ID, "name": TEST_GROUP_NAME},
                   "bot": {"names": [TEST_BOT_NAME], "sender_ids": [TEST_BOT_ID]}, "send": fcs.CONFIG,
                   "report_prefix": TEST_PREFIX, "timezone": "Asia/Shanghai", "daily_hour": 5,
                   "worker_uid": 1234, "worker_gid": 2345}
        for target, attribute, value in (
                (on_demand.settings, "load_project", lambda *args, **kwargs: project),
                (on_demand, "CHAT_ID", TEST_GROUP_ID), (on_demand, "REPORT_PREFIX", TEST_PREFIX),
                (on_demand, "JOBS", self.jobs), (on_demand, "WORK", self.work)):
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)
        for name, value in [('RECEIPTS', self.root / 'receipts'), ('DELIVERY_LOCK', self.root / 'delivery.lock'), ('RUNTIME', self.root / 'runtime')]:
            patcher = patch.object(on_demand, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def prepared_job(self):
        rid = 'b' * 64
        reports = self.root / 'runtime/reports'
        reports.mkdir(parents=True)
        editions = {}
        for edition in ('brief', 'review'):
            base = reports / (TEST_PREFIX + '-request-' + rid + '-' + edition)
            png = base.with_suffix('.png')
            png.write_bytes((edition + ' PNG fixture').encode())
            html = base.with_suffix('.html')
            html.write_text('<html>' + edition + '</html>')
            metadata = base.with_suffix('.json')
            metadata.write_text(json.dumps({'edition': edition, 'chat_username': on_demand.CHAT_ID,
                'request': {'kind': 'on_demand', 'id': rid, 'trigger_timestamp': 10000},
                'window': {'start_unix': 6400, 'end_unix_exclusive': 10000}}))
            editions[edition] = {'status': 'prepared', 'html': str(html), 'png': str(png), 'json': str(metadata),
                                 'png_sha256': hashlib.sha256(png.read_bytes()).hexdigest()}
        job = {'request_id': rid, 'status': 'prepared', 'candidate': {'trigger_timestamp': 10000},
               'intent': {'status': 'accepted', 'start': 6400, 'end': 10000, 'duration_seconds': 3600},
               'editions': editions, 'report_day': '1970-01-01',
               'window': {'start': 6400, 'end': 10000}}
        path = self.jobs / (rid + '.json')
        on_demand._atomic_json(path, job)
        return path, job

    @staticmethod
    def sent_result(command, png_hash, server_id):
        assert fcs.is_send(command)
        return fcs.result(command, 'sent')

    def test_review_pre_submit_failure_resumes_only_review(self):
        path, job = self.prepared_job()
        send_order = []
        notices = []

        def sender(command, **kwargs):
            edition = Path(fcs.argument(command, '--image')).stem.rsplit('-', 1)[-1]
            send_order.append(edition)
            digest = job['editions'][edition]['png_sha256']
            if edition == 'review' and send_order.count('review') == 1:
                return fcs.result(command, 'failed')
            return self.sent_result(command, digest, 'server-' + edition)

        with patch.object(on_demand.subprocess, 'run', side_effect=sender), patch.object(on_demand, '_retry_notice', side_effect=lambda receipt, rid: notices.append((receipt['edition'], rid))):
            self.assertEqual(on_demand.process_job(path)['status'], 'prepared')
            state = json.loads(path.read_text())
            self.assertEqual(state['editions']['brief']['status'], 'sent')
            self.assertEqual(state['editions']['review']['status'], 'prepared')
            state['next_attempt_at'] = 0
            on_demand._atomic_json(path, state)
            self.assertEqual(on_demand.process_job(path)['status'], 'sent')
        self.assertEqual(send_order, ['brief', 'review', 'review'])
        self.assertEqual(notices, [('brief', job['request_id'])])
        self.assertEqual(json.loads((on_demand.RECEIPTS / (job['request_id'] + '.json')).read_text())['edition'], 'brief')
        self.assertEqual(json.loads((on_demand.RECEIPTS / (job['request_id'] + '-review.json')).read_text())['edition'], 'review')

    def test_unknown_brief_is_not_retried_or_bypassed_by_review(self):
        path, job = self.prepared_job()
        calls = []
        def unknown_sender(command, **kwargs):
            calls.append(fcs.argument(command, '--image'))
            return fcs.result(command, 'unknown')
        with patch.object(on_demand.subprocess, 'run', side_effect=unknown_sender):
            self.assertEqual(on_demand.process_job(path)['status'], 'unknown')
            self.assertEqual(on_demand.process_job(path)['status'], 'unknown')
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith('-brief.png'))

    def test_both_persisted_receipts_recover_before_any_send(self):
        path, job = self.prepared_job()
        job['status'] = 'sending'
        for edition in ('brief', 'review'):
            job['editions'][edition]['status'] = 'sending'
            on_demand._atomic_json(on_demand.RECEIPTS / (job['request_id'] + ('-review' if edition == 'review' else '') + '.json'), {
                'status': 'sent', 'edition': edition, 'chat_id': on_demand.CHAT_ID,
                'request_id': job['request_id'], 'png_sha256': job['editions'][edition]['png_sha256'],
                'send_request_id': 'cd-server-' + edition, 'sent_at': 11000, 'report_day': job['report_day']})
        on_demand._atomic_json(path, job)
        notices = []
        with patch.object(on_demand.subprocess, 'run', side_effect=AssertionError('both persisted receipts prohibit resend')), patch.object(on_demand, '_retry_notice', side_effect=lambda receipt, rid: notices.append((receipt['edition'], rid))):
            self.assertEqual(on_demand.process_job(path)['status'], 'sent')
        self.assertEqual(json.loads(path.read_text())['status'], 'sent')
        self.assertEqual(notices, [('brief', job['request_id'])])

    def test_legacy_root_receipt_remains_terminal(self):
        path, job = self.prepared_job()
        job['status'] = 'sending'
        on_demand._atomic_json(path, job)
        on_demand._atomic_json(on_demand.RECEIPTS / (job['request_id'] + '.json'), {
            'status': 'sent', 'chat_id': on_demand.CHAT_ID, 'request_id': job['request_id'],
            'png_sha256': 'old-fingerprint', 'send_request_id': 'cd-legacy-server', 'sent_at': 11000})
        with patch.object(on_demand.subprocess, 'run', side_effect=AssertionError('legacy sent receipt is terminal')):
            result = on_demand.process_job(path)
        self.assertTrue(result['already_terminal'])
        self.assertEqual(json.loads(path.read_text())['status'], 'sent')

    def test_brief_receipt_without_job_editions_recovers_and_sends_review_only(self):
        path, job = self.prepared_job()
        brief_hash = job['editions']['brief']['png_sha256']
        review_hash = job['editions']['review']['png_sha256']
        job.pop('editions')
        job['status'] = 'sending'
        on_demand._atomic_json(path, job)
        on_demand._atomic_json(on_demand.RECEIPTS / (job['request_id'] + '.json'), {
            'status': 'sent', 'edition': 'brief', 'chat_id': on_demand.CHAT_ID,
            'request_id': job['request_id'], 'png_sha256': brief_hash,
            'send_request_id': 'cd-brief-server', 'sent_at': 11000, 'report_day': job['report_day']})
        sends = []
        notices = []
        def sender(command, **kwargs):
            sends.append(fcs.argument(command, '--image'))
            return self.sent_result(command, review_hash, 'review-server')
        with patch.object(on_demand.subprocess, 'run', side_effect=sender), patch.object(on_demand, '_retry_notice', side_effect=lambda receipt, rid: notices.append(receipt['edition'])):
            self.assertEqual(on_demand.process_job(path)['status'], 'sent')
        self.assertEqual(len(sends), 1)
        self.assertTrue(sends[0].endswith('-review.png'))
        self.assertEqual(notices, ['brief'])

    def test_legacy_sending_job_without_bundle_becomes_unknown(self):
        path, job = self.prepared_job()
        job.pop('editions')
        job['status'] = 'sending'
        on_demand._atomic_json(path, job)
        with patch.object(on_demand.subprocess, 'run', side_effect=AssertionError('ambiguous legacy send must not generate or send')):
            self.assertEqual(on_demand.process_job(path)['status'], 'unknown')
        self.assertEqual(json.loads(path.read_text())['status'], 'unknown')
    def test_brief_receipt_does_not_complete_request_before_review(self):
        path, job = self.prepared_job()
        job['editions']['brief']['status'] = 'sending'
        on_demand._atomic_json(path, job)
        on_demand._atomic_json(on_demand.RECEIPTS / (job['request_id'] + '.json'), {
            'status': 'sent', 'edition': 'brief', 'chat_id': on_demand.CHAT_ID,
            'request_id': job['request_id'], 'png_sha256': job['editions']['brief']['png_sha256'],
            'send_request_id': 'cd-brief-server', 'sent_at': 11000, 'report_day': job['report_day']})
        sends = []
        def sender(command, **kwargs):
            sends.append(fcs.argument(command, '--image'))
            return self.sent_result(command, job['editions']['review']['png_sha256'], 'review-server')
        notices = []
        with patch.object(on_demand.subprocess, 'run', side_effect=sender), patch.object(on_demand, '_retry_notice', side_effect=lambda receipt, rid: notices.append(receipt['edition'])):
            self.assertEqual(on_demand.process_job(path)['status'], 'sent')
        self.assertEqual(len(sends), 1)
        self.assertTrue(sends[0].endswith('-review.png'))
        self.assertEqual(notices, ['brief'])

    def test_pending_notice_requires_both_receipts_and_uses_brief(self):
        rid = 'c' * 64
        calls = []
        fake_notice = SimpleNamespace(RECEIPTS=self.root / 'site-notices')
        review_path = on_demand.RECEIPTS / (rid + '-review.json')
        brief_path = on_demand.RECEIPTS / (rid + '.json')
        on_demand._atomic_json(review_path, {'status': 'sent', 'edition': 'review', 'chat_id': on_demand.CHAT_ID,
                                             'request_id': rid, 'send_request_id': 'cd-review-server'})
        with patch.dict(sys.modules, {'site_access_notice': fake_notice}), patch.object(on_demand, '_retry_notice', side_effect=lambda receipt, request_id: calls.append((receipt['edition'], request_id))):
            on_demand.process_pending_notices()
            on_demand._atomic_json(brief_path, {'status': 'sent', 'edition': 'brief', 'chat_id': on_demand.CHAT_ID,
                                                'request_id': rid, 'send_request_id': 'cd-brief-server', 'report_day': '1970-01-01'})
            on_demand.process_pending_notices()
            self.assertEqual(calls, [])
            review = json.loads(review_path.read_text())
            review['report_day'] = '1970-01-01'
            on_demand._atomic_json(review_path, review)
            on_demand.process_pending_notices()
            self.assertEqual(calls, [('brief', rid)])
            _atomic_notice = fake_notice.RECEIPTS / (rid + '.json')
            on_demand._atomic_json(_atomic_notice, {'status': 'sent'})
            on_demand.process_pending_notices()

    def test_jobs_are_durable_before_cursor_and_duplicate_scans_are_idempotent(self):
        request_id = "a" * 64
        item = {"request_id": request_id, "message_uid": "u", "message_id": "s",
                "trigger_timestamp": 1000, "author_id": "author", "command_text": "总结"}
        scan = {"commands": [item], "cursor": {"conversation_id": TEST_GROUP_ID, "last_ts": 1000, "ids_at_last_ts": ["s"]}}
        on_demand._save_cursor_and_jobs(scan)
        first = json.loads((self.jobs / f"{request_id}.json").read_text())
        on_demand._save_cursor_and_jobs(scan)
        second = json.loads((self.jobs / f"{request_id}.json").read_text())
        self.assertEqual(first, second)
        self.assertEqual(json.loads((self.work / "cursor.json").read_text()), scan["cursor"])

    def test_invalid_request_id_does_not_advance_cursor(self):
        with self.assertRaises(ValueError):
            on_demand._save_cursor_and_jobs({"commands": [{"request_id": "bad"}], "cursor": {"x": 1}})
        self.assertFalse((self.work / "cursor.json").exists())

    def test_config_requires_enabled_from_even_for_one_shot(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            patcher = patch.object(on_demand, "CONFIG", path)
            patcher.start()
            self.addCleanup(patcher.stop)
            path.write_text(json.dumps({"chat_id": TEST_GROUP_ID,
                                        "bot_names": [TEST_BOT_NAME]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                on_demand._config()


if __name__ == "__main__":
    unittest.main()
