#!/usr/bin/env python3
"""Read-only consumer for the chat-export/v1 directory format (see docs/CONTRACT.md).

Producers (wechat-export-send or any other compatible exporter) append messages; this module never
writes into an export, never reads the producer's ``state/`` directory and never trusts paths that leave the
export root. Messages are normalized into the digest's internal message dictionaries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA = "chat-export/v1"
PLATFORMS = {"wechat", "wecom", "qq", "other"}
KINDS = {"group", "private", "channel", "other"}
MESSAGE_TYPES = {"text", "image", "file", "link", "voice", "video", "sticker", "quote", "forward",
                 "system", "revoke", "other"}
MEDIA_KINDS = {"image", "file", "voice", "video", "sticker"}
MEDIA_STATUSES = {"ready", "pending", "missing"}
_CONVERSATION_ID = re.compile(r"[A-Za-z0-9_.-]{1,200}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PLACEHOLDERS = {"image": "[图片]", "sticker": "[表情]", "video": "[视频]", "voice": "[语音]", "file": "[文件]"}
MAX_LINE_BYTES = 8 * 1024 * 1024


class ExportError(ValueError):
    """The export is malformed or unsafe; consumers must stop instead of guessing."""


def _safe_conversation_id(value) -> str:
    if not isinstance(value, str) or not _CONVERSATION_ID.fullmatch(value) or value in {".", ".."}:
        raise ExportError("conversation_id must match [A-Za-z0-9_.-]")
    return value


def _inside(root: Path, relative, *, must_exist: bool = True) -> Path:
    if not isinstance(relative, str) or not relative or relative.startswith("/") or "\\" in relative:
        raise ExportError("export paths must be relative POSIX paths")
    parts = Path(relative).parts
    if ".." in parts or (parts and parts[0] == "state"):
        raise ExportError("export path escapes the export root or points into state/")
    resolved_root = root.resolve()
    path = (resolved_root / relative)
    if must_exist:
        path = path.resolve(strict=True)
        if not path.is_relative_to(resolved_root):
            raise ExportError("export path resolves outside the export root")
    return path


def load_manifest(root: Path | str) -> dict:
    root = Path(root)
    try:
        raw = (root / "manifest.json").read_bytes()
    except FileNotFoundError:
        raise ExportError(f"manifest.json not found in export root {root}") from None
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ExportError("manifest.json must be UTF-8 without BOM")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ExportError(f"manifest.json is not valid UTF-8 JSON: {exc}") from None
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise ExportError(f"unsupported export schema; expected {SCHEMA}")
    source = manifest.get("source")
    if not isinstance(source, dict) or source.get("platform") not in PLATFORMS:
        raise ExportError("manifest source.platform must be wechat, wecom, qq or other")
    if not isinstance(source.get("account_id"), str) or not source["account_id"]:
        raise ExportError("manifest source.account_id must be a non-empty string")
    conversations = manifest.get("conversations")
    if not isinstance(conversations, list):
        raise ExportError("manifest conversations must be an array")
    seen = set()
    for item in conversations:
        if not isinstance(item, dict):
            raise ExportError("each manifest conversation must be an object")
        cid = _safe_conversation_id(item.get("id"))
        if cid in seen:
            raise ExportError(f"duplicate conversation id in manifest: {cid}")
        seen.add(cid)
        if item.get("kind") not in KINDS:
            raise ExportError(f"conversation {cid} has an unsupported kind")
        if not isinstance(item.get("file"), str):
            raise ExportError(f"conversation {cid} has no message file")
    return manifest


def list_conversations(root: Path | str) -> list[dict]:
    manifest = load_manifest(root)
    return sorted(({"conversation_id": item["id"], "kind": item["kind"], "name": item.get("name") or item["id"],
                    "last_ts": item.get("last_ts"), "message_count": item.get("message_count")}
                   for item in manifest["conversations"]), key=lambda value: (value["name"], value["conversation_id"]))


def find_conversation(manifest: dict, conversation_id: str) -> dict:
    conversation_id = _safe_conversation_id(conversation_id)
    for item in manifest["conversations"]:
        if item["id"] == conversation_id:
            return item
    raise ExportError(f"conversation not found in export: {conversation_id}")


def message_uid(manifest: dict, conversation_id: str, message_id: str) -> str:
    source = manifest["source"]
    value = "\0".join((SCHEMA, source["platform"], source["account_id"], conversation_id, message_id))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_row(row: dict, where: str) -> dict:
    if not isinstance(row, dict):
        raise ExportError(f"{where}: each line must be a JSON object")
    if not isinstance(row.get("id"), str) or not row["id"]:
        raise ExportError(f"{where}: id must be a non-empty string")
    if type(row.get("ts")) is not int or row["ts"] < 0:
        raise ExportError(f"{where}: ts must be integer Unix seconds")
    if not isinstance(row.get("sender_id"), str) or not row["sender_id"]:
        raise ExportError(f"{where}: sender_id must be a non-empty string")
    if row.get("type") not in MESSAGE_TYPES:
        raise ExportError(f"{where}: unsupported message type {row.get('type')!r}")
    if "text" in row and row["text"] is not None and not isinstance(row["text"], str):
        raise ExportError(f"{where}: text must be a string")
    return row


def iter_rows(root: Path | str, conversation: dict):
    """Yield validated rows in file order. An incomplete final line (no trailing newline) is ignored."""
    root = Path(root)
    path = _inside(root, conversation["file"])
    with path.open("rb") as stream:
        number = 0
        while True:
            line = stream.readline(MAX_LINE_BYTES + 1)
            if not line:
                return
            number += 1
            if not line.endswith(b"\n"):
                if len(line) > MAX_LINE_BYTES:
                    raise ExportError(f"{conversation['file']}:{number}: line exceeds {MAX_LINE_BYTES} bytes")
                return  # producer is still writing this line
            if number == 1 and line.startswith(b"\xef\xbb\xbf"):
                raise ExportError(f"{conversation['file']}: files must be UTF-8 without BOM")
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ExportError(f"{conversation['file']}:{number}: invalid JSON line ({exc})") from None
            yield _validate_row(row, f"{conversation['file']}:{number}")


def load_members(root: Path | str, conversation: dict) -> dict[str, dict]:
    relative = conversation.get("members_file")
    if not relative:
        return {}
    try:
        path = _inside(Path(root), relative)
    except FileNotFoundError:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    members = value.get("members") if isinstance(value, dict) else None
    if not isinstance(members, list):
        raise ExportError("members file must contain a members array")
    return {item["id"]: item for item in members if isinstance(item, dict) and isinstance(item.get("id"), str)}


def media_file(root: Path | str, item: dict, *, verify: bool = True) -> Path | None:
    """Return a verified ready media file, or None when it is pending/missing/thumbnail-only."""
    if not isinstance(item, dict) or item.get("status") != "ready":
        return None
    try:
        path = _inside(Path(root), item.get("path"))
    except FileNotFoundError:
        return None
    if not path.is_file():
        return None
    expected = item.get("sha256")
    if verify:
        if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
            raise ExportError("ready media requires a lowercase sha256")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise ExportError("media file does not match its sha256")
    return path


def _clean(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def readable_text(row: dict) -> tuple[str, str]:
    """(internal type label, readable text). Never invents content: placeholders only mark media."""
    kind = row["type"]
    text = _clean(row.get("text"))
    if kind in {"text", "quote"}:
        return "text", text
    if kind in _PLACEHOLDERS:
        if kind == "file" and not text:
            names = [_clean(item.get("name")) for item in row.get("media") or [] if isinstance(item, dict)]
            names = [name for name in names if name]
            return "file", ("[文件] " + "、".join(names)) if names else _PLACEHOLDERS["file"]
        return kind, text or _PLACEHOLDERS[kind]
    if kind == "link":
        link = row.get("link") if isinstance(row.get("link"), dict) else {}
        parts = [text, _clean(link.get("title")), _clean(link.get("description")), _clean(link.get("url"))]
        return "link", "\n".join(dict.fromkeys(part for part in parts if part))
    if kind == "forward":
        lines = [text] if text else []
        for entry in row.get("forward") or []:
            if not isinstance(entry, dict):
                continue
            body = _clean(entry.get("text")) or _PLACEHOLDERS.get(entry.get("type"), "")
            if body:
                name = _clean(entry.get("sender_name")) or "未知成员"
                lines.append(f"{name}：{body}")
        return "forward", "\n".join(lines)
    if kind == "system":
        return "system", text
    if kind == "revoke":
        return "recall", text
    return "other", text


def read_messages(root: Path | str, conversation_id: str, start: int, end: int, *,
                  manifest: dict | None = None) -> tuple[dict, list[dict]]:
    """Return (conversation, messages) with start <= ts < end, deduplicated by id (last line wins)."""
    root = Path(root)
    manifest = manifest or load_manifest(root)
    conversation = find_conversation(manifest, conversation_id)
    members = load_members(root, conversation)
    latest: dict[str, dict] = {}
    order: dict[str, int] = {}
    for index, row in enumerate(iter_rows(root, conversation)):
        if row["id"] not in order:
            order[row["id"]] = index
        latest[row["id"]] = row
    messages = []
    for mid, row in latest.items():
        if not start <= row["ts"] < end:
            continue
        label, text = readable_text(row)
        sender = row["sender_id"]
        system = sender == "system" or label in {"system", "recall"}
        member = members.get(sender, {})
        author = "系统" if system else (_clean(row.get("sender_name")) or _clean(member.get("name"))
                                         or _clean(member.get("display_name")) or sender)
        message = {"message_uid": message_uid(manifest, conversation["id"], mid), "message_id": mid,
                   "conversation_id": conversation["id"], "timestamp": row["ts"], "_order": order[mid],
                   "author_id": "" if system else sender, "author": author, "text": text, "type": label,
                   "export_type": row["type"], "is_self": row.get("is_self") is True}
        for key in ("reply_to", "link", "forward", "media", "mentions"):
            if row.get(key) is not None:
                message[key] = row[key]
        messages.append(message)
    messages.sort(key=lambda item: (item["timestamp"], item["_order"], item["message_uid"]))
    for message in messages:
        message.pop("_order", None)
    return conversation, messages


def fingerprint(root: Path | str, conversation_id: str) -> str:
    """Cheap change detector for schedulers: manifest entry plus message file size and mtime."""
    root = Path(root)
    manifest = load_manifest(root)
    conversation = find_conversation(manifest, conversation_id)
    path = _inside(root, conversation["file"])
    info = path.stat()
    value = [conversation.get("last_ts"), conversation.get("message_count"), info.st_size, info.st_mtime_ns]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--export-root", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list conversations (no message text)")
    check = sub.add_parser("check", help="validate one conversation; prints counts only")
    check.add_argument("--conversation-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            print(json.dumps(list_conversations(args.export_root), ensure_ascii=False, indent=2))
            return 0
        conversation, messages = read_messages(args.export_root, args.conversation_id, 0, 2 ** 62)
        media = [item for message in messages for item in message.get("media", []) if isinstance(item, dict)]
        print(json.dumps({"conversation_id": conversation["id"], "messages": len(messages),
                          "media": {status: sum(item.get("status") == status for item in media) for status in sorted(MEDIA_STATUSES)},
                          "first_ts": messages[0]["timestamp"] if messages else None,
                          "last_ts": messages[-1]["timestamp"] if messages else None}, ensure_ascii=False))
        return 0
    except (ExportError, OSError, ValueError) as exc:
        print(f"chat_export: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
