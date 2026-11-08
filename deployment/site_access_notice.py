from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path
import time
from urllib.parse import quote
from site_access_message import build_access_message

import chat_send
import settings

ROOT = settings.ROOT
RUNTIME = settings.RUNTIME
CONFIG = RUNTIME / "report-site-access.json"
MESSAGES = RUNTIME / "digest" / "site-access-messages"
RECEIPTS = RUNTIME / "digest" / "site-access-receipts"



def _receipt_send_id(receipt: dict) -> str:
    """chat-send/v1 idempotency key; a v1.x receipt (WeChat server_id) counts as a confirmed legacy send."""
    if receipt.get("send_request_id"):
        return str(receipt["send_request_id"])
    return "legacy-v1:" + str(receipt["server_id"]) if receipt.get("server_id") else ""

def _project_for_notice() -> tuple[dict, str, str]:
    project = settings.load_project(required=True)
    group_id = project["group"]["conversation_id"]
    public_url = project.get("public_url")
    bot = settings.bot_settings(project)
    names = bot.get("names") or []
    if not isinstance(public_url, str) or not public_url.startswith("https://"):
        raise ValueError("project.public_url must be an HTTPS URL for site-access notices")
    if not names:
        raise ValueError("project.bot.names is required for site-access notices")
    return project, group_id, names[0]


def _load_config(path: Path = CONFIG) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    _project, group_id, _bot_name = _project_for_notice()
    public_url = _project["public_url"].rstrip("/") + "/"
    if not isinstance(value, dict) or value.get("chat_id") != group_id or value.get("url") != public_url:
        raise ValueError("invalid site access configuration")
    if not isinstance(value.get("username"), str) or not value["username"] or not isinstance(value.get("password"), str) or not value["password"]:
        raise ValueError("invalid site access configuration")
    if isinstance(value.get("enabled_from"), bool) or not isinstance(value.get("enabled_from"), int) or value["enabled_from"] < 0:
        raise ValueError("invalid site access configuration")
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def process_notice(receipt: dict, *, request_id: str | None = None, config_path: Path = CONFIG, runner=None) -> dict:
    """Deliver/reconcile a text notice for a confirmed PNG receipt."""
    if request_id is not None and (not isinstance(request_id, str) or len(request_id) != 64 or any(c not in "0123456789abcdef" for c in request_id)):
        return {"status": "ineligible"}
    config = _load_config(config_path)
    if config is None:
        return {"status": "disabled"}
    project, chat_id, bot_name = _project_for_notice()
    if (receipt.get("status") != "sent" or receipt.get("chat_id") not in settings.conversation_ids(project) | {chat_id}
            or not _receipt_send_id(receipt) or not receipt.get("png_sha256")
            or isinstance(receipt.get("sent_at"), bool) or not isinstance(receipt.get("sent_at"), int)):
        return {"status": "ineligible"}
    if receipt["sent_at"] < config["enabled_from"]:
        return {"status": "disabled", "reason": "before enabled_from"}
    day = receipt.get('report_day')
    try:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            return {'status': 'ineligible'}
    except ValueError:
        return {'status': 'ineligible'}
    if request_id is not None and receipt.get("request_id") != request_id:
        return {"status": "ineligible"}
    key = request_id if request_id is not None else day
    RECP = Path(RECEIPTS) / (key + ".json")
    state = {}
    try:
        state = json.loads(RECP.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    if state.get("png_sha256") not in (None, receipt["png_sha256"]):
        return {"status": "failed", "reason": "receipt fingerprint mismatch"}
    if state.get("status") in {"sent", "unknown"}:
        return {"status": state["status"], "already_terminal": True}
    if state.get("status") == "sending":
        state.update(status="unknown", updated_at=int(time.time()))
        _write_json(RECP, state)
        return {"status": "unknown"}
    if state and not (state.get('status') == 'failed' and state.get('before_submit') is True):
        state.update(status='unknown', updated_at=int(time.time()))
        _write_json(RECP, state)
        return {'status': 'unknown'}
    site_url = config["url"].removeprefix("https://").removesuffix("/")
    access_url = f"https://{quote(config['username'], safe='')}:{quote(config['password'], safe='')}@{site_url}"
    body = build_access_message(receipt['report_day'], access_url, bot_name)
    encoded = body.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    if state.get("text_sha256") not in (None, digest):
        return {"status": "failed", "reason": "message fingerprint mismatch"}
    MESSAGES.mkdir(parents=True, exist_ok=True, mode=0o700)
    message_path = MESSAGES / f"{key}-{digest}.txt"
    if message_path.exists() and hashlib.sha256(message_path.read_bytes()).hexdigest() != digest:
        return {"status": "failed", "reason": "message file integrity mismatch"}
    if not message_path.exists():
        message_path.write_bytes(encoded)
    os.chmod(message_path, 0o600)
    state = {"status": "sending", "report_day": receipt["report_day"], "chat_id": chat_id,
             "png_sha256": receipt["png_sha256"], "text_sha256": digest, "updated_at": int(time.time())}
    if request_id is not None:
        state["request_id"] = request_id
    _write_json(RECP, state)
    send_cfg = chat_send.send_config(project)
    rid = chat_send.request_id("site-access", chat_id, key, digest)
    state["send_request_id"] = rid
    _write_json(RECP, state)
    delivered = chat_send.send(send_cfg, rid=rid, text_file=message_path,
                               **({"runner": runner} if runner is not None else {}))
    if delivered["status"] == "sent":
        state.update(status="sent", chat_id=chat_id, sent_at=int(time.time()))
    elif delivered["status"] in {"failed", "invalid"} and delivered.get("before_submit") is True:
        state.update(status="failed", before_submit=True)
    else:
        state.update(status="unknown")
    state["updated_at"] = int(time.time())
    _write_json(RECP, state)
    return ({"status": "sent", "chat_id": chat_id, "send_request_id": rid, "sent_at": state["sent_at"]}
            if state["status"] == "sent" else {"status": state["status"]})
