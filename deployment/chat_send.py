#!/usr/bin/env python3
"""Caller side of the chat-send/v1 command contract (see docs/CONTRACT.md).

chat-digest never drives a chat client itself. It runs the configured command prefix, for example
``["wechat-send"]`` or ``["docker", "exec", "wechat", "wechat-send"]``, and classifies the JSON result.
Anything that cannot be proven to be a pre-submit failure is reported as ``unknown`` and must not be retried
automatically.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

SCHEMA = "chat-send/v1"
EXIT_STATUS = {0: "sent", 1: "failed", 2: "unknown", 3: "invalid"}
_REQUEST_ID = re.compile(r"[A-Za-z0-9_.:-]{8,128}")


class SendConfigError(ValueError):
    pass


def send_config(project: dict) -> dict:
    """Validate project["send"]; returns a normalized copy without touching the network."""
    config = project.get("send")
    if not isinstance(config, dict):
        raise SendConfigError("send configuration is required for delivery (project.send)")
    command = config.get("command")
    if (not isinstance(command, list) or not command
            or any(not isinstance(part, str) or not part or part.startswith("REPLACE_WITH_") for part in command)):
        raise SendConfigError("send.command must be a non-empty list of strings, e.g. [\"wechat-send\"]")
    target = config.get("to")
    if not isinstance(target, str) or not target.strip() or target.startswith("REPLACE_WITH_"):
        raise SendConfigError("send.to must name the target conversation")
    timeout = config.get("timeout", 180)
    if type(timeout) is not int or not 10 <= timeout <= 3600:
        raise SendConfigError("send.timeout must be an integer between 10 and 3600 seconds")
    return {"command": list(command), "to": target, "timeout": timeout}


def config_fingerprint(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def request_id(*parts: str) -> str:
    """Stable idempotency key for one logical delivery (same inputs, same key)."""
    return "cd-" + hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:40]


def _classify(returncode: int, stdout: str, rid: str) -> dict:
    parsed = None
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            break
        if isinstance(value, dict):
            parsed = value
        break
    expected = EXIT_STATUS.get(returncode)
    if parsed is None or parsed.get("schema") != SCHEMA:
        return {"status": "unknown", "request_id": rid, "reason": "sender output is not a chat-send/v1 object",
                "returncode": returncode}
    status = parsed.get("status")
    if status != expected:
        # A disagreement between exit code and JSON is never trusted as success or as a safe failure.
        return {"status": "unknown", "request_id": rid, "reason": "sender exit code and status disagree",
                "returncode": returncode, "reported": status}
    if parsed.get("request_id") not in (None, rid):
        return {"status": "unknown", "request_id": rid, "reason": "sender answered for another request_id",
                "returncode": returncode}
    result = {key: parsed[key] for key in ("status", "to", "conversation_id", "submitted_at", "detail")
              if key in parsed}
    result.update(request_id=rid, returncode=returncode)
    if status == "sent" and type(parsed.get("submitted_at")) is not int:
        result["submitted_at"] = None
    return result


def send(config: dict, *, rid: str, text: str | None = None, text_file: Path | None = None,
         image: Path | None = None, dry_run: bool = False, runner=None) -> dict:
    """Run one chat-send/v1 call. Exactly one of text, text_file or image."""
    chosen = [value is not None for value in (text, text_file, image)]
    if sum(chosen) != 1:
        raise ValueError("exactly one of text, text_file or image is required")
    if not _REQUEST_ID.fullmatch(rid):
        raise ValueError("request id must be 8-128 characters of [A-Za-z0-9_.:-]")
    command = [*config["command"], "--to", config["to"]]
    if text is not None:
        command += ["--text", text]
    elif text_file is not None:
        command += ["--text-file", str(text_file)]
    else:
        command += ["--image", str(image)]
    command += ["--request-id", rid, "--timeout", str(config["timeout"]), "--json"]
    if dry_run:
        command.append("--dry-run")
    try:
        completed = (runner or subprocess.run)(command, capture_output=True, text=True, timeout=config["timeout"] + 30)
    except subprocess.TimeoutExpired:
        return {"status": "unknown", "request_id": rid, "reason": "sender did not finish before the timeout"}
    except OSError as exc:
        # The command could not even be started, so nothing was submitted.
        return {"status": "failed", "request_id": rid, "reason": f"sender could not start: {type(exc).__name__}",
                "before_submit": True}
    result = _classify(completed.returncode, completed.stdout, rid)
    if result["status"] in {"failed", "invalid"}:
        result["before_submit"] = True
    if result["status"] != "sent":
        tail = (completed.stderr or "")[-400:]
        if tail:
            result["stderr_tail"] = tail
    return result
