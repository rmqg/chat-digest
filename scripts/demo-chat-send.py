#!/usr/bin/env python3
"""Offline stand-in for a chat-send/v1 provider. It NEVER contacts any chat client or network.

Each accepted request is copied into --outbox with a receipt, so the demo and doctor can exercise the
contract (including idempotent replay by --request-id) without sending anything to anyone.
"""
import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path


def emit(status, code, **fields):
    print(json.dumps({"schema": "chat-send/v1", "status": status, **fields}, ensure_ascii=False))
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--outbox", required=True, type=Path)
    parser.add_argument("--to", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text")
    group.add_argument("--text-file", type=Path)
    group.add_argument("--image", type=Path)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code in (0, None):
            raise
        return emit("invalid", 3, request_id=None, detail="invalid arguments")
    rid = args.request_id
    receipt = args.outbox / (hashlib.sha256(rid.encode()).hexdigest()[:24] + ".json")
    if receipt.exists():
        previous = json.loads(receipt.read_text(encoding="utf-8"))
        return emit("sent", 0, request_id=rid, to=args.to, conversation_id="demo", submitted_at=previous["submitted_at"],
                    detail="already sent (idempotent replay)")
    source = args.image or args.text_file
    if source is not None and not source.is_file():
        return emit("failed", 1, request_id=rid, to=args.to, detail="input file does not exist")
    if args.dry_run:
        return emit("failed", 1, request_id=rid, to=args.to, detail="dry run: target and input are valid, nothing submitted")
    args.outbox.mkdir(parents=True, exist_ok=True)
    stored = None
    if source is not None:
        stored = args.outbox / (receipt.stem + source.suffix)
        shutil.copyfile(source, stored)
    else:
        stored = args.outbox / (receipt.stem + ".txt")
        stored.write_text(args.text, encoding="utf-8")
    now = int(time.time())
    receipt.write_text(json.dumps({"request_id": rid, "to": args.to, "file": stored.name, "submitted_at": now}), encoding="utf-8")
    return emit("sent", 0, request_id=rid, to=args.to, conversation_id="demo", submitted_at=now,
                detail="written to the local demo outbox; nothing was sent")


if __name__ == "__main__":
    sys.exit(main())
