#!/usr/bin/env python3
"""Generate the configured conversation's brief and review reports using the pinned local runtime image."""
import argparse
import re
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from image_pipeline import process_window
from model_fallback import providers as model_providers

import settings
ROOT = Path(__file__).resolve().parents[1]
STALE_TEMP_PREFIXES = ('.digest-', '.images-', '.web-context-', '.prepared-images-', '.image-stage-')
STALE_TEMP_SECONDS = 6 * 3600


def _trigger_report_publish():
    try:
        subprocess.Popen(
            [sys.executable, str(ROOT / "deployment" / "publish_reports.py")],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)
    except OSError:
        # The independent minute cron can retry publication without rerunning generation.
        pass


def _sweep_stale_temp(work: Path) -> None:
    """A container killed by SIGKILL skips its cleanup; a run lasts at most 30 minutes, so 6 hours is stale."""
    now = time.time()
    for entry in work.iterdir() if work.is_dir() else []:
        try:
            if (entry.name.startswith(STALE_TEMP_PREFIXES) and not entry.is_symlink()
                    and now - entry.stat().st_mtime > STALE_TEMP_SECONDS):
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
        except OSError:
            pass


def _work_path(path: str, work: Path) -> str:
    return str(Path("/work") / Path(path).relative_to(work))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--start", type=int)
    parser.add_argument("--end", type=int)
    parser.add_argument("--request-id")
    parser.add_argument("--list-conversations", action="store_true")
    args = parser.parse_args()
    project = settings.load_project(required=not args.list_conversations)
    image = settings.runtime_image("runtime")
    export = settings.export_root(project)
    on_demand = args.request_id is not None
    if on_demand and (not re.fullmatch(r"[0-9a-f]{64}", args.request_id) or args.start is None or args.end is None or args.start >= args.end):
        raise SystemExit("request-id requires a lowercase SHA-256 ID and explicit start < end")
    if not on_demand and (args.start is not None or args.hours <= 0):
        raise SystemExit("hours must be positive; explicit start requires request-id")
    end = args.end if args.end is not None else int(time.time())
    common = ["docker", "run", "--rm", "--pull", "never", "--read-only", "--user",
              f"{project['worker_uid']}:{project['worker_gid']}", "--cap-drop", "ALL", "--security-opt",
              "no-new-privileges:true", "--memory", "1g", "--cpus", "1", "--tmpfs", "/tmp:rw,nosuid,size=64m",
              "--env", "PYTHONPATH=/source/agent_console:/tool",
              "--env", "CHAT_DIGEST_CONFIG=/runtime/project.json", "--env", "TZ=" + project["timezone"]]
    mounts = [(ROOT / "agent_console", "/source/agent_console", True),
              (ROOT / "deployment", "/tool", True), (settings.RUNTIME / "project.json", "/runtime/project.json", True),
              (export, "/export", True),
              (settings.RUNTIME / "digest", "/work", False), (settings.RUNTIME / "reports", "/reports", False)]
    for source, target, readonly in mounts:
        common += ["--mount", f"type=bind,src={source},dst={target}" + (",readonly" if readonly else "")]
    fonts = settings.RUNTIME / "report-fonts"
    if fonts.is_dir():
        common += ["--mount", f"type=bind,src={fonts},dst=/app/runtime/report-fonts,readonly"]
    window = ["--export-root", "/export", "--runtime-dir", "/work"]
    start = args.start if on_demand else end - int(args.hours * 3600)
    if on_demand:
        window += ["--start", str(args.start), "--end", str(end), "--request-id", args.request_id]
    else:
        window += ["--hours", str(args.hours), "--end", str(end)]
    if args.list_conversations:
        return subprocess.run(common + ["--network", "none", image, "/tool/digest.py", *window, "--list-conversations"],
                              timeout=120).returncode
    work = settings.RUNTIME / "digest"
    _sweep_stale_temp(work)
    stage = process_window(start, end, scope="on-demand" if on_demand else "daily")
    if stage["status"] != "complete":
        print(json.dumps({**stage, "status": "images_pending"}, ensure_ascii=False), flush=True)
        return 2
    window += ["--exclude-own-text-sha256", _work_path(stage["notice_hashes_path"], work),
               "--exclude-own-image-sha256", _work_path(stage["image_hashes_path"], work),
               "--exclude-message-ids", _work_path(stage["message_ids_path"], work)]
    models = ["--mount", f"type=bind,src={settings.RUNTIME / 'gemini-model.json'},dst=/models/gemini.json,readonly"]
    container = "chat-digest-run-" + uuid.uuid4().hex[:12]
    command = common + ["--name", container, "--network", project["docker_network"]] + models + [
        image, "/tool/digest.py", *window, "--output-dir", "/reports", "--model-config", "/models/gemini.json",
        "--image-understanding", _work_path(stage["understanding_path"], work),
        "--auxiliary-context", _work_path(stage["auxiliary_path"], work)]
    model_config = json.loads((settings.RUNTIME / "gemini-model.json").read_text(encoding="utf-8"))
    timeout = 900 * len(model_providers(model_config))  # every configured provider may be tried once
    try:
        result = subprocess.run(command, timeout=timeout)
    except subprocess.TimeoutExpired:
        # The docker client was killed, not the container; remove it so it stops holding memory.
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)
        print(json.dumps({"status": "failed", "stage": "digest", "error": f"digest container timed out after {timeout}s"}), flush=True)
        return 1
    if result.returncode == 0:
        _trigger_report_publish()
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
