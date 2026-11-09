#!/usr/bin/env python3
"""Initialize private runtime directories, pin local images, or run read-only preflight."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import settings


def _docker_image(reference: str) -> str:
    try:
        result = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", reference],
                                check=True, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Image {reference!r} is unavailable locally. Build first with `docker build -t chat-digest-runtime:2.0.0 .` (see docs/INSTALL.md), then run `python3 deployment/bootstrap.py --pin-images`; automatic pulls are disabled.") from exc
    image_id = result.stdout.strip()
    if not image_id.startswith("sha256:") or len(image_id) != 71:
        raise RuntimeError(f"Docker returned a non-immutable ID for image {reference!r}")
    return image_id


def _private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".image-pins-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        project = settings.load_project()
        _set_owner(Path(temporary), project["worker_uid"], project["worker_gid"])
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _set_owner(path: Path, uid: int, gid: int) -> None:
    if os.geteuid() == 0:
        os.chown(path, uid, gid)
    else:
        info = path.stat()
        if info.st_uid != uid or info.st_gid != gid:
            raise RuntimeError(f"Cannot give worker {uid}:{gid} access to {path}; run bootstrap as root or set project worker_uid/worker_gid to the current owner")


def init_runtime() -> None:
    runtime = settings.RUNTIME
    # Use project.json's worker user when it exists, so re-running init after setting worker_uid fixes ownership.
    first_run = not (runtime / "project.json").exists()
    configured = settings.DEFAULTS if first_run else settings.load_project()
    worker_uid = int(configured["worker_uid"])
    worker_gid = int(configured["worker_gid"])
    if first_run and os.geteuid() != 0:
        # An unprivileged first run cannot hand files to another user; it is the worker itself
        # (docs/INSTALL.md creates a dedicated system user, whose UID is rarely the default).
        worker_uid, worker_gid = os.geteuid(), os.getegid()
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime, 0o700)
    _set_owner(runtime, worker_uid, worker_gid)
    for name in ("digest", "reports"):
        directory = runtime / name
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        _set_owner(directory, worker_uid, worker_gid)
    fonts = runtime / "report-fonts"
    fonts.mkdir(parents=True, exist_ok=True, mode=0o755)
    project = runtime / "project.json"
    if not project.exists():
        example = settings.ROOT / "examples" / "project.example.json"
        if not example.is_file():
            raise RuntimeError(f"Configuration template is missing: {example}")
        template = json.loads(example.read_text(encoding="utf-8"))
        template.update(worker_uid=worker_uid, worker_gid=worker_gid)
        project.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(project, 0o600)
        _set_owner(project, worker_uid, worker_gid)
        created = True
    else:
        created = False
        if os.geteuid() == 0:
            os.chown(project, worker_uid, worker_gid)
        project.chmod(0o600)
        _set_owner(project, worker_uid, worker_gid)
    import local_site
    site = local_site.init_config(runtime / local_site.CONFIG_NAME, owner=(worker_uid, worker_gid))
    result = {"status": "initialized", "config_created": created, "runtime": str(runtime), "config": str(project),
              "next": "set export.root, group.conversation_id/name and send in runtime/project.json, build the runtime image, then run bootstrap --pin-images"}
    if site:
        result["report_site"] = {**site, "note": "shown only once; start the site with ./bin/chat-digest site --serve"}
    print(json.dumps(result, ensure_ascii=False))


def _configured_images(config: dict) -> dict[str, str]:
    images = config.get("images", {})
    required = ("runtime",)
    result = {}
    for name in required:
        reference = images.get(name)
        if not isinstance(reference, str) or not reference.strip() or reference.startswith("REPLACE_WITH_"):
            raise RuntimeError(f"Set images.{name} in runtime/project.json before image pinning")
        result[name] = reference
    return result


def pin_images(*, refresh: bool) -> None:
    config = settings.load_project()
    runtime = settings.RUNTIME
    pin_path = runtime / "image-pins.json"
    if pin_path.exists() and not refresh:
        raise RuntimeError("image-pins.json already exists; use --refresh-pins only for an intentional image upgrade")
    if not pin_path.exists() and refresh:
        raise RuntimeError("No existing image pins to refresh; use --pin-images for initial pinning")
    references = _configured_images(config)
    pins = {name: {"reference": reference, "id": _docker_image(reference)}
            for name, reference in references.items()}
    _private_json(pin_path, {"schema_version": 1, "pinned_at": int(time.time()), "images": pins})
    print(json.dumps({"status": "pinned", "pins_file": str(pin_path),
                      "images": {name: record["id"] for name, record in pins.items()}}, ensure_ascii=False))


def check() -> None:
    config = settings.load_project()
    pin_path = settings.RUNTIME / "image-pins.json"
    try:
        pins = json.loads(pin_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError("Image pins are missing; build the local runtime image, then run `python3 deployment/bootstrap.py --pin-images`.") from None
    if pins.get("schema_version") != 1 or not isinstance(pins.get("images"), dict):
        raise RuntimeError("image-pins.json has an unsupported schema; inspect it before explicit --refresh-pins")
    references = _configured_images(config)
    for name, reference in references.items():
        record = pins["images"].get(name)
        if not isinstance(record, dict) or record.get("reference") != reference:
            raise RuntimeError(f"Image pin for {name} is stale; run `python3 deployment/bootstrap.py --refresh-pins` only after intentional upgrade")
        pinned_id = record.get("id")
        if not isinstance(pinned_id, str) or not pinned_id.startswith("sha256:") or len(pinned_id) != 71:
            raise RuntimeError(f"Image pin for {name} is invalid")
        current = _docker_image(reference)
        if current != pinned_id:
            raise RuntimeError(f"Configured image tag {name} now resolves to a different ID; after intentional upgrade run `python3 deployment/bootstrap.py --refresh-pins`")
    for path in (settings.RUNTIME / "digest", settings.RUNTIME / "reports"):
        if not path.is_dir():
            raise RuntimeError(f"Required runtime directory is missing: {path}; initialize with `python3 deployment/bootstrap.py --init-runtime`")
    export = settings.export_root(config)
    print(json.dumps({"status": "ready", "runtime": str(settings.RUNTIME), "pins_file": str(pin_path),
                      "export_manifest_present": (export / "manifest.json").is_file()}, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--init-runtime", action="store_true", help="create private runtime directories and copy config hints")
    mode.add_argument("--pin-images", action="store_true", help="resolve locally built images and create immutable ID pins")
    mode.add_argument("--refresh-pins", action="store_true", help="explicitly refresh immutable IDs after intentional upgrade")
    parser.add_argument("--check", action="store_true", help="read-only preflight (default)")
    args = parser.parse_args()
    try:
        if args.init_runtime:
            init_runtime()
        elif args.pin_images:
            pin_images(refresh=False)
        elif args.refresh_pins:
            pin_images(refresh=True)
        else:
            check()
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"bootstrap: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
