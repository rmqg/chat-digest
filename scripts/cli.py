#!/usr/bin/env python3
"""CLI dispatch with targeted, Chinese setup diagnostics; programming errors still propagate."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import runpy
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {
    "demo": ("scripts/generate-demo.py", []),
    "doctor": ("scripts/doctor.py", []),
    "init": ("deployment/bootstrap.py", ["--init-runtime"]),
    "pin-images": ("deployment/bootstrap.py", ["--pin-images"]),
    "check": ("deployment/bootstrap.py", ["--check"]),
    "list-conversations": ("deployment/chat_export.py", []),
    "check-export": ("deployment/chat_export.py", []),
    "run": ("deployment/run-digest.py", []),
    "daily-tick": ("deployment/daily-job.py", ["--tick"]),
    "prepare-only": ("deployment/daily-job.py", ["--prepare-only"]),
    "on-demand": ("deployment/on-demand.py", []),
    "watchdog": ("deployment/daily-watchdog.py", []),
    "qq": ("deployment/qq_service.py", []),
    "site": ("deployment/local_site.py", []),
}
DEPENDENCIES = {"PIL", "pillow_heif", "tiktoken", "websockets"}
# Only errors raised by known input-validation functions are setup errors. A ValueError
# elsewhere (including inside an external renderer) must retain its traceback.
CONFIG_SOURCES = {
    "settings.py": {"load_project", "runtime_image", "export_root", "bot_settings", "conversation_ids"},
    "model_fallback.py": {"providers"},
    "chat_send.py": {"send_config"},
    "timezones.py": {"project_zone"},
    "render_plugin.py": {"normalize"},
}


def command(*parts: object) -> str:
    return shlex.join(str(part) for part in parts)


def runtime_path() -> Path:
    root = Path(os.environ.get("CHAT_DIGEST_ROOT", ROOT)).expanduser()
    return Path(os.environ.get("CHAT_DIGEST_RUNTIME_ROOT", root / "runtime")).expanduser().absolute()


def config_path() -> Path:
    return Path(os.environ.get("CHAT_DIGEST_CONFIG", runtime_path() / "project.json")).expanduser().absolute()


def hint(title: str, repair: str) -> None:
    print(f"chat-digest：{title}\n修复命令：{repair}", file=sys.stderr)


def setup_hint(cmd: str, args: list[str]) -> None:
    launcher = ROOT / "bin" / "chat-digest"
    if cmd == "doctor":
        return  # doctor owns its structured output and per-check repair suggestions
    if cmd == "demo":
        hint("演示未完成，请先检查依赖和分词数据；自检不会调用模型或发送消息。",
             command(launcher, "doctor", "--demo-only"))
    elif cmd == "site":
        path = Path(os.environ.get("CHAT_DIGEST_SITE_CONFIG", runtime_path() / "local-site.json"))
        for index, arg in enumerate(args):
            if arg == "--config" and index + 1 < len(args):
                path = Path(args[index + 1])
            elif arg.startswith("--config="):
                path = Path(arg.split("=", 1)[1])
        path = path.expanduser().absolute()
        repair = command("vi", path) if path.exists() else command(launcher, "site", "--config", path, "--init")
        hint("网站未启动或参数/配置有误；先检查配置、端口占用和目录权限，不要删除现有密码配置。", repair)
    else:
        hint("命令未完成。请检查参数、项目配置和宿主环境；自检不会调用模型或发送消息。",
             command(launcher, "doctor", "--host-only"))
        hint("项目配置可在编辑器中修正；尚未安装时先运行根目录安装器。",
             command("vi", config_path()) if config_path().exists() else command(ROOT / "install.sh"))


def is_config_error(exc: ValueError) -> bool:
    tb = exc.__traceback__
    while tb and tb.tb_next:
        tb = tb.tb_next
    if tb is None:
        return False
    frame = tb.tb_frame
    source = Path(frame.f_code.co_filename).resolve()
    return source.parent == ROOT / "deployment" and frame.f_code.co_name in CONFIG_SOURCES.get(source.name, set())


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd, *args = argv
    if sys.version_info < (3, 11):
        hint("运行需要 Python 3.11 或更新版本；安装后重新运行安装器。",
             "brew install python@3.11" if sys.platform == "darwin" else
             "sudo apt-get update && sudo apt-get install -y python3.11 python3.11-venv")
        return 1
    script, prefix = COMMANDS[cmd]
    suffix = ["list"] if cmd == "list-conversations" else []
    sys.path.insert(0, str(ROOT / "deployment"))
    sys.path.insert(0, str(ROOT / "agent_console"))
    previous_argv = sys.argv
    sys.argv = [str(ROOT / script), *prefix, *args, *suffix]
    try:
        runpy.run_path(str(ROOT / script), run_name="__main__")
    except ImportError as exc:
        # Missing arbitrary project imports are bugs, not an excuse to reinstall packages.
        if not exc.name or exc.name.split(".", 1)[0] not in DEPENDENCIES:
            raise
        hint(f"Python 依赖 {exc.name} 缺失或无法导入。请通过安装器安装到项目虚拟环境，不要用 sudo pip。",
             command(ROOT / "install.sh", "--non-interactive"))
        return 1
    except json.JSONDecodeError as exc:
        hint(f"JSON 配置或输入格式错误（第 {exc.lineno} 行，第 {exc.colno} 列）；请检查逗号、引号和文件编码。",
             command("vi", config_path()))
        setup_hint(cmd, args)
        return 1
    except UnicodeError:
        hint("配置或输入文件编码无效，请保存为 UTF-8 后重试。", command("vi", config_path()))
        setup_hint(cmd, args)
        return 1
    except ValueError as exc:
        if not is_config_error(exc):
            raise
        hint(f"配置或时区不可用：{exc}", command("vi", config_path()))
        setup_hint(cmd, args)
        return 1
    except OSError as exc:
        if exc.errno not in {errno.ENOENT, errno.EACCES, errno.EPERM, errno.ENOTDIR,
                             errno.EISDIR, errno.EROFS, errno.ENOSPC, errno.EADDRINUSE,
                             errno.EADDRNOTAVAIL, errno.ECONNREFUSED, errno.ENETUNREACH}:
            raise
        if exc.errno == errno.ENOENT:
            title = "文件、目录或外部命令不存在，请检查配置路径；Docker 任务还需要已安装且正在运行的 Docker。"
        elif exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
            title = "当前用户没有访问权限或文件系统只读；请用目录属主运行，勿开放私有目录给所有用户。"
        elif exc.errno == errno.ENOSPC:
            title = "磁盘空间不足，请先检查磁盘并自行清理不再需要的文件。"
        else:
            title = "目录类型、监听端口或网络环境不可用，请核对配置并检查本机环境。"
        repair = command("df", "-h", ROOT) if exc.errno == errno.ENOSPC else command(ROOT / "bin" / "chat-digest", "doctor", "--host-only")
        hint(title, repair)
        setup_hint(cmd, args)
        return 1
    except SystemExit as exc:
        status = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        if status:
            if isinstance(exc.code, str):
                print(exc.code, file=sys.stderr)
            setup_hint(cmd, args)
        return status
    finally:
        sys.argv = previous_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
