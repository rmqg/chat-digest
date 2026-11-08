import argparse
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

from deployment import render_plugin


def _builtin():
    calls = []
    module = types.SimpleNamespace(
        render_html=lambda report: "<p>builtin</p>",
        render_png_pillow=lambda report, path: (calls.append("png"), Path(path).write_bytes(b"builtin")))
    return module, calls


PLUGIN = '''
from dataclasses import dataclass
from helper_for_plugin import marker


@dataclass
class Layout:  # a dataclass needs its module registered while it is being imported
    width: int = 1200

def draw(report, output):
    output.write_bytes(("external:" + marker() + ":" + report["title"]).encode())

def page(report):
    return "<p>external " + report["title"] + "</p>"
'''


class RenderPluginTests(unittest.TestCase):
    def setUp(self):
        render_plugin._LOADED.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.plugin_dir = self.root / "plugin"
        self.plugin_dir.mkdir()
        (self.plugin_dir / "renderer.py").write_text(PLUGIN, encoding="utf-8")
        (self.plugin_dir / "helper_for_plugin.py").write_text("def marker():\n    return 'own'\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()
        sys.modules.pop("helper_for_plugin", None)

    def test_missing_or_empty_config_uses_the_bundled_renderer(self):
        builtin, calls = _builtin()
        for spec in (None, {}, {"module": ""}):
            renderer = render_plugin.load(spec, builtin)
            self.assertEqual(renderer.source, "builtin")
        renderer.png({}, self.root / "a.png")
        self.assertEqual(calls, ["png"])
        self.assertEqual(renderer.html({}), "<p>builtin</p>")

    def test_external_png_function_is_used_and_html_stays_bundled_by_default(self):
        builtin, calls = _builtin()
        renderer = render_plugin.load({"module": str(self.plugin_dir / "renderer.py"), "function": "draw"}, builtin)
        out = self.root / "x.png"
        renderer.png({"title": "t"}, out)
        self.assertEqual(out.read_bytes(), b"external:own:t")
        self.assertEqual(renderer.html({"title": "t"}), "<p>builtin</p>")
        self.assertEqual(calls, [])

    def test_external_html_function_is_optional(self):
        builtin, _ = _builtin()
        renderer = render_plugin.load({"module": str(self.plugin_dir / "renderer.py"), "function": "draw",
                                       "html_function": "page"}, builtin)
        html_path, png_path = self.root / "r.html", self.root / "r.png"
        renderer.render({"title": "t"}, html_path, png_path)
        self.assertEqual(html_path.read_text(encoding="utf-8"), "<p>external t</p>")
        self.assertTrue(png_path.read_bytes().startswith(b"external:"))

    def test_sibling_modules_do_not_replace_modules_already_loaded(self):
        builtin, _ = _builtin()
        ours = types.ModuleType("helper_for_plugin")
        ours.marker = lambda: "chat-digest"
        sys.modules["helper_for_plugin"] = ours
        renderer = render_plugin.load({"module": str(self.plugin_dir / "renderer.py"), "function": "draw"}, builtin)
        out = self.root / "y.png"
        renderer.png({"title": "t"}, out)
        self.assertEqual(out.read_bytes(), b"external:own:t")       # the plugin got its own sibling
        self.assertIs(sys.modules["helper_for_plugin"], ours)        # ours is untouched
        self.assertNotIn(str(self.plugin_dir), sys.path)

    def test_invalid_configurations_are_rejected(self):
        builtin, _ = _builtin()
        module = str(self.plugin_dir / "renderer.py")
        bad = [
            "renderer.py",
            {"module": module, "colour": "red"},
            {"module": "relative/renderer.py"},
            {"module": str(self.plugin_dir / "missing.py")},
            {"module": str(self.plugin_dir / "notpython.txt")},
            {"module": module, "function": "no_such_function"},
            {"module": module, "function": "draw", "html_function": "no_such_function"},
            {"module": module, "function": "not-an-identifier"},
            {"function": "draw"},
        ]
        for spec in bad:
            with self.subTest(spec=spec), self.assertRaises(render_plugin.RenderConfigError):
                render_plugin.load(spec, builtin)

    def test_import_errors_are_reported_without_leaking_state(self):
        builtin, _ = _builtin()
        (self.plugin_dir / "broken.py").write_text("import helper_for_plugin\nraise RuntimeError('boom')\n")
        with self.assertRaisesRegex(render_plugin.RenderConfigError, "failed to import: RuntimeError"):
            render_plugin.load({"module": str(self.plugin_dir / "broken.py")}, builtin)
        self.assertNotIn("helper_for_plugin", sys.modules)
        self.assertNotIn(str(self.plugin_dir), sys.path)

    def test_container_spec_mounts_the_module_directory(self):
        spec = {"module": str(self.plugin_dir / "renderer.py"), "function": "draw"}
        inside, directory = render_plugin.container_spec(spec)
        self.assertEqual(directory, self.plugin_dir.resolve())
        self.assertEqual(inside, {"module": "/render/renderer.py", "function": "draw"})
        self.assertEqual(render_plugin.container_spec(None), (None, None))

    def test_cli_arguments_round_trip(self):
        spec = {"module": "/render/renderer.py", "function": "draw", "html_function": "page"}
        parser = argparse.ArgumentParser()
        render_plugin.add_arguments(parser)
        args = parser.parse_args(render_plugin.cli_args(spec))
        self.assertEqual(render_plugin.spec_from_args(args), spec)
        self.assertEqual(render_plugin.cli_args(None), [])
        self.assertIsNone(render_plugin.spec_from_args(parser.parse_args([])))
        self.assertEqual(render_plugin.cli_args({"module": "/render/renderer.py"}),
                         ["--render-module", "/render/renderer.py", "--render-function", "render_png_pillow"])


class RenderPluginEntryPointTests(unittest.TestCase):
    def test_digest_and_qq_digest_accept_render_arguments(self):
        from deployment import digest, qq_digest
        args = digest.build_parser().parse_args(["--export-root", "/x", "--render-module", "/render/r.py",
                                                 "--render-function", "draw"])
        self.assertEqual(render_plugin.spec_from_args(args)["module"], "/render/r.py")
        args = qq_digest._parse_args(["--sources-json", "s", "--model-config", "m", "--runtime-dir", "r",
                                      "--group-id", "1", "--output-dir", "o", "--start", "1", "--end", "2",
                                      "--render-module", "/render/r.py"])
        self.assertEqual(render_plugin.spec_from_args(args)["function"], None)

    def test_qq_service_validates_render_and_passes_it_to_the_generator(self):
        from deployment import qq_service
        with tempfile.TemporaryDirectory() as root:
            base = {"onebot_url": "ws://127.0.0.1:3001", "onebot_token_file": "token", "group_id": 10001,
                    "group_name": "示例群", "bot_names": ["bot"], "runtime_dir": "work", "reports_dir": "reports",
                    "model_config": "model.json", "enabled_from": 1, "expected_self_id": "10000"}
            path = Path(root) / "service.json"
            path.write_text(json.dumps({**base, "render": {"module": "/render/r.py", "colour": 1}}), encoding="utf-8")
            with self.assertRaisesRegex(qq_service.ConfigurationError, "unknown render keys"):
                qq_service.load_config(path)
            path.write_text(json.dumps({**base, "render": {"module": "/render/r.py", "function": "draw"}}),
                            encoding="utf-8")
            config = qq_service.load_config(path)
            self.assertEqual(render_plugin.cli_args(config["render"]),
                             ["--render-module", "/render/r.py", "--render-function", "draw"])

    def test_project_settings_validate_render(self):
        from deployment import settings
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "project.json"
            path.write_text(json.dumps({"render": {"module": "/srv/r.py", "html_function": "1bad"}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                _load_with(settings, path)
            path.write_text(json.dumps({"render": {"module": "/srv/r.py"}}), encoding="utf-8")
            self.assertEqual(_load_with(settings, path)["render"], {"module": "/srv/r.py"})


def _load_with(settings, path):
    import os
    old = os.environ.get("CHAT_DIGEST_CONFIG")
    os.environ["CHAT_DIGEST_CONFIG"] = str(path)
    try:
        return settings.load_project()
    finally:
        if old is None:
            os.environ.pop("CHAT_DIGEST_CONFIG", None)
        else:
            os.environ["CHAT_DIGEST_CONFIG"] = old


if __name__ == "__main__":
    unittest.main()
