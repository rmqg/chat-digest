"""Test helpers: build a small, wholly fictional chat-export/v1 directory."""
import hashlib
import json
from pathlib import Path

CONVERSATION = "g_fixture"


def add_media(root: Path, payload: bytes, ext: str = "png") -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    relative = f"media/{digest[:2]}/{digest}.{ext}"
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {"path": relative, "sha256": digest, "bytes": len(payload)}


def build_export(root, rows, *, members=None, conversation=CONVERSATION, name="虚构测试群",
                 platform="wechat", partial_tail: str | None = None) -> Path:
    root = Path(root)
    (root / "conversations").mkdir(parents=True, exist_ok=True)
    lines = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    if partial_tail is not None:
        lines += partial_tail
    (root / "conversations" / f"{conversation}.jsonl").write_text(lines, encoding="utf-8")
    entry = {"id": conversation, "kind": "group", "name": name, "file": f"conversations/{conversation}.jsonl",
             "last_ts": max((row["ts"] for row in rows), default=0), "message_count": len(rows)}
    if members is not None:
        (root / "members").mkdir(exist_ok=True)
        (root / "members" / f"{conversation}.json").write_text(json.dumps(
            {"conversation_id": conversation, "updated_at": 1, "members": members}, ensure_ascii=False), encoding="utf-8")
        entry["members_file"] = f"members/{conversation}.json"
    manifest = {"schema": "chat-export/v1", "producer": {"name": "fixture", "version": "0"},
                "source": {"platform": platform, "account_id": "acct_fixture"},
                "generated_at": "2026-11-08T12:00:00+08:00", "timezone": "Asia/Shanghai", "conversations": [entry]}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return root


def text(mid, ts, sender, body, **extra):
    return {"id": mid, "ts": ts, "sender_id": sender, "sender_name": extra.pop("name", sender), "type": "text",
            "text": body, **extra}
