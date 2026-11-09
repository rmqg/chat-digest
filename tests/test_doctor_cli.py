"""Focused read-only doctor/CLI coverage; all fixtures are fictional and local."""
import contextlib
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location("test_chat_digest_" + name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.doctor = load_script("doctor")
        self.temp = tempfile.TemporaryDirectory(prefix="doctor space '")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.config = self.runtime / "project '$().json"
        self.config.write_text('{}', encoding="utf-8")
        self.config.chmod(0o600)
        import settings
        self.settings = settings
        patches = [mock.patch.object(self.doctor, "ROOT", self.root),
                   mock.patch.object(settings, "RUNTIME", self.runtime),
                   mock.patch.dict(os.environ, {"CHAT_DIGEST_CONFIG": str(self.config)})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def assertRepairs(self):
        for result in self.doctor.RESULTS:
            if result["status"] in {"fail", "warn"}:
                self.assertTrue(result.get("fix"), result)
                self.assertTrue(shlex.split(result["fix"]), result)
                self.assertRegex(result["detail"], r"[\u4e00-\u9fff]", result)

    def test_private_permissions_quote_dynamic_path(self):
        self.config.chmod(0o644)
        self.assertFalse(self.doctor._private(self.config, "project.json", required=True))
        self.assertEqual(shlex.split(self.doctor.RESULTS[-1]["fix"]), ["chmod", "600", str(self.config)])
        self.assertRepairs()

    def test_missing_optional_model_has_repair_and_warning(self):
        self.doctor.check_model()
        self.assertEqual(self.doctor.RESULTS[-1]["status"], "warn")
        self.assertEqual(shlex.split(self.doctor.RESULTS[-1]["fix"]), [str(self.root / "install.sh")])
        self.assertRepairs()

    def test_invalid_project_uses_selected_config_in_repair(self):
        self.config.write_text('{ invalid', encoding="utf-8")
        self.assertIsNone(self.doctor.check_runtime(self.config))
        self.assertEqual(shlex.split(self.doctor.RESULTS[-1]["fix"]), ["vi", str(self.config)])
        self.assertRepairs()

    def test_missing_and_corrupt_tokenizer_use_selected_cache(self):
        cache = self.root / "cache '; $()"
        cache.mkdir()
        with mock.patch.dict(os.environ, {"TIKTOKEN_CACHE_DIR": str(cache)}):
            self.doctor.check_tokenizer()
            args = shlex.split(self.doctor.RESULTS[-1]["fix"])
            self.assertEqual(args[:3], ["env", "TIKTOKEN_CACHE_DIR=" + str(cache), sys.executable])
            path = cache / self.doctor.TIKTOKEN_FILE
            path.write_bytes(b"fictional corrupted cache")
            self.doctor.check_tokenizer()
        fix = self.doctor.RESULTS[-1]["fix"]
        self.assertIn("mktemp", fix)
        self.assertIn(shlex.quote(str(path)), fix)
        self.assertNotIn("rm ", fix)
        self.assertEqual(path.read_bytes(), b"fictional corrupted cache")
        self.assertRepairs()

    def test_missing_packages_report_but_programming_errors_escape(self):
        with mock.patch.object(self.doctor.importlib, "import_module", side_effect=ModuleNotFoundError("missing", name="PIL")):
            self.doctor.check_packages()
        self.assertEqual(len(self.doctor.RESULTS), 4)
        self.assertRepairs()
        with mock.patch.object(self.doctor.importlib, "import_module", side_effect=RuntimeError("programming bug")):
            with self.assertRaisesRegex(RuntimeError, "programming bug"):
                self.doctor.check_packages()

    def test_export_permissions_and_staleness_have_quoted_commands(self):
        import chat_export
        export = self.root / "export ';()"
        export.mkdir()
        export.chmod(0o705)
        project = {"export": {"root": str(export)}, "group": {"conversation_id": "g_fictional"}}
        with mock.patch.object(chat_export, "load_manifest", return_value={"source": {"platform": "fictional"}}), mock.patch.object(chat_export, "find_conversation", return_value={"last_ts": int(time.time()) - 200000}):
            self.doctor.check_export(project)
        permissions, stale = self.doctor.RESULTS
        self.assertEqual(shlex.split(permissions["fix"]), ["chmod", "700", str(export.resolve())])
        self.assertEqual(shlex.split(stale["fix"]), [str(self.root / "bin" / "chat-digest"), "list-conversations", "--export-root", str(export.resolve())])
        self.assertRepairs()

    def test_missing_sender_and_bad_render_config_have_repairs(self):
        project = {"send": {"command": ["fictional-not-installed"], "to": "g_fictional"}, "render": "invalid"}
        self.doctor.check_sender({}, False)
        with mock.patch.object(self.doctor.shutil, "which", return_value=None):
            self.doctor.check_sender(project, False)
        self.doctor.check_render(project, import_module=False)
        self.assertEqual([item["status"] for item in self.doctor.RESULTS], ["warn", "fail", "fail"])
        self.assertRepairs()

    def test_failed_sender_probe_never_suggests_real_send(self):
        import chat_send
        project = {"send": {"command": ["fictional-sender"], "to": "g_fictional"}}
        with mock.patch.object(self.doctor.shutil, "which", return_value="/fictional/sender"), mock.patch.object(chat_send, "send", return_value={"status": "failed", "reason": "offline"}) as sender:
            self.doctor.check_sender(project, True)
        self.assertTrue(sender.call_args.kwargs["dry_run"])
        self.assertIn("--probe-send", self.doctor.RESULTS[-1]["fix"])
        self.assertRepairs()

    def test_site_permission_and_invalid_config_repairs(self):
        import local_site
        path = self.runtime / "site '$().json"
        path.write_text('{}', encoding="utf-8")
        path.chmod(0o644)
        with mock.patch.object(local_site, "config_path", return_value=path), mock.patch.object(local_site, "load_config", return_value={"port": 12345}):
            self.doctor.check_site()
        self.assertEqual(shlex.split(self.doctor.RESULTS[0]["fix"]), ["chmod", "600", str(path)])
        with mock.patch.object(local_site, "config_path", return_value=path), mock.patch.object(local_site, "load_config", side_effect=ValueError("fictional invalid configuration")):
            self.doctor.check_site()
        self.assertEqual(shlex.split(self.doctor.RESULTS[-1]["fix"]), ["vi", str(path)])
        self.assertRepairs()

    def test_missing_fonts_have_nonoverwriting_restore_command(self):
        self.doctor.check_fonts()
        self.assertIn("cp -n --", self.doctor.RESULTS[-1]["fix"])
        self.assertIn(shlex.quote(str(self.root / "assets" / "fonts")), self.doctor.RESULTS[-1]["fix"])
        self.assertRepairs()

    def test_docker_absent_and_unreachable_have_repairs(self):
        with mock.patch.object(self.doctor.shutil, "which", return_value=None):
            self.doctor.check_docker({})
        with mock.patch.object(self.doctor.shutil, "which", return_value="/fictional/docker"), mock.patch.object(self.doctor.subprocess, "run", side_effect=OSError("offline")):
            self.doctor.check_docker({})
        self.assertEqual([item["status"] for item in self.doctor.RESULTS], ["warn", "fail"])
        self.assertRepairs()

    def test_modes_json_exit_status_and_repeated_invocations(self):
        names = ("check_python", "check_packages", "check_tokenizer", "check_fonts", "check_runtime", "check_timezone", "check_export", "check_model", "check_sender", "check_render", "check_site", "check_docker")
        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(mock.patch.object(self.doctor, name)) for name in names}
            mocks["check_python"].side_effect = lambda: self.doctor.report("python", "ok", "版本满足要求")
            mocks["check_runtime"].return_value = {"group": {"conversation_id": "g_fictional"}}
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(self.doctor.main(["--demo-only", "--json"]), 0)
            result = json.loads(out.getvalue())
            self.assertEqual(result, {"ok": True, "checks": [{"check": "python", "status": "ok", "detail": "版本满足要求"}]})
            mocks["check_runtime"].assert_not_called()
            mocks["check_packages"].assert_called_once()
            mocks["check_packages"].reset_mock()
            mocks["check_tokenizer"].reset_mock()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.doctor.main(["--host-only", "--no-docker", "--probe-send"]), 0)
            mocks["check_packages"].assert_not_called()
            mocks["check_tokenizer"].assert_not_called()
            mocks["check_docker"].assert_not_called()
            mocks["check_render"].assert_called_once_with(mocks["check_runtime"].return_value, import_module=False)
            mocks["check_sender"].assert_called_once_with(mocks["check_runtime"].return_value, True)
            self.assertEqual(len(self.doctor.RESULTS), 1)
            mocks["check_python"].side_effect = lambda: self.doctor.report("python", "fail", "版本太旧", "brew install python@3.11")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.doctor.main(["--demo-only", "--json"]), 1)


