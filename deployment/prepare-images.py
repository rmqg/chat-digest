#!/usr/bin/env python3
"""Prepare the selected conversation's exported images for captioning and OCR (no network, read-only export)."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
from PIL import Image
import chat_export
import settings
import digest

DEFAULT_PENDING_GRACE_SECONDS = 6 * 3600


def source_image_format(data):
    if not data:
        return None
    checks = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF87a", "gif"), (b"GIF89a", "gif"),
              (b"RIFF", "webp"), (b"BM", "bmp"), (b"II*\x00", "tif"), (b"MM\x00*", "tif"))
    for magic, name in checks:
        if data.startswith(magic):
            if name == "webp" and data[8:12] != b"WEBP":
                return None
            return name
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"heic", b"heix", b"hevc", b"hevx", b"mif1"}:
        return "heic"
    return None


def model_image(data, fmt):
    if fmt == "heic":
        from PIL import ImageOps
        from pillow_heif import register_heif_opener
        register_heif_opener()
        output = io.BytesIO()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            ImageOps.exif_transpose(image, in_place=True)
            image.save(output, format="PNG")
        data, fmt = output.getvalue(), "png"
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        dimensions = image.size
    return data, fmt, dimensions


def cached_model_image(data, fmt, cache_dir):
    """Reuse only byte-verified conversions of the exact complete source image."""
    source_sha = hashlib.sha256(data).hexdigest()
    metadata_path = cache_dir / f"{source_sha}.json"
    try:
        cached = json.loads(metadata_path.read_text())
        model_format = cached["format"]
        if cached.get("version") != 1 or cached.get("source_format") != fmt or model_format not in {"png", "jpg", "gif", "webp", "bmp", "tif"}:
            raise ValueError("cached conversion metadata differs")
        path = cache_dir / f"{source_sha}.{model_format}"
        converted = path.read_bytes()
        if hashlib.sha256(converted).hexdigest() != cached["sha256"]:
            raise ValueError("cached conversion fingerprint changed")
        dimensions = tuple(cached["dimensions"])
        if len(dimensions) != 2 or any(type(value) is not int or value <= 0 for value in dimensions):
            raise ValueError("invalid cached image dimensions")
        return converted, model_format, dimensions, path
    except (OSError, ValueError, KeyError, TypeError):
        pass
    converted, model_format, dimensions = model_image(data, fmt)
    path = cache_dir / f"{source_sha}.{model_format}"
    for target, payload in ((path, converted), (metadata_path, json.dumps({
            "version": 1, "source_format": fmt, "format": model_format, "dimensions": list(dimensions),
            "sha256": hashlib.sha256(converted).hexdigest()}, ensure_ascii=False).encode())):
        fd, temporary = tempfile.mkstemp(prefix=".convert-", dir=cache_dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return converted, model_format, dimensions, path



def file_signature(path):
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    return [str(resolved), info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def prepared_cache_hit(entry, signature, output_dir):
    if not isinstance(entry, dict) or entry.get("inputs") != signature:
        return None
    item = entry.get("item")
    if not isinstance(item, dict) or not item.get("available") or item.get("download_required"):
        return None
    try:
        path = Path(item["image_path"]).resolve(strict=True)
        if path.is_relative_to(output_dir.resolve()) and file_signature(path) == entry.get("output"):
            return dict(item)
    except (OSError, ValueError, KeyError):
        pass
    return None



def _pick_media(message):
    """(status, media item). Images only; a thumbnail is never treated as the original."""
    items = [item for item in message.get("media") or [] if isinstance(item, dict) and item.get("kind") == "image"]
    full = [item for item in items if item.get("is_thumbnail") is not True]
    for item in full:
        if item.get("status") == "ready":
            return "ready", item
    if any(item.get("status") == "pending" for item in full):
        return "pending", next(item for item in full if item.get("status") == "pending")
    if any(item.get("is_thumbnail") is True and item.get("status") == "ready" for item in items):
        return "thumbnail_only", None
    return "missing", None


def prepare(export_root, conversation_id, start, end, runtime_dir, *, pending_grace_seconds=DEFAULT_PENDING_GRACE_SECONDS,
            now=None):
    now = int(time.time()) if now is None else now
    output = runtime_dir / "images" / "decoded"
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    prepared_cache_path = runtime_dir / "prepared-image-cache.json"
    prepared_cache = json.loads(prepared_cache_path.read_text()) if prepared_cache_path.exists() else {}
    _conversation, messages = chat_export.read_messages(export_root, conversation_id, start, end)
    manifest = []
    for message in messages:
        if message["type"] != "image":
            continue
        uid = message["message_uid"]
        item = {"message_uid": uid, "timestamp": message["timestamp"], "available": False, "download_required": False}
        status, media = _pick_media(message)
        if status == "pending" and now - message["timestamp"] > pending_grace_seconds:
            status = "missing"
        if status == "ready":
            try:
                path = chat_export.media_file(export_root, media)
                if path is None:
                    raise FileNotFoundError("ready media file is absent")
                inputs = {"version": 2, "media_sha256": media["sha256"], "file": file_signature(path)}
                ready = prepared_cache_hit(prepared_cache.get(uid), inputs, output)
                if ready:
                    manifest.append(ready)
                    continue
                payload = path.read_bytes()
                source_format = source_image_format(payload)
                if source_format is None:
                    raise ValueError("unsupported image format")
                data, fmt, dimensions, model_path = cached_model_image(payload, source_format, output)
                quality = media.get("source_quality") if media.get("source_quality") in {"original", "client_full"} else "original"
                item.update(available=True, image_path=str(model_path), dimensions=list(dimensions), source_quality=quality,
                            sha256=hashlib.sha256(data).hexdigest(),
                            provenance={"source": "chat-export", "source_format": source_format, "source_bytes": len(payload),
                                        "source_sha256": media["sha256"], "converted_without_resizing": source_format == "heic"})
                prepared_cache[uid] = {"inputs": inputs, "output": file_signature(Path(item["image_path"])), "item": item}
            except (OSError, ValueError, chat_export.ExportError) as exc:
                item.update(source_quality="missing", terminal=True, reason=f"exported image could not be used: {type(exc).__name__}")
        elif status == "pending":
            item.update(source_quality="pending", reason="the exporter has not downloaded the original yet")
        else:
            item.update(source_quality="missing", terminal=True,
                        reason="only a thumbnail was exported" if status == "thumbnail_only" else "the original image is missing from the export")
        manifest.append(item)
    fd, temporary = tempfile.mkstemp(prefix=".prepared-images-", dir=runtime_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(prepared_cache, stream, ensure_ascii=False)
        os.chmod(temporary, 0o600)
        os.replace(temporary, prepared_cache_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-root", required=True, type=Path)
    parser.add_argument("--runtime-dir", default="/work", type=Path)
    parser.add_argument("--end", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--pending-grace-seconds", type=int, default=DEFAULT_PENDING_GRACE_SECONDS)
    args = parser.parse_args()
    os.umask(0o077)
    project = settings.load_project(required=True)
    end = digest._parse_time(args.end, int(time.time()))
    start = digest._parse_time(args.start, end - 86400)
    manifest = prepare(args.export_root, project["group"]["conversation_id"], start, end, args.runtime_dir,
                       pending_grace_seconds=args.pending_grace_seconds)
    path = args.manifest or args.runtime_dir / "image-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    os.chmod(path, 0o600)
    print(json.dumps({"image_messages": len(manifest), "ready_images": sum(item["available"] for item in manifest),
                      "pending": sum(item.get("source_quality") == "pending" for item in manifest),
                      "missing": sum(item.get("source_quality") == "missing" for item in manifest),
                      "manifest": str(path)}), flush=True)


if __name__ == "__main__":
    main()
