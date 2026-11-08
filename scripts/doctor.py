#!/usr/bin/env python3
"""Read-only self-check: dependencies, permissions, configuration, export and sender reachability.

Nothing is sent and no model is called. ``--probe-send`` runs the sender once with ``--dry-run``, which by the
chat-send/v1 contract only locates the target and validates input.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deployment"))
sys.path.insert(0, str(ROOT / "agent_console"))
TIKTOKEN_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
TIKTOKEN_FILE = "fb374d419588a4632f3f557e76b4b70aebbca790"  # sha1 of the o200k_base URL, tiktoken's cache key
RESULTS: list[dict] = []


def report(name: str, status: str, detail: str, fix: str = "") -> None:
    RESULTS.append({"check": name, "status": status, "detail": detail, **({"fix": fix} if fix else {})})


def check_python() -> None:
    version = sys.version_info
    if version >= (3, 11):
        report("python", "ok", f"Python {version.major}.{version.minor}.{version.micro}")
    else:
        report("python", "fail", f"Python {version.major}.{version.minor} is too old", "install Python 3.11 or newer")


def check_packages() -> None:
    for module, package in (("PIL", "Pillow"), ("pillow_heif", "pillow-heif"), ("tiktoken", "tiktoken"), ("websockets", "websockets")):
        try:
            importlib.import_module(module)
            report("package:" + package, "ok", "importable")
        except Exception as exc:  # noqa: BLE001 - report every import problem
            report("package:" + package, "fail", type(exc).__name__,
                   "run ./scripts/install.sh --apply, or: .venv/bin/pip install -r requirements.txt")


def check_tokenizer() -> None:
    cache = os.environ.get("TIKTOKEN_CACHE_DIR") or str(ROOT / ".cache" / "tiktoken")
    path = Path(cache) / TIKTOKEN_FILE
    if not path.is_file():
        report("tokenizer-cache", "fail", f"o200k_base data missing in {cache}",
               "run once with network: TIKTOKEN_CACHE_DIR=.cache/tiktoken .venv/bin/python -c \"import tiktoken; tiktoken.get_encoding('o200k_base')\"")
        return
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != TIKTOKEN_SHA256:
        report("tokenizer-cache", "fail", "o200k_base data failed its integrity check", "delete the cached file and prewarm again")
    else:
        report("tokenizer-cache", "ok", f"verified in {cache}")


def check_fonts() -> None:
    fonts = ROOT / "assets" / "fonts"
    wanted = ["ResourceHanRoundedCN-Regular.ttf", "ResourceHanRoundedCN-Bold.ttf"]
    missing = [name for name in wanted if not (fonts / name).is_file()]
    report("fonts", "fail" if missing else "ok", "missing " + ", ".join(missing) if missing else "Resource Han Rounded present",
           "restore assets/fonts from the release package" if missing else "")


def _private(path: Path, name: str, *, required: bool) -> bool:
    if not path.exists():
        report(name, "fail" if required else "warn", f"{path} does not exist",
               "python3 deployment/bootstrap.py --init-runtime" if required else "")
        return False
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        report(name, "fail", f"{path} has mode {mode:o}; group/other can read private data", f"chmod {'700' if path.is_dir() else '600'} {path}")
        return False
    report(name, "ok", f"{path} mode {mode:o}")
    return True


def check_runtime(project_path: Path | None) -> dict | None:
    import settings
    runtime = settings.RUNTIME
    _private(runtime, "runtime-dir", required=True)
    config_path = project_path or (Path(os.environ["CHAT_DIGEST_CONFIG"]) if os.environ.get("CHAT_DIGEST_CONFIG") else settings.CONFIG_PATH)
    if not _private(config_path, "project.json", required=True):
        return None
    try:
        os.environ["CHAT_DIGEST_CONFIG"] = str(config_path)
        project = settings.load_project(required=True)
        report("project-config", "ok", f"conversation {project['group']['conversation_id']}")
        return project
    except ValueError as exc:
        report("project-config", "fail", str(exc), "edit runtime/project.json; see docs/CONFIGURATION.md")
        return None


def check_export(project: dict) -> None:
    import chat_export
    import settings
    try:
        root = settings.export_root(project)
    except ValueError as exc:
        report("export", "fail", str(exc), "set export.root in runtime/project.json")
        return
    if not os.access(root, os.R_OK | os.X_OK):
        report("export", "fail", f"{root} is not readable by this user", "grant read access (the exporter should keep it 700 and owned by the same user)")
        return
    try:
        manifest = chat_export.load_manifest(root)
        conversation = chat_export.find_conversation(manifest, project["group"]["conversation_id"])
    except (chat_export.ExportError, OSError) as exc:
        report("export", "fail", str(exc), "python3 deployment/chat_export.py --export-root <dir> list")
        return
    mode = stat.S_IMODE(root.stat().st_mode)
    if mode & 0o007:
        report("export-permissions", "warn", f"export root mode {mode:o} is readable by others", f"chmod 700 {root}")
    last = conversation.get("last_ts")
    age = time.time() - last if isinstance(last, int) else None
    if age is not None and age > 36 * 3600:
        report("export", "warn", f"last message in the export is {int(age // 3600)} h old",
               "check that the exporter is still running and appending")
    else:
        report("export", "ok", f"{manifest['source']['platform']} export, conversation found, last_ts={last}")


def check_model() -> None:
    import settings
    from model_fallback import providers
    path = settings.RUNTIME / "gemini-model.json"
    if not _private(path, "model-config", required=False):
        report("model-config", "warn", "no model configuration; only the offline demo can run",
               "cp examples/gemini-model.example.json runtime/gemini-model.json && chmod 600 runtime/gemini-model.json")
        return
    try:
        chain = providers(json.loads(path.read_text(encoding="utf-8")))
        report("model-chain", "ok", " -> ".join(provider["model"] for provider in chain))
    except (ValueError, OSError) as exc:
        report("model-chain", "fail", str(exc), "fix runtime/gemini-model.json; see docs/CONFIGURATION.md")


def check_sender(project: dict, probe: bool) -> None:
    import chat_send
    try:
        config = chat_send.send_config(project)
    except chat_send.SendConfigError as exc:
        report("sender", "warn", str(exc), "set send.command and send.to (only needed for delivery)")
        return
    executable = config["command"][0]
    if shutil.which(executable) is None and not Path(executable).is_file():
        report("sender", "fail", f"command not found: {executable}", "install wechat-export-send or another chat-send/v1 provider")
        return
    if not probe:
        report("sender", "ok", f"command found: {executable} (use --probe-send for a dry run)")
        return
    result = chat_send.send(config, rid=chat_send.request_id("doctor", str(time.time())), text="chat-digest doctor dry run", dry_run=True)
    if result["status"] == "dry_run":
        report("sender", "ok", f"dry run answered: {result.get('detail', result['status'])}")
    else:
        report("sender", "fail", f"dry run status {result['status']}: {result.get('reason') or result.get('detail')}",
               "run the sender command by hand with --dry-run --json; see docs/TROUBLESHOOTING.md")


def check_docker(project: dict) -> None:
    if shutil.which("docker") is None:
        report("docker", "warn", "docker not found; scheduled processing needs it", "install Docker Engine 24+ (see docs/INSTALL.md)")
        return
    try:
        subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], check=True, capture_output=True, text=True, timeout=20)
    except (subprocess.SubprocessError, OSError):
        report("docker", "fail", "docker daemon not reachable by this user", "start Docker or run as a user in the docker group")
        return
    import settings
    try:
        settings.runtime_image("runtime")
        report("runtime-image", "ok", "pinned image present")
    except ValueError as exc:
        report("runtime-image", "fail", str(exc), "docker build -t chat-digest-runtime:2.0.0 . && python3 deployment/bootstrap.py --pin-images")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, help="project.json to check (default: runtime/project.json)")
    parser.add_argument("--demo-only", action="store_true", help="only check what the offline demo needs")
    parser.add_argument("--host-only", action="store_true",
                        help="Docker-only host: skip Python package and tokenizer checks (they live in the runtime image)")
    parser.add_argument("--no-docker", action="store_true", help="skip Docker checks")
    parser.add_argument("--probe-send", action="store_true", help="run the sender once with --dry-run (never submits)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    check_python()
    if not args.host_only:
        check_packages()
        check_tokenizer()
    check_fonts()
    if not args.demo_only:
        project = check_runtime(args.config)
        if project:
            check_export(project)
            check_model()
            check_sender(project, args.probe_send)
            if not args.no_docker:
                check_docker(project)
    failed = [item for item in RESULTS if item["status"] == "fail"]
    if args.json:
        print(json.dumps({"ok": not failed, "checks": RESULTS}, ensure_ascii=False, indent=2))
    else:
        marks = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}
        for item in RESULTS:
            print(f"{marks[item['status']]} {item['check']}: {item['detail']}")
            if item.get("fix"):
                print(f"       fix: {item['fix']}")
        print("doctor: " + ("all required checks passed" if not failed else f"{len(failed)} check(s) failed"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