class CLITests(unittest.TestCase):
    def setUp(self):
        self.cli = load_script("cli")

    def invoke(self, args, error):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(self.cli.runpy, "run_path", side_effect=error), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = self.cli.main(args)
        return status, out.getvalue(), err.getvalue()

    def test_missing_known_dependency_is_actionable_and_stdout_stays_clean(self):
        status, out, err = self.invoke(["demo"], ModuleNotFoundError("No module named PIL", name="PIL"))
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("依赖", err)
        self.assertIn("install.sh", err)
        self.assertNotIn("Traceback", err)

    def test_missing_configuration_has_chinese_hint(self):
        import settings
        def missing(*args, **kwargs):
            settings.load_project(required=True)
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {"CHAT_DIGEST_CONFIG": str(Path(temporary) / "absent.json")}):
            status, out, err = self.invoke(["run"], missing)
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("配置", err)
        self.assertIn("修复命令", err)
        self.assertNotIn("Traceback", err)

    def test_common_environment_errors_are_actionable(self):
        for error in (FileNotFoundError(errno.ENOENT, "missing executable"), PermissionError(errno.EACCES, "private directory"), OSError(errno.EADDRINUSE, "port busy")):
            with self.subTest(error=error):
                status, out, err = self.invoke(["site", "--serve"], error)
                self.assertEqual(status, 1)
                self.assertEqual(out, "")
                self.assertIn("修复命令", err)
                self.assertNotIn("Traceback", err)

    def test_unexpected_programming_errors_keep_tracebacks(self):
        for error in (RuntimeError("bug"), ValueError("bug"), KeyError("bug"), ModuleNotFoundError("bug", name="fictional_project_module")):
            with self.subTest(error=error), mock.patch.object(self.cli.runpy, "run_path", side_effect=error):
                with self.assertRaises(type(error)):
                    self.cli.main(["demo"])

    def test_dispatch_preserves_flags_list_suffix_and_success_output(self):
        previous = sys.argv
        seen = []
        def run(path, run_name):
            seen.append((path, list(sys.argv), run_name))
            print('{"synthetic": true}')
            raise SystemExit(0)
        status, out, err = self.invoke(["list-conversations", "--export-root", "/fictional/export space"], run)
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(out), {"synthetic": True})
        self.assertEqual(err, "")
        self.assertIs(sys.argv, previous)
        self.assertEqual(seen[0][1][1:], ["--export-root", "/fictional/export space", "list"])
        self.assertEqual(seen[0][2], "__main__")

    def test_doctor_json_failure_retains_status_without_extra_hints(self):
        def run(*args, **kwargs):
            print('{"ok": false, "checks": []}')
            raise SystemExit(1)
        status, out, err = self.invoke(["doctor", "--json"], run)
        self.assertEqual(status, 1)
        self.assertFalse(json.loads(out)["ok"])
        self.assertEqual(err, "")

    def test_existing_systemexit_keeps_failure_and_adds_hint(self):
        status, out, err = self.invoke(["run"], SystemExit("fictional configuration error"))
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("fictional configuration error", err)
        self.assertIn("修复命令", err)

    def test_site_custom_config_is_shell_quoted_in_hint(self):
        with tempfile.TemporaryDirectory(prefix="site '") as temporary:
            path = Path(temporary) / "config '$().json"
            path.write_text('{}', encoding="utf-8")
            _, _, err = self.invoke(["site", "--config", str(path), "--serve"], SystemExit(2))
        repair = err.split("修复命令：", 1)[1].strip()
        self.assertEqual(shlex.split(repair), ["vi", str(path)])

    def test_invalid_json_and_encoding_have_chinese_repairs(self):
        errors = (json.JSONDecodeError("fictional invalid JSON", "{", 1),
                  UnicodeDecodeError("utf-8", b"\xff", 0, 1, "fictional invalid encoding"))
        for error in errors:
            with self.subTest(error=error):
                status, out, err = self.invoke(["run"], error)
                self.assertEqual(status, 1)
                self.assertEqual(out, "")
                self.assertIn("修复命令", err)
                self.assertNotIn("Traceback", err)

    def test_shell_rejects_old_interpreter_with_install_command(self):
        with tempfile.TemporaryDirectory(prefix="python '") as temporary:
            interpreter = Path(temporary) / "old-python"
            interpreter.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            interpreter.chmod(0o700)
            env = {**os.environ, "CHAT_DIGEST_PYTHON": str(interpreter)}
            result = subprocess.run([str(ROOT / "bin" / "chat-digest"), "doctor"], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("低于 3.11", result.stderr)
        self.assertIn("安装命令", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_shell_missing_python_and_python_free_help(self):
        env = {**os.environ, "CHAT_DIGEST_PYTHON": "/fictional/absent-python"}
        failed = subprocess.run([str(ROOT / "bin" / "chat-digest"), "demo"], env=env, capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("Python", failed.stderr)
        self.assertIn("安装命令", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)
        help_result = subprocess.run([str(ROOT / "bin" / "chat-digest"), "help"], env=env, capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("doctor", help_result.stdout)


if __name__ == "__main__":
    unittest.main()
