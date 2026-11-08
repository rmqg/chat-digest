"""chat-digest components that consume a chat-export/v1 directory (all data is fictional)."""
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import fixture_export as fx
import report_avatars
import source_media
import summary_commands
import web_context

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


digest = load("digest_v2_consumer", "digest.py")
prepare = load("prepare_images_v2", "prepare-images.py")


def png(color=(200, 30, 30), size=(8, 6)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


class ExportFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "export"
        self.project = {"group": {"conversation_id": fx.CONVERSATION, "name": "虚构测试群"},
                        "bot": {"names": ["小助手"], "sender_ids": ["u_bot"]}, "timezone": "Asia/Shanghai",
                        "daily_hour": 5, "report_prefix": "chat-digest", "worker_uid": 1000, "worker_gid": 1000}
        patcher = patch("settings.load_project", return_value=self.project)
        patcher.start()
        self.addCleanup(patcher.stop)


class SummaryCommandScanTests(ExportFixture):
    def test_candidate_rules(self):
        names = ["小助手"]
        self.assertTrue(summary_commands.is_candidate("@小助手 总结一下", bot_names=names))
        self.assertTrue(summary_commands.is_candidate("总结最近3小时", bot_names=names, mentions=["u_bot"], bot_sender_ids=["u_bot"]))
        self.assertFalse(summary_commands.is_candidate("@小助手 你好", bot_names=names))
        self.assertFalse(summary_commands.is_candidate("小助手总结一下", bot_names=names))
        self.assertFalse(summary_commands.is_candidate("@别人 总结", bot_names=names))

    def test_scan_finds_new_requests_once_and_skips_self_bot_and_non_text(self):
        rows = [fx.text("a", 100, "u1", "@小助手 总结"), fx.text("b", 100, "u_bot", "@小助手 总结"),
                fx.text("c", 101, "me", "@小助手 总结", is_self=True),
                {"id": "d", "ts": 102, "sender_id": "u2", "type": "image", "text": "@小助手 总结"},
                fx.text("e", 50, "u1", "@小助手 总结")]
        fx.build_export(self.root, rows)
        cursor = self.base / "cursor.json"
        first = summary_commands.scan(self.root, ["小助手"], 60, cursor)
        self.assertEqual([item["message_id"] for item in first["commands"]], ["a"])
        self.assertEqual(first["commands"][0]["request_id"],
                         hashlib.sha256((fx.CONVERSATION + ":a").encode()).hexdigest())
        cursor.write_text(json.dumps(first["cursor"]))
        self.assertEqual(summary_commands.scan(self.root, ["小助手"], 60, cursor)["commands"], [])
        with (self.root / "conversations" / f"{fx.CONVERSATION}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(fx.text("f", 102, "u3", "@小助手 汇总今天"), ensure_ascii=False) + "\n")
        third = summary_commands.scan(self.root, ["小助手"], 60, cursor)
        self.assertEqual([item["message_id"] for item in third["commands"]], ["f"])

    def test_bot_names_must_be_configured(self):
        fx.build_export(self.root, [])
        with self.assertRaises(ValueError):
            summary_commands.scan(self.root, ["陌生名字"], 0, self.base / "cursor.json")


class PrepareImagesTests(ExportFixture):
    def test_ready_original_thumbnail_pending_and_missing(self):
        self.root.mkdir(parents=True)
        original = fx.add_media(self.root, png())
        thumb = fx.add_media(self.root, png((0, 0, 200), (2, 2)))
        rows = [{"id": "ok", "ts": 100, "sender_id": "u", "type": "image", "media": [{"kind": "image", "status": "ready", **original}]},
                {"id": "th", "ts": 101, "sender_id": "u", "type": "image",
                 "media": [{"kind": "image", "status": "ready", "is_thumbnail": True, **thumb}]},
                {"id": "pe", "ts": 102, "sender_id": "u", "type": "image", "media": [{"kind": "image", "status": "pending", "path": "media/x/y.png"}]},
                {"id": "old", "ts": 10, "sender_id": "u", "type": "image", "media": [{"kind": "image", "status": "pending", "path": "media/x/z.png"}]},
                {"id": "mi", "ts": 103, "sender_id": "u", "type": "image", "media": [{"kind": "image", "status": "missing"}]},
                fx.text("tx", 104, "u", "不是图片")]
        fx.build_export(self.root, rows)
        runtime = self.base / "work"
        runtime.mkdir()
        manifest = prepare.prepare(self.root, fx.CONVERSATION, 0, 1000, runtime, pending_grace_seconds=500, now=600)
        by_uid = {item["message_uid"]: item for item in manifest}
        _c, messages = digest.chat_export.read_messages(self.root, fx.CONVERSATION, 0, 1000)
        quality = {m["message_id"]: by_uid[m["message_uid"]]["source_quality"] for m in messages if m["type"] == "image"}
        self.assertEqual(quality, {"old": "missing", "ok": "original", "th": "missing", "pe": "pending", "mi": "missing"})
        ok = next(item for item in manifest if item["source_quality"] == "original")
        self.assertTrue(ok["available"])
        self.assertEqual(hashlib.sha256(Path(ok["image_path"]).read_bytes()).hexdigest(), ok["sha256"])
        self.assertTrue(all(item.get("terminal") for item in manifest if item["source_quality"] == "missing"))
        cache = json.loads((runtime / "prepared-image-cache.json").read_text())
        self.assertIn(ok["message_uid"], cache)


class AvatarTests(ExportFixture):
    def test_only_exported_avatar_files_are_used(self):
        self.root.mkdir(parents=True)
        avatar = fx.add_media(self.root, png((10, 200, 10)))
        fx.build_export(self.root, [], members=[{"id": "u1", "name": "甲", "avatar": avatar["path"]},
                                                {"id": "u2", "name": "乙", "avatar": "../escape.png"}])
        with patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
            result = report_avatars.collect_avatars(self.root, fx.CONVERSATION, ["u1", "u2", "u3"])
        self.assertTrue(result["u1"]["available"])
        self.assertTrue(result["u1"]["data_uri"].startswith("data:image/png;base64,"))
        self.assertFalse(result["u2"]["available"])
        self.assertEqual(result["u3"]["reason"], "avatar_not_exported")


class SourceMediaTests(unittest.TestCase):
    def test_reply_and_forward_image_signals(self):
        messages = [{"message_uid": "U1", "message_id": "i", "type": "image"},
                    {"message_uid": "U2", "message_id": "q", "type": "text", "reply_to": {"id": "i"}},
                    {"message_uid": "U3", "message_id": "f", "type": "forward", "forward": [{"type": "image"}]},
                    {"message_uid": "U4", "message_id": "c1", "type": "text", "reply_to": {"id": "c2"}},
                    {"message_uid": "U5", "message_id": "c2", "type": "text", "reply_to": {"id": "c1"}}]
        self.assertEqual(source_media.source_image_uids(messages, {}),
                         {"U1": ["U1"], "U2": ["U1"], "U3": [], "U4": [], "U5": []})
        self.assertEqual(source_media.source_image_requests(messages),
                         {"U1": True, "U2": True, "U3": True, "U4": False, "U5": False})


class WebLinkTests(unittest.TestCase):
    def test_link_url_and_text_urls_are_collected(self):
        message = {"text": "看看 https://example.org/a。", "link": {"url": "https://example.org/b"}}
        self.assertEqual(web_context._urls(message), ["https://example.org/a", "https://example.org/b"])


class DigestExportTests(ExportFixture):
    def test_conversation_selection_requires_unique_exact_name(self):
        conversations = [{"conversation_id": "a", "name": "同名"}, {"conversation_id": "b", "name": "同名"}]
        with self.assertRaises(ValueError):
            digest.select_conversation(conversations, "同名")
        self.assertEqual(digest.select_conversation(conversations, "同名", "b")["conversation_id"], "b")
        with self.assertRaises(ValueError):
            digest.select_conversation(conversations, None, "c")

    def test_only_self_sent_deliveries_are_excluded(self):
        notice = "网站入口说明"
        image_hash = "c" * 64
        own_text = {"is_self": True, "type": "text", "text": notice}
        self.assertTrue(digest._is_own_report(own_text, {hashlib.sha256(notice.encode()).hexdigest()}, set(), set()))
        self.assertFalse(digest._is_own_report({**own_text, "is_self": False}, {hashlib.sha256(notice.encode()).hexdigest()}, set(), set()))
        own_image = {"is_self": True, "type": "image", "media": [{"sha256": image_hash}]}
        self.assertTrue(digest._is_own_report(own_image, set(), {image_hash}, set()))
        self.assertTrue(digest._is_own_report({"is_self": True, "type": "image", "message_id": "m9"}, set(), set(), {"m9"}))
        self.assertFalse(digest._is_own_report({"is_self": True, "type": "text", "text": "普通发言"}, set(), {image_hash}, set()))

    def test_refused_and_missing_images_are_marked_other_gaps_stop(self):
        messages = [{"message_uid": "r", "type": "image"}, {"message_uid": "m", "type": "image"}]
        understanding = {"r": {"status": "failed", "source_quality": "original",
                               "attempts": [{"category": "refusal", "retryable": False}]},
                         "m": {"status": "unavailable", "source_quality": "missing"}}
        digest._attach_image_understanding(messages, understanding)
        self.assertTrue(messages[0]["image_understanding"]["refused"])
        self.assertTrue(messages[1]["image_understanding"]["missing"])
        with self.assertRaises(ValueError):
            digest._attach_image_understanding([{"message_uid": "p", "type": "image"}],
                                               {"p": {"status": "pending", "source_quality": "pending"}})



class LegacyConfigurationTests(unittest.TestCase):
    def test_v1_group_id_configuration_is_rejected_with_migration_hint(self):
        import settings
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "project.json"
            config.write_text(json.dumps({"group": {"id": "legacy-group", "name": "x"}}))
            with patch.dict("os.environ", {"CHAT_DIGEST_CONFIG": str(config)}):
                with self.assertRaisesRegex(ValueError, "MIGRATION"):
                    settings.load_project()

if __name__ == "__main__":
    unittest.main()
