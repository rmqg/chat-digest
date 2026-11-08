"""Test helpers: a fake chat-send/v1 command (never sends anything)."""
import json
import subprocess

COMMAND = "fixture-send"
CONFIG = {"command": [COMMAND], "to": "Synthetic target", "timeout": 60}
EXIT = {"sent": 0, "failed": 1, "unknown": 2, "invalid": 3}


def is_send(command) -> bool:
    return bool(command) and command[0] == COMMAND


def argument(command, flag):
    return command[command.index(flag) + 1] if flag in command else None


def result(command, status, detail="fixture"):
    """CompletedProcess exactly as a chat-send/v1 provider would print it."""
    body = {"schema": "chat-send/v1", "status": status, "request_id": argument(command, "--request-id"),
            "to": argument(command, "--to"), "conversation_id": "c_fixture", "submitted_at": 1794043483,
            "detail": detail}
    return subprocess.CompletedProcess(command, EXIT[status], json.dumps(body) + "\n", "")
