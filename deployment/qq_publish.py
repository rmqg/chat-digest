#!/usr/bin/env python3
"""Publish one QQ group's minimal report bundle using a restricted SSH identity."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
from report_web_style import reader_styles
_PUBLISHER_CODE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


_SAFE_STEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,160}\Z")
_SAFE_ASSET = re.compile(r"[a-f0-9]{64}\.(?:png|jpe?g|gif|webp|woff2?|ttf|otf)\Z")
_BODY_OPEN_RE = re.compile(rb"<body\b[^>]*>", re.IGNORECASE)
_PARTIAL_BODY_RE = re.compile(rb"<(?:b(?:o(?:d(?:y(?:\b[^>]*)?)?)?)?)?\Z", re.IGNORECASE)
_HTML_STREAM_CHUNK = 256 * 1024
_HASH_CHUNK = 1024 * 1024


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(_HASH_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _mem_available_kb() -> int | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as stream:
            for line in stream:
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except (OSError, ValueError):
        return None
    return None


def _configured_min_avail_kb(default: int = 512000) -> int:
    raw = os.environ.get("CHAT_DIGEST_PUBLISH_MIN_AVAIL_KB")
    if raw is None or not raw.strip():
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _copy_html_with_reader_chrome(source: Path, destination: Path, styles: str, navigation: str) -> None:
    """Stream HTML while injecting navigation after <body> and styles before </head>."""
    styles_bytes = styles.encode("utf-8")
    nav_bytes = navigation.encode("utf-8")
    injected_head = False
    injected_body = False
    buf = b""
    with source.open("rb") as inp, destination.open("wb") as out:
        while True:
            chunk = inp.read(_HTML_STREAM_CHUNK)
            eof = not chunk
            if chunk:
                buf += chunk
            if not injected_head:
                head_idx = buf.find(b"</head>")
                if head_idx != -1:
                    buf = buf[:head_idx] + styles_bytes + buf[head_idx:]
                    injected_head = True
            if not injected_body:
                body_match = _BODY_OPEN_RE.search(buf)
                if body_match is not None:
                    buf = buf[:body_match.end()] + nav_bytes + buf[body_match.end():]
                    injected_body = True
            if eof or (injected_head and injected_body):
                if buf:
                    out.write(buf)
                    buf = b""
                if eof:
                    break
                for tail_chunk in iter(lambda: inp.read(_HTML_STREAM_CHUNK), b""):
                    out.write(tail_chunk)
                break
            keep = 7 if not injected_head else 0
            if not injected_body:
                partial_match = _PARTIAL_BODY_RE.search(buf)
                if partial_match is not None:
                    keep = max(keep, min(len(buf) - partial_match.start(), 65536))
            if keep == 0:
                out.write(buf)
                buf = b""
            elif len(buf) > keep:
                out.write(buf[:-keep])
                buf = buf[-keep:]


def _source_stamp(source: Path) -> str:
    """Fingerprint source names and metadata before any report bytes are copied."""
    source = Path(source).resolve(strict=True)
    candidates = []
    for path in source.iterdir():
        if path.is_symlink() or not path.is_file():
            if path.name == "assets":
                continue
            continue
        if path.name.endswith(("-brief.json", "-brief.html", "-brief.png",
                               "-review.json", "-review.html", "-review.png")):
            candidates.append(path)
    assets = source / "assets"
    if assets.is_dir() and not assets.is_symlink():
        candidates.extend(path for path in assets.iterdir()
                          if not path.is_symlink() and path.is_file() and _SAFE_ASSET.fullmatch(path.name))
    records = []
    for path in sorted(candidates, key=lambda item: item.relative_to(source).as_posix()):
        stat = path.stat()
        records.append((path.relative_to(source).as_posix(), stat.st_size, stat.st_mtime_ns,
                        stat.st_ctime_ns, stat.st_ino))
    return hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()


def build_bundle(source: Path, group_id: int, destination: Path) -> list[str]:
    """Copy paired, nonempty editions and allowlisted hash assets only."""
    source = Path(source).resolve(strict=True)
    destination = Path(destination)
    files: list[str] = []
    for brief in sorted(source.glob("*-brief.json")):
        if brief.is_symlink():
            continue
        base = brief.name[:-len("-brief.json")]
        if not _SAFE_STEM.fullmatch(base):
            continue
        pair = []
        for edition in ("brief", "review"):
            stem = base + "-" + edition
            paths = {ext: source / (stem + "." + ext) for ext in ("html", "png", "json")}
            if any(path.is_symlink() or not path.is_file() or not path.stat().st_size
                   for path in paths.values()):
                break
            metadata = json.loads(paths["json"].read_text(encoding="utf-8"))
            stats = metadata.get("stats")
            count = stats.get("messages") if isinstance(stats, dict) else None
            if (metadata.get("chat_username") != f"qq:{group_id}"
                    or metadata.get("edition") != edition
                    or type(count) is not int or count <= 0):
                break
            pair.append((stem, paths, metadata))
        if len(pair) != 2 or pair[0][2].get("window") != pair[1][2].get("window"):
            continue
        for stem, paths, metadata in pair:
            for extension in ("html", "png"):
                name = stem + "." + extension
                if extension == 'html':
                    sibling = stem[:-6] + '-review.html' if stem.endswith('-brief') else stem[:-7] + '-brief.html'
                    navigation = '<p class="site-home"><a href="./">返回本群首页</a> · <a href="' + sibling + '">切换简报/回顾</a></p>'
                    _copy_html_with_reader_chrome(paths[extension], destination / name, reader_styles(), navigation)
                else:
                    shutil.copyfile(paths[extension], destination / name)
                files.append(name)
            # Source snapshots, raw messages, tokens and model configuration stay local.
            small = {key: metadata[key] for key in
                     ("chat_username", "chat_display", "edition", "window", "range_text", "day", "generated_at")
                     if key in metadata}
            small["stats"] = {"messages": metadata["stats"]["messages"]}
            name = stem + ".json"
            (destination / name).write_text(json.dumps(small, ensure_ascii=False), encoding="utf-8")
            files.append(name)
    assets = source / "assets"
    if assets.is_dir() and not assets.is_symlink():
        for path in sorted(assets.iterdir()):
            if path.is_file() and not path.is_symlink() and _SAFE_ASSET.fullmatch(path.name):
                (destination / "assets").mkdir(exist_ok=True)
                shutil.copyfile(path, destination / "assets" / path.name)
                files.append("assets/" + path.name)
    return files


def _load_config(config_path: Path) -> dict:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("publisher configuration must be an object")
    group_id = config.get("group_id")
    if type(group_id) is not int or group_id <= 0:
        raise ValueError("invalid group id")
    for key, pattern in (("host", r"[A-Za-z0-9.:-]+"), ("user", r"[a-z_][a-z0-9_-]*")):
        if not isinstance(config.get(key), str) or not re.fullmatch(pattern, config[key]):
            raise ValueError("invalid SSH target")
    if type(config.get("port")) is not int or not 1 <= config["port"] <= 65535:
        raise ValueError("invalid SSH port")
    for key in ("reports_dir", "state_dir", "identity_file", "known_hosts"):
        if not isinstance(config.get(key), str) or not config[key]:
            raise ValueError("missing publisher path")
    return config


def publish(config_path: Path, *, min_avail_kb: int | None = None) -> dict:
    config = _load_config(config_path)
    group_id = config["group_id"]
    state_dir = Path(config["state_dir"])
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state_dir, 0o700)
    with (state_dir / "publish.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy"}
        reports_dir = Path(config["reports_dir"])
        source_stamp = hashlib.sha256(json.dumps([
            _source_stamp(reports_dir), group_id, config["host"], config["user"], config["port"],
            config["identity_file"], config["known_hosts"], reader_styles(), _PUBLISHER_CODE_SHA256], separators=(",", ":")).encode()).hexdigest()
        success = state_dir / "success.json"
        if success.exists():
            previous = json.loads(success.read_text(encoding="utf-8"))
            if previous.get("input_stamp") == source_stamp and previous.get("group_id") == group_id:
                return {"status": "unchanged", "files": len(previous.get("files", []))}
        if min_avail_kb is not None and min_avail_kb > 0:
            avail_kb = _mem_available_kb()
            if avail_kb is not None and avail_kb < min_avail_kb:
                return {
                    "status": "deferred",
                    "reason": "low_memory",
                    "available_kb": avail_kb,
                    "min_available_kb": min_avail_kb,
                }
        with tempfile.TemporaryDirectory(prefix=".qq-publish-", dir=state_dir) as temporary:
            stage = Path(temporary)
            files = build_bundle(reports_dir, group_id, stage)
            if not files:
                return {"status": "no_complete_reports", "files": 0}
            fingerprint = hashlib.sha256(json.dumps([
                (name, _file_sha256(stage / name)) for name in files
            ]).encode()).hexdigest()
            ssh = ["ssh", "-i", config["identity_file"], "-p", str(config["port"]),
                   "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                   "-o", "UserKnownHostsFile=" + config["known_hosts"], "-o", "ConnectTimeout=15"]
            target = config["user"] + "@" + config["host"] + ":."
            command = ["rsync", "-rlt", "--delay-updates", "--no-owner", "--no-group",
                       "--chmod=D2750,F640", "-e", shlex.join(ssh), str(stage) + "/", target]
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise RuntimeError("restricted QQ publication failed (exit " + str(result.returncode) + ")")
            temporary_state = success.with_suffix(".tmp")
            temporary_state.write_text(json.dumps({"sha256": fingerprint, "input_stamp": source_stamp,
                                                   "files": files, "group_id": group_id}),
                                       encoding="utf-8")
            os.chmod(temporary_state, 0o600)
            os.replace(temporary_state, success)
            return {"status": "published", "files": len(files), "group_id": group_id}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(publish(args.config, min_avail_kb=_configured_min_avail_kb()), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
