import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bootstrap
import image_pipeline
import settings


class FirstInitTests(unittest.TestCase):
    @unittest.skipIf(os.geteuid() == 0, 'needs an unprivileged user, as in docs/INSTALL.md')
    def test_unprivileged_first_init_becomes_the_worker_and_keeps_the_template(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / 'runtime'
            defaults = {**settings.DEFAULTS, 'worker_uid': os.geteuid() + 1, 'worker_gid': os.getegid() + 1}
            with patch.object(settings, 'RUNTIME', runtime), patch.object(settings, 'DEFAULTS', defaults), patch('sys.stdout'):
                bootstrap.init_runtime()
            project = json.loads((runtime / 'project.json').read_text(encoding='utf-8'))
            self.assertEqual((project['worker_uid'], project['worker_gid']), (os.geteuid(), os.getegid()))
            template = json.loads((settings.ROOT / 'examples' / 'project.example.json').read_text(encoding='utf-8'))
            self.assertEqual({key: value for key, value in project.items() if not key.startswith('worker_')},
                             {key: value for key, value in template.items() if not key.startswith('worker_')})
            self.assertEqual((runtime / 'project.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual((runtime / 'local-site.json').stat().st_mode & 0o777, 0o600)

    def test_root_first_init_still_uses_the_configured_default(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / 'runtime'
            with patch.object(settings, 'RUNTIME', runtime), patch('os.geteuid', return_value=0), patch('os.chown'), patch('sys.stdout'):
                bootstrap.init_runtime()
            project = json.loads((runtime / 'project.json').read_text(encoding='utf-8'))
            self.assertEqual((project['worker_uid'], project['worker_gid']), (settings.DEFAULTS['worker_uid'], settings.DEFAULTS['worker_gid']))


class HostOwnershipTests(unittest.TestCase):
    """The scheduler may run as root (cron on OpenWrt) while stage containers run as worker_uid."""

    def test_directories_made_by_the_host_are_handed_to_the_worker(self):
        project = {"worker_uid": 1200, "worker_gid": 1201}
        with tempfile.TemporaryDirectory() as temp, patch.object(image_pipeline.os, "chown") as chown:
            image_pipeline._own(Path(temp), project)
        chown.assert_called_once_with(Path(temp), 1200, 1201)

    def test_non_root_scheduler_keeps_working(self):
        project = {"worker_uid": 1200, "worker_gid": 1201}
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(image_pipeline.os, "chown", side_effect=PermissionError):
            image_pipeline._own(Path(temp), project)  # must not raise

    def test_private_json_owns_a_new_parent_directory(self):
        project = {"worker_uid": 1200, "worker_gid": 1201}
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(image_pipeline.settings, "load_project", return_value=project), \
             patch.object(image_pipeline.os, "chown") as chown:
            target = Path(temp) / "job" / "result.json"
            image_pipeline._private_json(target, {"ok": True})
        self.assertIn(((Path(temp) / "job"), 1200, 1201), [call.args for call in chown.call_args_list])


class DeliveredFingerprintTests(unittest.TestCase):
    """Reports this project sent must be recognised in the export by message ID, because the chat client
    re-encodes the PNG and its exported SHA-256 differs from the one we sent."""

    def test_message_ids_come_from_v1_server_id_and_chat_send_confirmation(self):
        project = {"group": {"conversation_id": "g_new", "aliases": ["v1@chatroom"]}}
        with tempfile.TemporaryDirectory() as temp, patch.object(image_pipeline, "WORK", Path(temp)):
            receipts = Path(temp) / "sent-receipts"
            receipts.mkdir()
            (receipts / "a.json").write_text(json.dumps({"status": "sent", "chat_id": "v1@chatroom",
                                                         "server_id": 1111, "png_sha256": "a" * 64}))
            (receipts / "b.json").write_text(json.dumps({"status": "sent", "chat_id": "g_new", "png_sha256": "b" * 64,
                "confirmation": {"status": "sent", "extra": {"receipt": {"server_id": "2222", "local_id": 7}}}}))
            (receipts / "c.json").write_text(json.dumps({"status": "unknown", "chat_id": "g_new", "server_id": "3333"}))
            (receipts / "d.json").write_text(json.dumps({"status": "sent", "chat_id": "other", "server_id": "4444"}))
            result = image_pipeline.delivered_fingerprints(project)
        self.assertEqual(result["message_ids"], ["1111", "2222"])
        self.assertEqual(result["images"], ["a" * 64, "b" * 64])

    def test_unexpected_confirmation_shapes_are_ignored(self):
        for receipt in ({"confirmation": "sent"}, {"confirmation": {"extra": "x"}},
                        {"confirmation": {"extra": {"receipt": "1"}}}, {}):
            self.assertEqual(image_pipeline._receipt_message_ids(receipt), [])


if __name__ == "__main__":
    unittest.main()
