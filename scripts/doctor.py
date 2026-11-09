#!/usr/bin/env python3
"""只读自检：依赖、权限、配置、导出、发送器和运行环境；不调用模型、不发送消息。

``--probe-send`` 仅按 chat-send/v1 约定执行发送器的 ``--dry-run``。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shlex
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
TIKTOKEN_FILE = "fb374d419588a4632f3f557e76b4b70aebbca790"  # tiktoken's o200k_base cache key
RESULTS: list[dict] = []


def command(*parts: object) -> str:
    """Quote every argument, including paths supplied through configuration/environment."""
    return shlex.join(str(part) for part in parts)


def cli_command(*parts: object) -> str:
    return command(ROOT / "bin" / "chat-digest", *parts)


def edit_command(path: Path) -> str:
    return command("vi", path.expanduser().absolute())


def project_path() -> Path:
    import settings
    return Path(os.environ.get("CHAT_DIGEST_CONFIG", settings.CONFIG_PATH)).expanduser().absolute()


def install_command() -> str:
    return command(ROOT / "install.sh", "--non-interactive")


def python_install_command() -> str:
    if sys.platform == "darwin":
        return "brew install python@3.11"
    if shutil.which("opkg"):
        return "opkg update && opkg install python3"
    return "sudo apt-get update && sudo apt-get install -y python3.11 python3.11-venv"


def report(name: str, status: str, detail: str, fix: str = "") -> None:
    RESULTS.append({"check": name, "status": status, "detail": detail, **({"fix": fix} if fix else {})})


def check_python() -> None:
    version = sys.version_info
    if version >= (3, 11):
        report("python", "ok", f"Python {version.major}.{version.minor}.{version.micro}")
    else:
        report("python", "fail", f"Python {version.major}.{version.minor} 太旧，运行需要 3.11 或更新版本", python_install_command())


def check_packages() -> None:
    for module, package in (("PIL", "Pillow"), ("pillow_heif", "pillow-heif"), ("tiktoken", "tiktoken"), ("websockets", "websockets")):
        try:
            importlib.import_module(module)
            report("package:" + package, "ok", "可以导入")
        except (ImportError, OSError) as exc:
            report("package:" + package, "fail", f"依赖缺失或无法加载：{type(exc).__name__}", install_command())


def check_tokenizer() -> None:
    cache = Path(os.environ.get("TIKTOKEN_CACHE_DIR") or ROOT / ".cache" / "tiktoken").expanduser().absolute()
    path = cache / TIKTOKEN_FILE
    warm = command("env", f"TIKTOKEN_CACHE_DIR={cache}", sys.executable, "-c",
                   "import tiktoken; tiktoken.get_encoding('o200k_base')")
    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        report("tokenizer-cache", "fail", f"缺少 o200k_base 分词数据：{cache}（修复需联网）", warm)
        return
    except OSError as exc:
        report("tokenizer-cache", "fail", f"无法读取分词缓存：{path}（{type(exc).__name__}）", command("chmod", "u+rX", cache, path))
        return
    if hashlib.sha256(payload).hexdigest() != TIKTOKEN_SHA256:
        backup = shlex.quote(str(path) + ".corrupt.XXXXXX")
        fix = f"backup=$(mktemp {backup}) && {command('mv', '--', path)} \"$backup\" && {warm}"
        report("tokenizer-cache", "fail", "o200k_base 分词数据校验失败；先备份坏缓存再重新下载", fix)
    else:
        report("tokenizer-cache", "ok", f"分词数据校验通过：{cache}")


def check_fonts() -> None:
    fonts = ROOT / "assets" / "fonts"
    missing = [name for name in ("ResourceHanRoundedCN-Regular.ttf", "ResourceHanRoundedCN-Bold.ttf") if not (fonts / name).is_file()]
    # Ask for an unpacked release, never guess/download an archive or overwrite an existing font.
    copies = " && ".join(f'cp -n -- "$release/assets/fonts/{name}" {shlex.quote(str(fonts / name))}' for name in missing)
    fix = f"printf '请输入已解压的完整发行包目录：' && IFS= read -r release && {command('mkdir', '-p', fonts)} && {copies}" if missing else ""
    report("fonts", "fail" if missing else "ok", "缺少字体：" + ", ".join(missing) if missing else "中文字体齐全", fix)


def _private(path: Path, name: str, *, required: bool) -> bool:
    path = path.expanduser().absolute()
    try:
        info = path.stat()
    except FileNotFoundError:
        fix = cli_command("init") if name == "runtime-dir" else edit_command(path)
        if name == "project.json":
            fix = command(ROOT / "install.sh")
        if name == "model-config":
            fix = command(ROOT / "install.sh")
        report(name, "fail" if required else "warn", f"文件或目录不存在：{path}", fix)
        return False
    except OSError as exc:
        report(name, "fail", f"无法检查权限：{path}（{type(exc).__name__}），请用文件属主运行修复", command("chmod", "u+rX", path.parent, path))
        return False
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077:
        report(name, "fail", f"{path} 权限为 {mode:o}，其他用户可访问私有数据", command("chmod", "700" if stat.S_ISDIR(info.st_mode) else "600", path))
        return False
    report(name, "ok", f"{path} 权限为 {mode:o}")
    return True


def check_runtime(config_path: Path | None) -> dict | None:
    import settings
    _private(settings.RUNTIME, "runtime-dir", required=True)
    path = config_path.expanduser().absolute() if config_path else project_path()
    os.environ["CHAT_DIGEST_CONFIG"] = str(path)
    if not _private(path, "project.json", required=True):
        return None
    try:
        project = settings.load_project(required=True)
    except (ValueError, OSError) as exc:
        report("project-config", "fail", f"项目配置无效或不可读：{exc}", edit_command(path))
        return None
    report("project-config", "ok", f"会话：{project['group']['conversation_id']}")
    return project


def check_timezone(project: dict) -> None:
    import timezones
    name = project.get("timezone", "Asia/Shanghai")
    try:
        timezones.project_zone(name)
    except ValueError as exc:
        report("timezone", "fail", f"时区不可用：{exc}；安装数据库后，若仍失败请修正 timezone", command(sys.executable, "-m", "pip", "install", "tzdata") + " && " + edit_command(project_path()))
        return
    report("timezone", "ok", f"{name}：使用固定偏移（宿主无时区数据库）" if timezones.uses_fixed_offset(name) else name)


def check_export(project: dict) -> None:
    import chat_export
    import settings
    try:
        root = settings.export_root(project)
    except ValueError as exc:
        report("export", "fail", f"请设置 export.root：{exc}", edit_command(project_path()))
        return
    listing = cli_command("list-conversations", "--export-root", root)
    if not os.access(root, os.R_OK | os.X_OK):
        report("export", "fail", f"当前用户无法读取导出目录：{root}；请由导出目录属主运行，不要开放给所有用户", command("chmod", "u+rX", root))
        return
    try:
        manifest = chat_export.load_manifest(root)
        conversation = chat_export.find_conversation(manifest, project["group"]["conversation_id"])
        mode = stat.S_IMODE(root.stat().st_mode)
    except (chat_export.ExportError, OSError) as exc:
        report("export", "fail", f"导出或会话不可用：{exc}；列出会话后核对配置，并检查导出工具", listing + " ; " + edit_command(project_path()))
        return
    if mode & 0o007:
        report("export-permissions", "warn", f"导出目录权限 {mode:o} 允许其他用户访问", command("chmod", "700", root))
    last = conversation.get("last_ts")
    age = time.time() - last if isinstance(last, int) else None
    if age is not None and age > 36 * 3600:
        report("export", "warn", f"最后一条消息距今 {int(age // 3600)} 小时；可能没有新消息，也可能导出工具已停止。先核对时间，再到导出工具恢复导出", listing)
    else:
        report("export", "ok", f"{manifest['source']['platform']} 导出中找到会话，last_ts={last}")


def check_model() -> None:
    import settings
    from model_fallback import providers
    path = settings.RUNTIME / "gemini-model.json"
    if not _private(path, "model-config", required=False):
        return
    try:
        chain = providers(json.loads(path.read_text(encoding="utf-8")))
        report("model-chain", "ok", " -> ".join(provider["model"] for provider in chain))
    except (ValueError, OSError) as exc:
        report("model-chain", "fail", f"模型配置无效或不可读：{exc}", edit_command(path))


def check_sender(project: dict, probe: bool) -> None:
    import chat_send
    try:
        config = chat_send.send_config(project)
    except chat_send.SendConfigError as exc:
        report("sender", "warn", f"发送配置未就绪（仅投递需要）：{exc}", edit_command(project_path()))
        return
    executable = config["command"][0]
    if shutil.which(executable) is None:
        report("sender", "fail", f"发送命令不存在或不可执行：{executable}；请把 send.command 改为已安装的 chat-send/v1 发送器绝对路径", edit_command(project_path()))
        return
    if not probe:
        report("sender", "ok", f"找到发送命令：{executable}（--probe-send 可做不提交的 dry run）")
        return
    result = chat_send.send(config, rid=chat_send.request_id("doctor", str(time.time())), text="chat-digest doctor dry run", dry_run=True)
    if result["status"] == "dry_run":
        report("sender", "ok", f"发送器 dry run 通过：{result.get('detail', result['status'])}")
    else:
        report("sender", "fail", f"发送器 dry run 失败：{result['status']}：{result.get('reason') or result.get('detail')}；检查发送器登录和目标配置", edit_command(project_path()) + " && " + cli_command("doctor", "--probe-send"))


def check_render(project: dict, import_module: bool) -> None:
    import render_plugin
    fix = edit_command(project_path())
    try:
        config = render_plugin.normalize(project.get("render"))
    except ValueError as exc:
        report("renderer", "fail", f"渲染配置无效：{exc}", fix)
        return
    if config is None:
        report("renderer", "ok", "使用自带渲染器")
        return
    path = Path(str(config["module"])).expanduser()
    if not path.is_absolute() or not path.is_file():
        report("renderer", "fail", f"render.module 必须是存在的渲染器 .py 文件绝对路径：{path}", fix)
        return
    if not import_module:
        report("renderer", "ok", f"宿主渲染器文件存在：{path}（模块将在运行时容器内加载）")
        return
    try:
        import daily_report
        render_plugin.load(project.get("render"), daily_report)
        report("renderer", "ok", f"外部渲染器已加载：{path}")
    except ImportError as exc:
        if not exc.name or exc.name.split(".", 1)[0] not in {"PIL", "pillow_heif", "tiktoken", "websockets"}:
            raise
        report("renderer", "fail", f"渲染依赖缺失或无法加载：{exc.name}", install_command())
    except ValueError as exc:
        report("renderer", "fail", f"渲染器加载失败，请核对 module/function/html_function：{exc}", fix)


def check_site() -> None:
    import local_site
    path = local_site.config_path()
    if not path.is_file():
        report("site", "warn", f"未配置本机报告网站：{path}（远端发布不需要）", cli_command("site", "--config", path, "--init"))
        return
    try:
        config = local_site.load_config(path)
        mode = path.stat().st_mode
    except (OSError, ValueError) as exc:
        report("site", "fail", f"网站配置无效或不可读：{path}：{exc}", edit_command(path))
        return
    if mode & 0o077:
        report("site", "warn", f"网站配置允许其他用户读取：{path}", command("chmod", "600", path))
    report("site", "ok", f"网站监听 127.0.0.1:{config['port']}；启动：{cli_command('site', '--serve')}")


def check_docker(project: dict) -> None:
    if shutil.which("docker") is None:
        fix = "brew install --cask docker" if sys.platform == "darwin" else "sudo apt-get update && sudo apt-get install -y docker.io"
        if shutil.which("opkg"):
            fix = "opkg update && opkg install dockerd docker"
        report("docker", "warn", "未找到 Docker；定时生成需要 Docker Engine 24+，请使用对应系统的软件包管理器安装", fix)
        return
    try:
        subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], check=True, capture_output=True, text=True, timeout=20)
    except (subprocess.SubprocessError, OSError):
        fix = "open -a Docker" if sys.platform == "darwin" else "sudo systemctl start docker"
        report("docker", "fail", "Docker 服务不可达；启动服务后，用有权限访问 Docker 的用户重试（不要 chmod 666 socket）", fix)
        return
    import settings
    try:
        settings.runtime_image("runtime")
        report("runtime-image", "ok", "运行时镜像已固定")
    except (ValueError, OSError) as exc:
        pins = settings.RUNTIME / "image-pins.json"
        flag = "--refresh-pins" if pins.exists() else "--pin-images"
        image = project.get("images", {}).get("runtime", "chat-digest-runtime:2.0.0")
        fix = command("docker", "build", "-t", image, ROOT) + " && " + command(sys.executable, ROOT / "deployment" / "bootstrap.py", flag)
        report("runtime-image", "fail", f"运行时镜像锁定无效：{exc}；确认有意构建/升级后执行", fix)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, help="要检查的 project.json（默认 runtime/project.json）")
    parser.add_argument("--demo-only", action="store_true", help="仅检查离线演示需要的项目")
    parser.add_argument("--host-only", action="store_true", help="仅检查 Docker 宿主：跳过容器内的 Python 包和分词数据")
    parser.add_argument("--no-docker", action="store_true", help="跳过 Docker 检查")
    parser.add_argument("--probe-send", action="store_true", help="仅执行发送器 dry run，绝不提交消息")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)
    RESULTS.clear()
    check_python()
    if not args.host_only:
        check_packages()
        check_tokenizer()
    check_fonts()
    if not args.demo_only:
        project = check_runtime(args.config)
        if project:
            check_timezone(project)
            check_export(project)
            check_model()
            check_sender(project, args.probe_send)
            check_render(project, import_module=not args.host_only)
            check_site()
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
                print(f"       修复命令：{item['fix']}")
        print("doctor: " + ("所有必需检查通过" if not failed else f"{len(failed)} 项检查失败"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
