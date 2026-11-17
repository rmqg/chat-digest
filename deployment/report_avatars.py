"""Report avatars from a chat-export/v1 members file. Local files only: no network, no client database."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import tempfile
from pathlib import Path

import chat_export

MAX_IMAGE_BYTES = 4 * 1024 * 1024
AVATAR_SIZE = (96, 96)


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
            image.thumbnail(AVATAR_SIZE, Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.convert("RGBA").save(output, format="PNG", compress_level=6)
            return output.getvalue()
    except Exception:
        return None


def _unavailable(reason: str) -> dict:
    return {"available": False, "data_uri": "", "source": "", "reason": reason}


def _read_cached_uri(cache_file: Path) -> str | None:
    try:
        uri = cache_file.read_text(encoding="ascii").strip()
        return uri if uri.startswith("data:image/png;base64,") else None
    except OSError:
        return None


def _write_cached_uri(cache_file: Path, uri: str) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(cache_file.parent, 0o700)
    fd, name = tempfile.mkstemp(prefix="." + cache_file.name + "-", dir=cache_file.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(uri)
        os.replace(name, cache_file)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def collect_avatars(export_root: Path | str, conversation_id: str, ids: list[str], *,
                    manifest: dict | None = None, cache_dir: Path | str | None = None) -> dict:
    """Map sender_id to a small embedded PNG. Missing avatars stay missing; names are never used to guess."""
    root = Path(export_root)
    cache_root = Path(cache_dir) if cache_dir is not None else None
    manifest = manifest or chat_export.load_manifest(root)
    conversation = chat_export.find_conversation(manifest, conversation_id)
    members = chat_export.load_members(root, conversation)
    result = {}
    for sender in dict.fromkeys(ids):
        member = conversation if sender == conversation["id"] else members.get(sender)
        relative = member.get("avatar") if isinstance(member, dict) else None
        if not isinstance(relative, str) or not relative:
            result[sender] = _unavailable("avatar_not_exported")
            continue
        try:
            path = chat_export._inside(root, relative)
            data = path.read_bytes() if path.is_file() and path.stat().st_size <= MAX_IMAGE_BYTES else b""
        except (OSError, chat_export.ExportError):
            data = b""
        if not data:
            result[sender] = _unavailable("avatar_unreadable")
            continue
        cache_file = (cache_root / f"{hashlib.sha256(data).hexdigest()}-96.uri") if cache_root is not None else None
        uri = _read_cached_uri(cache_file) if cache_file is not None else None
        if uri is None:
            normalized = _image_bytes(data)
            if normalized is None:
                result[sender] = _unavailable("avatar_unreadable")
                continue
            uri = "data:image/png;base64," + base64.b64encode(normalized).decode("ascii")
            if cache_file is not None:
                try:
                    _write_cached_uri(cache_file, uri)
                except OSError:
                    pass
        result[sender] = {"available": True, "source": "export", "data_uri": uri}
    return result
