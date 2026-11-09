#!/usr/bin/env python3
"""Portable project settings for public deployment tools."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("CHAT_DIGEST_ROOT", Path(__file__).resolve().parents[1])).expanduser().resolve()
RUNTIME = Path(os.environ.get("CHAT_DIGEST_RUNTIME_ROOT", ROOT / "runtime")).expanduser().resolve()
CONFIG_PATH = Path(os.environ.get("CHAT_DIGEST_CONFIG", RUNTIME / "project.json")).expanduser()


DEFAULTS: dict[str, Any] = {
    "report_prefix": "chat-digest", "docker_network": "chat-digest_default",
    "public_url": None, "image_origin_host": None, "daily_hour": 5, "timezone": "Asia/Shanghai",
    "worker_uid": 1000, "worker_gid": 1000,
    "export": {"root": None, "refresh_avatars_command": None},
    "images": {"runtime": "chat-digest-runtime:2.0.0"},
}


def runtime_image(name: str, root: Path | str = ROOT) -> str:
    """Read the immutable image ID pinned by explicit bootstrap."""
    config = load_project(root=root)
    reference = config["images"].get(name)
    if not isinstance(reference, str) or not reference.strip() or reference.startswith("REPLACE_WITH_"):
        raise ValueError(f"images.{name} must name a local image reference")
    root_path = Path(root).expanduser().resolve()
    pins_path = (RUNTIME if root_path == ROOT else root_path / "runtime") / "image-pins.json"
    try:
        pins = json.loads(pins_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("Image pins are missing; run `python3 deployment/bootstrap.py --pin-images` after building local images") from None
    record = pins.get("images", {}).get(name) if isinstance(pins, dict) else None
    if not isinstance(record, dict) or record.get("reference") != reference:
        raise ValueError(f"Image pin for {name} is missing or stale; after an intentional upgrade run `python3 deployment/bootstrap.py --refresh-pins`")
    image_id = record.get("id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:") or len(image_id) != 71:
        raise ValueError(f"Image pin for {name} is not an immutable Docker image ID")
    return image_id

def load_project(required: bool = False, root: Path | str = ROOT) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    configured = os.environ.get("CHAT_DIGEST_CONFIG")
    path = Path(configured).expanduser() if configured else (CONFIG_PATH if root == ROOT else root / "runtime" / "project.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise ValueError(f"project configuration is required: {path}") from None
        value = {}
    if not isinstance(value, dict):
        raise ValueError("project configuration must be a JSON object")
    config = {**DEFAULTS, **value}
    for field in ("group", "bot", "images", "export"):
        if not isinstance(config.get(field, {}), dict):
            raise ValueError(f"project {field} configuration must be an object")
    config["group"] = config.get("group", {})
    config["bot"] = config.get("bot", {})
    config["images"] = {**DEFAULTS["images"], **config.get("images", {})}
    config["export"] = {**DEFAULTS["export"], **config.get("export", {})}
    import render_plugin  # validates the optional "render" object (external renderer)
    render_plugin.normalize(config.get("render"))
    if "id" in config["group"] and "conversation_id" not in config["group"]:
        raise ValueError("group.id is a v1 WeChat field; v2 reads chat-export/v1 and needs group.conversation_id "
                         "(see docs/MIGRATION-v2.md)")
    if required:
        for field in ("conversation_id", "name"):
            item = config["group"].get(field)
            if not isinstance(item, str) or not item.strip() or item.startswith("REPLACE_WITH_"):
                raise ValueError(f"explicit group.{field} is required for this operation")
    return config


def conversation_ids(config: dict[str, Any] | None = None) -> set[str]:
    """The configured conversation plus group.aliases: earlier IDs of the same chat (for example the v1
    WeChat group ID) whose reports and receipts belong to this conversation's history."""
    config = config or load_project()
    group = config.get("group", {})
    aliases = group.get("aliases", [])
    if not isinstance(aliases, list) or any(not isinstance(item, str) or not item for item in aliases):
        raise ValueError("group.aliases must be a list of non-empty strings")
    current = group.get("conversation_id")
    return {item for item in [current, *aliases] if isinstance(item, str) and item}


def export_root(config: dict[str, Any] | None = None) -> Path:
    """Host path of the chat-export/v1 directory (read-only for this project)."""
    config = config or load_project()
    value = os.environ.get("CHAT_DIGEST_EXPORT_ROOT") or config.get("export", {}).get("root")
    if not isinstance(value, str) or not value.strip() or value.startswith("REPLACE_WITH_"):
        raise ValueError("export.root must name the chat-export/v1 directory")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()



def bot_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_project(required=True)
    bot = config["bot"]
    names = bot.get("names", [])
    if not isinstance(names, list) or any(not isinstance(name, str) or not name.strip() for name in names):
        raise ValueError("bot.names must be a list of non-empty strings")
    sender_ids = bot.get("sender_ids", [])
    if not isinstance(sender_ids, list) or any(not isinstance(item, str) or not item for item in sender_ids):
        raise ValueError("bot.sender_ids must be a list of export sender_id strings")
    return {"names": names, "sender_ids": sender_ids}
