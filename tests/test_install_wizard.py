import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def wizard():
    spec = importlib.util.spec_from_file_location("install_wizard", ROOT / "scripts/install-wizard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_answer_file_is_data_not_shell(tmp_path):
    module = wizard()
    marker = tmp_path / "not-executed"
    answers = tmp_path / "answers"
    answers.write_text(f"MODE=demo\nGROUP_NAME='测试 小组'\nMODEL_API_KEY='$(touch {marker})'\n")
    result = module.read_answers(answers)
    assert result["GROUP_NAME"] == "测试 小组"
    assert result["MODEL_API_KEY"] == f"$(touch {marker})"
    assert not marker.exists()


@pytest.mark.parametrize("line", ["UNKNOWN=value", "MODE=demo\nMODE=real", "GROUP_NAME=two words", "missing-equals"])
def test_invalid_answers_are_rejected(tmp_path, line):
    path = tmp_path / "answers"
    path.write_text(line)
    with pytest.raises(ValueError):
        wizard().read_answers(path)


def test_real_answers_require_all_fields_and_absolute_export():
    module = wizard()
    with pytest.raises(ValueError, match="缺少"):
        module.validate({"MODE": "real"})
    answers = dict(MODE="real", EXPORT_ROOT="relative", CONVERSATION_ID="example", GROUP_NAME="示例", MODEL_BASE_URL="https://api.example.invalid/v1", MODEL_NAME="example", MODEL_API_KEY="fictional-key")
    with pytest.raises(ValueError, match="绝对"):
        module.validate(answers)
    answers["EXPORT_ROOT"] = "/srv/export"
    assert module.validate(answers) == "real"
    with pytest.raises(ValueError, match="至少 8"):
        module.validate({"SITE_PASSWORD": "short"})


def test_initialize_real_private_files_and_preserve_aliases(tmp_path, monkeypatch, capsys):
    module = wizard()
    monkeypatch.syspath_prepend(str(ROOT / "deployment"))
    import settings
    import local_site
    import qq_report_site
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(settings, "RUNTIME", runtime)
    monkeypatch.setattr(settings, "CONFIG_PATH", runtime / "project.json")
    monkeypatch.delenv("CHAT_DIGEST_CONFIG", raising=False)
    answers = dict(MODE="real", EXPORT_ROOT="/srv/export", CONVERSATION_ID="sample-id", GROUP_NAME="合成群", MODEL_BASE_URL="https://api.example.invalid/v1", MODEL_NAME="sample-model", MODEL_API_KEY="fictional-key", SITE_PASSWORD="fictional-password")
    module.initialize(answers, "real")
    project_path = runtime / "project.json"
    project = json.loads(project_path.read_text())
    project["group"]["aliases"] = ["old-synthetic-id"]
    project["export"]["refresh_avatars_command"] = ["example-exporter", "refresh-avatars"]
    module.private_json(project_path, project)
    module.initialize(answers, "real")
    configured = json.loads(project_path.read_text())
    assert configured["group"]["aliases"] == ["old-synthetic-id"]
    assert configured["export"]["refresh_avatars_command"] == ["example-exporter", "refresh-avatars"]
    assert configured["group"]["conversation_id"] == "sample-id"
    assert configured["worker_uid"] == os.geteuid()
    for path in (project_path, runtime / "gemini-model.json", runtime / "local-site.json"):
        assert path.stat().st_mode & 0o777 == 0o600
    assert runtime.stat().st_mode & 0o777 == 0o700
    site = local_site.load_config(runtime / "local-site.json")
    assert qq_report_site.verify_password("fictional-password", site["password_scrypt"])
    output = capsys.readouterr().out
    assert "fictional-key" not in output
    assert "fictional-password" not in output


def test_noninteractive_no_venv_runs_host_doctor(monkeypatch):
    module = wizard()
    monkeypatch.setattr(module, "preflight", Mock())
    initialize = Mock()
    monkeypatch.setattr(module, "initialize", initialize)
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main(["--non-interactive", "--no-venv"]) == 0
    initialize.assert_called_once_with({"MODE": "demo"}, "demo")
    commands = [call.args[0] for call in run.call_args_list]
    assert commands[0] == [str(ROOT / "scripts/install.sh"), "--apply", "--no-venv"]
    assert commands[1] == [str(ROOT / "bin/chat-digest"), "doctor", "--demo-only", "--host-only"]
    assert len(commands) == 2


def test_config_mode_and_service_options_forwarded(tmp_path, monkeypatch):
    module = wizard()
    path = tmp_path / "answers"
    path.write_text("MODE=demo\nSITE_PASSWORD='fictional-password'\n")
    monkeypatch.setattr(module, "preflight", Mock())
    initialize = Mock()
    monkeypatch.setattr(module, "initialize", initialize)
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main(["--config", str(path), "--no-venv", "--services", "procd", "--prefix", "/opt/chat-digest"]) == 0
    initialize.assert_called_once_with({"MODE": "demo", "SITE_PASSWORD": "fictional-password"}, "demo")
    assert run.call_args_list[0].args[0][-5:] == ["--no-venv", "--services", "procd", "--prefix", "/opt/chat-digest"]


def test_preview_does_not_initialize(monkeypatch):
    module = wizard()
    monkeypatch.setattr(module, "preflight", Mock())
    initialize = Mock()
    monkeypatch.setattr(module, "initialize", initialize)
    monkeypatch.setattr(module.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0)))
    assert module.main(["--non-interactive", "--dry-run"]) == 0
    initialize.assert_not_called()


def test_root_legacy_dry_run_keeps_original_semantics():
    result = subprocess.run(["sh", str(ROOT / "install.sh"), "--no-venv", "--services", "procd", "--prefix", "/opt/chat-digest"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "dry-run" in result.stdout
    assert "skipped (--no-venv)" in result.stdout


def test_root_config_no_venv_end_to_end(tmp_path):
    answers = tmp_path / "answers"
    answers.write_text("MODE=demo\nSITE_PASSWORD='fictional-password'\n")
    runtime = tmp_path / "runtime"
    environment = os.environ.copy()
    environment.update(CHAT_DIGEST_RUNTIME_ROOT=str(runtime), CHAT_DIGEST_CONFIG=str(runtime / "project.json"))
    result = subprocess.run(["sh", str(ROOT / "install.sh"), "--config", str(answers), "--no-venv", "--python", sys.executable], capture_output=True, text=True, env=environment)
    assert result.returncode == 0, result.stderr + result.stdout
    assert (runtime / "project.json").is_file()
    assert "下一步" in result.stdout
    assert "fictional-password" not in result.stdout


def test_interactive_demo_needs_no_account_or_key(monkeypatch):
    module = wizard()
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: "")
    assert module.collect() == {"MODE": "demo", "SITE_PASSWORD": ""}


def test_interactive_real_collects_required_fields(monkeypatch):
    module = wizard()
    replies = iter(["2", "/srv/export", "synthetic-id", "合成群", "https://api.example.invalid/v1", "synthetic-model"])
    secrets = iter(["fictional-key", "fictional-password"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(replies))
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: next(secrets))
    answers = module.collect()
    assert module.validate(answers) == "real"
    assert answers["GROUP_NAME"] == "合成群"


def test_no_terminal_requires_explicit_mode(monkeypatch):
    module = wizard()
    monkeypatch.setattr(module, "preflight", Mock())
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    with pytest.raises(ValueError, match="没有交互终端"):
        module.main([])
