#!/usr/bin/env python3
"""Chinese first-run wizard. Answer files are data, never executable shell scripts."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
KEYS = {"MODE", "EXPORT_ROOT", "CONVERSATION_ID", "GROUP_NAME", "MODEL_BASE_URL", "MODEL_API_KEY", "MODEL_NAME", "SITE_PASSWORD"}


def read_answers(path: Path) -> dict[str, str]:
    answers = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, raw = line.partition("=")
        key = key.strip()
        if not separator or key not in KEYS or key in answers:
            raise ValueError(f"答案文件第 {number} 行：键名未知、重复或缺少等号（见 examples/install.env.example）")
        # shlex only removes quotes; it never expands variables or runs commands.
        values = shlex.split(raw, comments=True)
        if len(values) > 1:
            raise ValueError(f"答案文件第 {number} 行：含空格的值请用引号包住")
        answers[key] = values[0] if values else ""
    return answers


def preflight() -> None:
    print("=== 环境检查（不会自动修改系统或启动服务） ===", flush=True)
    distro = platform.system()
    os_release = Path("/etc/os-release")
    if os_release.is_file():
        for line in os_release.read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                distro = line.partition("=")[2].strip('"')
    print(f"系统：{distro}；内核：{platform.release()}；Python：{platform.python_version()}")
    print("Python 检查：3.10+ 可运行向导；完整依赖和日报运行要求 Python 3.11+。")
    if sys.version_info < (3, 11):
        print("修复：sudo apt-get update && sudo apt-get install -y python3.11 python3.11-venv")
    if not shutil.which("docker"):
        print("Docker 未安装（离线演示不需要）；Debian/Ubuntu 修复：sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2")
        print("OpenWrt：opkg update && opkg install dockerd docker docker-compose")
    else:
        try:
            engine = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=20)
            print("Docker：" + (engine.stdout.strip() or "无法读取版本；检查：docker --version"))
            compose = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, timeout=20)
            print("Docker Compose：" + compose.stdout.strip() if compose.returncode == 0 else "缺少 Compose；Debian/Ubuntu 修复：sudo apt-get install -y docker-compose-v2")
        except (OSError, subprocess.SubprocessError):
            print("Docker/Compose 检查失败（不影响离线演示）；检查：docker --version && docker compose version")
    memory = Path("/proc/meminfo")
    if memory.is_file():
        available = next((int(line.split()[1]) // 1024 for line in memory.read_text().splitlines() if line.startswith("MemAvailable:")), 0)
        print(f"可用内存：{available} MiB（建议至少 2048 MiB）")
        if available < 2048:
            print("内存不足建议先关闭其他任务；检查：free -h；离线演示可在另一台机器运行。")
    else:
        print("可用内存：当前系统无 /proc/meminfo；Linux 可复制：free -h")
    print("KVM：" + ("可用" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "不可用（chat-digest 不需要 KVM，不影响安装）"))
    print("若另行运行虚拟机，可先检查：ls -l /dev/kvm；本项目只处理现有导出目录。")
    free = shutil.disk_usage(ROOT).free // (1024 * 1024)
    print(f"可用磁盘：{free} MiB（建议至少 4096 MiB）")
    if free < 4096:
        print("请换到空间充足目录，先检查：df -h .")


def collect() -> dict[str, str]:
    print("1. 离线演示：约 5 分钟，无需账号、API key 或 Docker\n2. 真实部署：需要聊天导出目录、模型 API 和 Docker")
    choice = input("选择 [1/2，默认 1]：").strip() or "1"
    if choice not in {"1", "2"}:
        raise ValueError("请选择 1 或 2；重新运行 ./install.sh")
    answers = {"MODE": "demo" if choice == "1" else "real"}
    if choice == "2":
        for key, prompt in (("EXPORT_ROOT", "export.root（聊天导出的绝对目录）"),
                            ("CONVERSATION_ID", "要总结的群 conversation_id"),
                            ("GROUP_NAME", "群显示名称"),
                            ("MODEL_BASE_URL", "OpenAI 兼容 API base_url（含 /v1）"),
                            ("MODEL_NAME", "模型名称")):
            answers[key] = input(prompt + "：").strip()
        answers["MODEL_API_KEY"] = getpass.getpass("模型 API key（不回显）：")
    answers["SITE_PASSWORD"] = getpass.getpass("本机网站密码（至少 8 位；留空自动生成，初始化后仅显示一次）：")
    return answers


def validate(answers: dict[str, str]) -> str:
    mode = answers.get("MODE", "demo")
    if mode not in {"demo", "real"}:
        raise ValueError("MODE 只能是 demo 或 real")
    password = answers.get("SITE_PASSWORD", "")
    if password and len(password) < 8:
        raise ValueError("SITE_PASSWORD 至少 8 位；修复：编辑答案文件后重新运行 ./install.sh --config <文件>")
    if mode == "real":
        required = ("EXPORT_ROOT", "CONVERSATION_ID", "GROUP_NAME", "MODEL_BASE_URL", "MODEL_API_KEY", "MODEL_NAME")
        missing = [key for key in required if not answers.get(key)]
        if missing:
            raise ValueError("真实部署缺少答案项：" + ", ".join(missing))
        if not Path(answers["EXPORT_ROOT"]).is_absolute():
            raise ValueError("EXPORT_ROOT 必须是绝对路径")
        address = urlsplit(answers["MODEL_BASE_URL"])
        if address.scheme not in {"http", "https"} or not address.netloc or address.username or address.password:
            raise ValueError("MODEL_BASE_URL 必须是 http(s) URL，不能嵌入账号或密钥")
    return mode


def private_json(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".install-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def initialize(answers: dict[str, str], mode: str) -> None:
    sys.path.insert(0, str(ROOT / "deployment"))
    import bootstrap
    import local_site
    import qq_report_site
    import settings

    runtime = settings.RUNTIME
    existing = (runtime / "project.json").is_file()
    # New installations use the invoking user, including root on appliance hosts.
    if not existing:
        runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        template = json.loads((ROOT / "examples/project.example.json").read_text(encoding="utf-8"))
        template.update(worker_uid=os.geteuid(), worker_gid=os.getegid())
        private_json(runtime / "project.json", template)
    project_path = runtime / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    if mode == "real":
        project["export"] = {**project.get("export", {}), "root": answers["EXPORT_ROOT"]}
        project["group"] = {**project.get("group", {}), "conversation_id": answers["CONVERSATION_ID"], "name": answers["GROUP_NAME"]}
    elif not existing:
        project["export"] = {"root": str(ROOT / "examples/synthetic-export")}
        project["group"] = {"conversation_id": "g_demo_garden", "name": "合成示例群（虚构）"}
    private_json(project_path, project)
    if answers.get("SITE_PASSWORD"):
        site_path = runtime / local_site.CONFIG_NAME
        local_site.init_config(site_path)
        site = json.loads(site_path.read_text(encoding="utf-8"))
        site["password_scrypt"] = qq_report_site.hash_password(answers["SITE_PASSWORD"])
        private_json(site_path, site)
    # Bootstrap reuses configured ownership and creates private report/cache directories.
    bootstrap.init_runtime()
    model_path = runtime / "gemini-model.json"
    if mode == "real":
        private_json(model_path, {"base_url": answers["MODEL_BASE_URL"], "model": answers["MODEL_NAME"], "api_key": answers["MODEL_API_KEY"]})
    elif not model_path.exists():
        private_json(model_path, json.loads((ROOT / "examples/gemini-model.example.json").read_text()))
    model_path.chmod(0o600)
    (runtime / local_site.CONFIG_NAME).chmod(0o600)
    # Replacement files must remain accessible to an explicitly configured worker.
    if os.geteuid() == 0:
        for path in (project_path, model_path, runtime / local_site.CONFIG_NAME):
            os.chown(path, int(project["worker_uid"]), int(project["worker_gid"]))
    print("配置已写入 runtime/（目录 700，配置文件 600）；答案文件含密钥，请保持 chmod 600。", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="读取 KEY=value 答案文件（不执行 shell）")
    parser.add_argument("--non-interactive", action="store_true", help="不提问，安装依赖并初始化默认演示模板")
    args, legacy = parser.parse_known_args(argv)
    preflight()
    if args.config:
        answers = read_answers(args.config)
    elif args.non_interactive:
        answers = {"MODE": "demo"}
    elif sys.stdin.isatty():
        answers = collect()
    else:
        raise ValueError("没有交互终端；请运行 ./install.sh --non-interactive 或 ./install.sh --config <答案文件>")
    mode = validate(answers)
    # Preserve legacy arguments; first-run defaults apply unless explicitly previewing.
    if "--apply" not in legacy and "--dry-run" not in legacy:
        legacy.insert(0, "--apply")
    result = subprocess.run([str(ROOT / "scripts/install.sh"), *legacy], cwd=ROOT)
    if result.returncode or "--dry-run" in legacy:
        return result.returncode
    initialize(answers, mode)
    no_venv = "--no-venv" in legacy
    environment = os.environ.copy()
    environment["CHAT_DIGEST_PYTHON"] = sys.executable
    if not no_venv and (ROOT / ".venv/bin/python").exists():
        environment["CHAT_DIGEST_PYTHON"] = str(ROOT / ".venv/bin/python")
    command = [str(ROOT / "bin/chat-digest"), "doctor"]
    if mode == "demo":
        command.append("--demo-only")
    if no_venv:
        command.append("--host-only")
    doctor = subprocess.run(command, cwd=ROOT, env=environment)
    if mode == "demo" and not no_venv and doctor.returncode == 0:
        demo = subprocess.run([str(ROOT / "bin/chat-digest"), "demo", "--output", str(ROOT / "chat-digest-demo")], cwd=ROOT, env=environment)
        if demo.returncode:
            return demo.returncode
    print("\n下一步：", flush=True)
    if mode == "demo":
        print("./bin/chat-digest demo --output ./chat-digest-demo\n打开 chat-digest-demo/index.html 和其中日报 PNG（全部为合成数据）。")
        if no_venv:
            print("宿主机已跳过依赖。请在普通 Linux 机器运行 ./install.sh --non-interactive 体验演示。")
    else:
        print("docker build -t chat-digest-runtime:2.0.0 .\n./bin/chat-digest pin-images\n./bin/chat-digest doctor --host-only\n./bin/chat-digest run --hours 24\n./bin/chat-digest site --serve")
        print("网站：http://127.0.0.1:8787/；用户名 reader。只在本机监听；远程访问见 docs/QUICKSTART.md。")
    print("不会启动常驻服务，不会发送聊天消息。doctor 未通过时请先执行上方修复命令；详见 docs/QUICKSTART.md。")
    return doctor.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # Do not print answer values or API credentials from arbitrary exception strings.
        print(f"安装未完成（{type(exc).__name__}）：" + (str(exc) if isinstance(exc, ValueError) else "请检查文件权限、Python 和网络。"), file=sys.stderr)
        print("修复：./install.sh --help；答案示例：cat examples/install.env.example", file=sys.stderr)
        raise SystemExit(1)
