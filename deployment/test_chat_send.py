import json
import subprocess
import unittest
from pathlib import Path

import chat_send
import fixture_chat_send as fcs


def runner_returning(returncode, body, calls=None):
    def run(command, **kwargs):
        if calls is not None:
            calls.append(command)
        text = body if isinstance(body, str) else json.dumps({**body, "request_id": fcs.argument(command, "--request-id")})
        return subprocess.CompletedProcess(command, returncode, text, "")
    return run


class ChatSendContractTests(unittest.TestCase):
    def test_config_is_validated(self):
        self.assertEqual(chat_send.send_config({"send": fcs.CONFIG})["command"], [fcs.COMMAND])
        for bad in ({}, {"send": {"command": "wechat-send", "to": "x"}}, {"send": {"command": [], "to": "x"}},
                    {"send": {"command": ["x"], "to": ""}}, {"send": {"command": ["x"], "to": "y", "timeout": 5}},
                    {"send": {"command": ["REPLACE_WITH_SENDER"], "to": "y"}}):
            with self.subTest(bad=bad), self.assertRaises(chat_send.SendConfigError):
                chat_send.send_config(bad)

    def test_command_line_follows_the_contract(self):
        calls = []
        result = chat_send.send(fcs.CONFIG, rid="cd-0123456789", image=Path("/reports/x.png"),
                                runner=lambda command, **kw: calls.append(command) or fcs.result(command, "sent"))
        self.assertEqual(result["status"], "sent")
        command = calls[0]
        self.assertEqual(command[:3], [fcs.COMMAND, "--to", "Synthetic target"])
        self.assertEqual(fcs.argument(command, "--image"), "/reports/x.png")
        self.assertEqual(fcs.argument(command, "--request-id"), "cd-0123456789")
        self.assertIn("--json", command)
        self.assertNotIn("--dry-run", command)

    def test_each_status_is_classified(self):
        for status in ("sent", "failed", "unknown", "invalid"):
            with self.subTest(status=status):
                result = chat_send.send(fcs.CONFIG, rid="cd-0123456789", text="hello",
                                        runner=lambda command, **kw: fcs.result(command, status))
                self.assertEqual(result["status"], status)
                self.assertEqual(result.get("before_submit") is True, status in {"failed", "invalid"})

    def test_anything_ambiguous_is_unknown_never_failed(self):
        good = {"schema": "chat-send/v1", "status": "sent"}
        cases = [(0, "not json"), (0, ""), (0, {**good, "schema": "other"}), (1, good), (0, {**good, "status": "failed"}),
                 (7, {**good, "status": "failed"})]
        for returncode, body in cases:
            with self.subTest(returncode=returncode, body=body):
                result = chat_send.send(fcs.CONFIG, rid="cd-0123456789", text="x", runner=runner_returning(returncode, body))
                self.assertEqual(result["status"], "unknown")
        wrong_id = lambda command, **kw: subprocess.CompletedProcess(command, 0, json.dumps({**good, "request_id": "cd-other00000"}), "")
        self.assertEqual(chat_send.send(fcs.CONFIG, rid="cd-0123456789", text="x", runner=wrong_id)["status"], "unknown")

    def test_timeout_is_unknown_and_a_missing_command_is_a_safe_failure(self):
        def timeout(command, **kw):
            raise subprocess.TimeoutExpired(command, 1)
        self.assertEqual(chat_send.send(fcs.CONFIG, rid="cd-0123456789", text="x", runner=timeout)["status"], "unknown")
        def missing(command, **kw):
            raise FileNotFoundError(command[0])
        result = chat_send.send(fcs.CONFIG, rid="cd-0123456789", text="x", runner=missing)
        self.assertEqual((result["status"], result["before_submit"]), ("failed", True))

    def test_request_ids_are_stable_and_validated(self):
        self.assertEqual(chat_send.request_id("daily", "g", "2026-01-01", "brief", "a" * 64),
                         chat_send.request_id("daily", "g", "2026-01-01", "brief", "a" * 64))
        self.assertNotEqual(chat_send.request_id("daily", "g", "2026-01-01", "brief", "a" * 64),
                            chat_send.request_id("daily", "g", "2026-01-01", "review", "a" * 64))
        with self.assertRaises(ValueError):
            chat_send.send(fcs.CONFIG, rid="bad id", text="x", runner=runner_returning(0, {}))
        with self.assertRaises(ValueError):
            chat_send.send(fcs.CONFIG, rid="cd-0123456789", text="x", image=Path("y"), runner=runner_returning(0, {}))


if __name__ == "__main__":
    unittest.main()
