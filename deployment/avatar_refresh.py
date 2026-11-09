"""Ask the export provider to refresh this window's speakers before a daily report.

Avatars live in the export; the provider does the downloading. A failure here never fails the report: the
report then uses the avatars already exported, and the outcome is logged (counts only).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import chat_export
import settings

TIMEOUT_SECONDS = 240


def refresh_speakers(project: dict, start: int, end: int, *, runner=subprocess.run) -> dict:
    command = (project.get("export") or {}).get("refresh_avatars_command")
    if not command:
        return {"status": "disabled"}
    work = settings.RUNTIME / "digest"
    outcome: dict = {"time": int(time.time()), "window": [start, end]}
    temporary = None
    try:
        conversation_id = project["group"]["conversation_id"]
        _conversation, messages = chat_export.read_messages(settings.export_root(project), conversation_id, start, end)
        ids = sorted({message["author_id"] for message in messages if message["author_id"]} | {conversation_id})
        work.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".avatar-refresh-", dir=work)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(ids) + "\n")
        completed = runner([*command, "--ids-file", temporary], capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        outcome.update(status="ok" if completed.returncode == 0 else "failed", requested=len(ids), code=completed.returncode)
        try:
            outcome["stats"] = json.loads(completed.stdout)
        except (TypeError, ValueError):
            pass
    except Exception as exc:  # the report must still be generated, with the avatars already exported
        outcome.update(status="failed", error=type(exc).__name__)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    try:
        with (work / "avatar-refresh.log").open("a", encoding="utf-8") as log:
            log.write(json.dumps(outcome, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return outcome
