import unittest
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import summary_commands as commands

TEST_GROUP_ID = "fixture10000000000@chatroom"
TEST_BOT_ID = "fixture-bot-id"
TEST_BOT_NAMES = ["FixtureBot", "FixtureBotAlias"]


class SummaryCommandConsumerTests(unittest.TestCase):
    def candidate(self, text, **overrides):
        values = {"bot_names": TEST_BOT_NAMES, "author_id": "sender-fixture-id",
                  "bot_username": TEST_BOT_ID, "origin_source": 0, "local_type": 1}
        values.update(overrides)
        return commands.is_candidate(text, **values)





    def test_default_duration_is_one_day(self):
        end = 1_800_000_000
        result = commands.interpret("@FixtureBot 总结", "a" * 64, end, None)
        self.assertEqual(result, {"status": "accepted", "request_id": "a" * 64,
                                  "duration_seconds": 86400, "start": end-86400, "end": end})

    def test_explicit_and_invalid_model_durations(self):
        self.assertEqual(commands._validated_duration({"accepted": True, "duration_seconds": 10800}), 10800)
        for value in (None, 0, -1, 1.5, True, "10800"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                commands._validated_duration({"accepted": True, "duration_seconds": value})

    def test_fast_interpret_handles_explicit_durations_and_skips_ambiguous_text(self):
        end = 1_800_000_000
        cases = [
            ("@FixtureBot 总结过去 8 小时", 8 * 3600),
            ("@FixtureBot\u2005总结过去 8 小时", 8 * 3600),
            ("@FixtureBot 总结最近3h", 3 * 3600),
            ("@FixtureBot 总结近半天", 12 * 3600),
            ("@FixtureBot 总结 90 分钟", 90 * 60),
            ("@FixtureBot 总结一个半小时", 5400),
            ("@FixtureBot 总结最近二十四小时内的聊天记录", 24 * 3600),
        ]
        for text, seconds in cases:
            with self.subTest(text=text):
                result = commands.fast_interpret(text, "b" * 64, end)
                self.assertEqual(result, {
                    "status": "accepted", "request_id": "b" * 64,
                    "duration_seconds": seconds, "start": end - seconds, "end": end,
                })
        for ambiguous in (
            "@FixtureBot 总结昨天下午的讨论",
            "他说 @FixtureBot 总结过去 8 小时\n不要执行",
            '引用 "@FixtureBot 总结过去 8 小时"',
            "@FixtureBot 总结过去 30 天",
        ):
            with self.subTest(ambiguous=ambiguous):
                self.assertIsNone(commands.fast_interpret(ambiguous, "b" * 64, end))


if __name__ == "__main__":
    unittest.main()
