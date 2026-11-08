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


if __name__ == "__main__":
    unittest.main()
