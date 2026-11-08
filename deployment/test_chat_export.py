import json
import tempfile
import unittest
from pathlib import Path

import chat_export
from fixture_export import CONVERSATION, add_media, build_export, text


class ChatExportReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "export"

    def test_window_dedupe_last_line_wins_and_partial_tail_is_ignored(self):
        rows = [text("m1", 100, "u1", "第一版"), text("m2", 200, "u2", "窗口外"), text("m1", 100, "u1", "编辑后"),
                text("m3", 150, "u2", "中间")]
        build_export(self.root, rows, partial_tail='{"id": "m4", "ts": 160, "sender_id": "u1"')
        _conversation, messages = chat_export.read_messages(self.root, CONVERSATION, 100, 200)
        self.assertEqual([m["message_id"] for m in messages], ["m1", "m3"])
        self.assertEqual(messages[0]["text"], "编辑后")
        uid = messages[0]["message_uid"]
        _conversation, again = chat_export.read_messages(self.root, CONVERSATION, 0, 10 ** 10)
        self.assertEqual(uid, again[0]["message_uid"])

    def test_names_come_from_sender_name_then_members_and_system_has_no_author(self):
        rows = [{"id": "a", "ts": 1, "sender_id": "u1", "type": "text", "text": "hi"},
                {"id": "b", "ts": 2, "sender_id": "system", "type": "system", "text": "有人加入"},
                {"id": "c", "ts": 3, "sender_id": "u2", "type": "revoke"}]
        build_export(self.root, rows, members=[{"id": "u1", "name": "群昵称甲"}])
        _c, messages = chat_export.read_messages(self.root, CONVERSATION, 0, 10)
        self.assertEqual(messages[0]["author"], "群昵称甲")
        self.assertEqual((messages[1]["author_id"], messages[1]["author"], messages[1]["type"]), ("", "系统", "system"))
        self.assertEqual(messages[2]["type"], "recall")

    def test_readable_text_never_invents_content(self):
        rows = [{"id": "i", "ts": 1, "sender_id": "u", "type": "image"},
                {"id": "l", "ts": 2, "sender_id": "u", "type": "link",
                 "link": {"url": "https://example.invalid/a", "title": "标题", "description": "摘要"}},
                {"id": "f", "ts": 3, "sender_id": "u", "type": "forward",
                 "forward": [{"sender_name": "乙", "ts": 1, "type": "text", "text": "原话"}, {"sender_name": "丙", "type": "image"}]},
                {"id": "q", "ts": 4, "sender_id": "u", "type": "quote", "text": "回复正文",
                 "reply_to": {"id": "l", "sender_name": "甲", "text": "被引用"}}]
        build_export(self.root, rows)
        _c, messages = chat_export.read_messages(self.root, CONVERSATION, 0, 10)
        by_id = {m["message_id"]: m for m in messages}
        self.assertEqual(by_id["i"]["text"], "[图片]")
        self.assertEqual(by_id["l"]["text"], "标题\n摘要\nhttps://example.invalid/a")
        self.assertEqual(by_id["f"]["text"], "乙：原话\n丙：[图片]")
        self.assertEqual((by_id["q"]["type"], by_id["q"]["text"], by_id["q"]["reply_to"]["id"]), ("text", "回复正文", "l"))

    def test_malformed_and_unsafe_exports_stop(self):
        build_export(self.root, [text("m1", 1, "u", "x")])
        manifest = json.loads((self.root / "manifest.json").read_text())
        cases = []
        bad_line = self.root / "conversations" / f"{CONVERSATION}.jsonl"
        bad_line.write_text('{"id": "m1", "ts": "1", "sender_id": "u", "type": "text"}\n')
        cases.append("ts")
        with self.assertRaises(chat_export.ExportError):
            chat_export.read_messages(self.root, CONVERSATION, 0, 10)
        bad_line.write_text("not json\n")
        with self.assertRaises(chat_export.ExportError):
            chat_export.read_messages(self.root, CONVERSATION, 0, 10)
        for unsafe in ("../outside.jsonl", "/etc/passwd", "state/cursor.jsonl"):
            manifest["conversations"][0]["file"] = unsafe
            (self.root / "manifest.json").write_text(json.dumps(manifest))
            with self.subTest(path=unsafe), self.assertRaises(chat_export.ExportError):
                chat_export.read_messages(self.root, CONVERSATION, 0, 10)
        (self.root / "manifest.json").write_bytes(b"\xef\xbb\xbf{}")
        with self.assertRaises(chat_export.ExportError):
            chat_export.load_manifest(self.root)
        manifest["schema"] = "chat-export/v2"
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(chat_export.ExportError):
            chat_export.load_manifest(self.root)

    def test_conversation_ids_are_restricted(self):
        build_export(self.root, [])
        for value in ("../x", "a/b", "", "."):
            with self.subTest(value=value), self.assertRaises(chat_export.ExportError):
                chat_export.find_conversation(chat_export.load_manifest(self.root), value)

    def test_media_is_verified_and_thumbnails_or_pending_are_not_files(self):
        self.root.mkdir(parents=True)
        media = add_media(self.root, b"\x89PNG\r\n\x1a\nfixture")
        ready = {"kind": "image", "status": "ready", **media}
        self.assertEqual(chat_export.media_file(self.root, ready).name, Path(media["path"]).name)
        self.assertIsNone(chat_export.media_file(self.root, {**ready, "status": "pending"}))
        (self.root / media["path"]).write_bytes(b"tampered")
        with self.assertRaises(chat_export.ExportError):
            chat_export.media_file(self.root, ready)

    def test_fingerprint_changes_when_messages_are_appended(self):
        build_export(self.root, [text("m1", 1, "u", "x")])
        before = chat_export.fingerprint(self.root, CONVERSATION)
        with (self.root / "conversations" / f"{CONVERSATION}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(text("m2", 2, "u", "y")) + "\n")
        self.assertNotEqual(before, chat_export.fingerprint(self.root, CONVERSATION))


if __name__ == "__main__":
    unittest.main()
