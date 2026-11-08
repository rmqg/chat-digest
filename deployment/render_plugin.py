#!/usr/bin/env python3
"""Optional external report renderer.

By default every edition is drawn by the bundled renderer (``agent_console/daily_report.py``).
A deployment can point ``render.module`` at a Python file on the host to draw the PNG (and,
optionally, the HTML) with its own functions instead. See docs/CONFIGURATION.md, section "render".

    "render": {"module": "/srv/my-renderer/renderer.py",
               "function": "render_png_pillow",   # (report: dict, output: Path) -> None
               "html_function": "render_html"}    # optional, (report: dict) -> str

The report dictionary passed to these functions is the same object that is written to the
edition JSON (format: docs/REPORT-JSON.md). The external module is loaded once per process.
Python files next to it take precedence for its own imports, without replacing modules that
chat-digest itself already loaded.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

DEFAULT_FUNCTION = "render_png_pillow"
KEYS = {"module", "function", "html_function"}


class RenderConfigError(ValueError):
    """The render configuration is invalid or the external renderer cannot be loaded."""


@dataclass(frozen=True)
class Renderer:
    png: Callable[[dict, Path], Any]
    html: Callable[[dict], str]
    source: str  # "builtin" or the external module path

    def render(self, report: dict, html_path: Path, png_path: Path) -> None:
        text = self.html(report)
        if not isinstance(text, str):
            raise RenderConfigError("render.html_function must return a string")
        html_path.write_text(text, encoding="utf-8")
        self.png(report, png_path)
        if not png_path.is_file() or png_path.stat().st_size == 0:
            raise RenderConfigError("render.function did not write the PNG file")


def normalize(spec: Any) -> dict[str, str | None] | None:
    """Validate a ``render`` object; ``None``/``{}``/missing module means the bundled renderer."""
    if spec is None or spec == {}:
        return None
    if not isinstance(spec, dict):
        raise RenderConfigError("render must be a JSON object")
    unknown = sorted(set(spec) - KEYS)
    if unknown:
        raise RenderConfigError("unknown render keys: " + ", ".join(unknown))
    module = spec.get("module")
    if module in (None, ""):
        if spec.get("function") or spec.get("html_function"):
            raise RenderConfigError("render.function needs render.module")
        return None
    if not isinstance(module, str) or not module.endswith(".py"):
        raise RenderConfigError("render.module must be the path of a .py file")
    result: dict[str, str | None] = {"module": module, "function": DEFAULT_FUNCTION, "html_function": None}
    for key in ("function", "html_function"):
        value = spec.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or not value.isidentifier():
            raise RenderConfigError(f"render.{key} must be a Python function name")
        result[key] = value
    return result


_LOADED: dict[str, ModuleType] = {}


def _load_module(path: Path) -> ModuleType:
    key = str(path)
    if key in _LOADED:
        return _LOADED[key]
    directory = path.parent
    siblings = {item.stem for item in directory.glob("*.py")}
    saved = {name: sys.modules.pop(name) for name in list(sys.modules) if name in siblings}
    name = "chat_digest_render_plugin_" + hashlib.sha256(key.encode()).hexdigest()[:12]
    sys.path.insert(0, str(directory))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RenderConfigError("render.module cannot be imported")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses and pickling look the module up while it is executing
        spec.loader.exec_module(module)
    except RenderConfigError:
        sys.modules.pop(name, None)
        raise
    except Exception as exc:
        sys.modules.pop(name, None)
        raise RenderConfigError(f"render.module failed to import: {type(exc).__name__}") from exc
    finally:
        sys.path.remove(str(directory))
        for sibling in siblings:  # keep the plugin's own copies private to it
            sys.modules.pop(sibling, None)
        sys.modules.update(saved)
    _LOADED[key] = module
    return module


def load(spec: Any, builtin: ModuleType) -> Renderer:
    """Return the renderer to use: the external one when configured, else ``builtin``."""
    config = normalize(spec)
    if config is None:
        return Renderer(builtin.render_png_pillow, builtin.render_html, "builtin")
    path = Path(str(config["module"])).expanduser()
    if not path.is_absolute():
        raise RenderConfigError("render.module must be an absolute path")
    if not path.is_file():
        raise RenderConfigError(f"render.module not found: {path}")
    module = _load_module(path.resolve())
    png = getattr(module, str(config["function"]), None)
    if not callable(png):
        raise RenderConfigError(f"render.module has no function {config['function']}")
    html = builtin.render_html
    if config["html_function"]:
        html = getattr(module, str(config["html_function"]), None)
        if not callable(html):
            raise RenderConfigError(f"render.module has no function {config['html_function']}")
    return Renderer(png, html, str(path))


def container_spec(spec: Any, mount_point: str = "/render") -> tuple[dict | None, Path | None]:
    """For containerized runs: the spec as seen inside the container and the host directory to mount."""
    config = normalize(spec)
    if config is None:
        return None, None
    host = Path(str(config["module"])).expanduser()
    if not host.is_absolute() or not host.is_file():
        raise RenderConfigError(f"render.module not found: {host}")
    inside = {key: value for key, value in config.items() if value}
    inside["module"] = f"{mount_point}/{host.name}"
    return inside, host.resolve().parent


def cli_args(spec: Any) -> list[str]:
    config = normalize(spec)
    if config is None:
        return []
    args = ["--render-module", str(config["module"]), "--render-function", str(config["function"])]
    if config["html_function"]:
        args += ["--render-html-function", str(config["html_function"])]
    return args


def spec_from_args(args: Any) -> dict | None:
    module = getattr(args, "render_module", None)
    if not module:
        return None
    return {"module": module, "function": getattr(args, "render_function", None),
            "html_function": getattr(args, "render_html_function", None)}


def add_arguments(parser: Any) -> None:
    parser.add_argument("--render-module", help="external renderer .py file (see docs/CONFIGURATION.md)")
    parser.add_argument("--render-function", help=f"PNG function in --render-module (default {DEFAULT_FUNCTION})")
    parser.add_argument("--render-html-function", help="HTML function in --render-module (default: bundled HTML)")
