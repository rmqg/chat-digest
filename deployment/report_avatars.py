"""Report avatars from a chat-export/v1 members file. Local files only: no network, no client database."""
from __future__ import annotations

import base64
import io
from pathlib import Path

import chat_export

MAX_IMAGE_BYTES = 4 * 1024 * 1024


def _image_bytes(data: bytes) -> bytes | None:
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(data)) as image:
            if image.width <= 0 or image.height <= 0 or image.width * image.height > 40_000_000:
                return None
            image.load()
            image = ImageOps.exif_transpose(image)
            image.thumbnail((256, 256), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.convert("RGBA").save(output, format="PNG", optimize=True)
            return output.getvalue()
    except Exception:
        return None


def _unavailable(reason: str) -> dict:
    return {"available": False, "data_uri": "", "source": "", "reason": reason}


def collect_avatars(export_root: Path | str, conversation_id: str, ids: list[str], *, manifest: dict | None = None) -> dict:
    """Map sender_id to a small embedded PNG. Missing avatars stay missing; names are never used to guess."""
    root = Path(export_root)
    manifest = manifest or chat_export.load_manifest(root)
    conversation = chat_export.find_conversation(manifest, conversation_id)
    members = chat_export.load_members(root, conversation)
    result = {}
    for sender in dict.fromkeys(ids):
        member = members.get(sender)
        relative = member.get("avatar") if isinstance(member, dict) else None
        if not isinstance(relative, str) or not relative:
            result[sender] = _unavailable("avatar_not_exported")
            continue
        try:
            path = chat_export._inside(root, relative)
            data = path.read_bytes() if path.is_file() and path.stat().st_size <= MAX_IMAGE_BYTES else b""
        except (OSError, chat_export.ExportError):
            data = b""
        normalized = _image_bytes(data)
        if normalized is None:
            result[sender] = _unavailable("avatar_unreadable")
            continue
        result[sender] = {"available": True, "source": "export",
                          "data_uri": "data:image/png;base64," + base64.b64encode(normalized).decode("ascii")}
    return result
