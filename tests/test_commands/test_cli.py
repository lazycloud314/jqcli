import json

from click.testing import CliRunner

from jqcli.cli import main


def test_version_option():
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert "jqcli, version" in result.output


def test_web_run_rejects_public_host_by_default(tmp_path):
    result = CliRunner().invoke(
        main,
        ["--config", str(tmp_path / "c.json"), "web", "run", "--host", "0.0.0.0"],
    )

    assert result.exit_code != 0
    assert "默认只允许监听本机地址" in result.output


def test_web_run_rejects_public_debug_host(tmp_path):
    result = CliRunner().invoke(
        main,
        ["--config", str(tmp_path / "c.json"), "web", "run", "--host", "0.0.0.0", "--allow-public", "--debug"],
    )

    assert result.exit_code != 0
    assert "debug 模式不能监听公开地址" in result.output


def test_debug_runtime_reports_actual_credential_sources(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"token": "config-token", "cookie": "sid=config-cookie", "username": "private-user"}))
    env_file = tmp_path / ".env"
    env_file.write_text("JQCLI_TOKEN=file-token\nJQCLI_COOKIE=sid=file-cookie\n")
    monkeypatch.setenv("JQCLI_TOKEN", "process-token")
    result = CliRunner().invoke(main, [
        "--config", str(config), "--env-file", str(env_file), "--token", "cli-token", "--cookie", "sid=cli-cookie",
        "--format", "json", "--debug", "auth", "status",
    ])
    assert result.exit_code == 0
    runtime = json.loads(result.stderr)["debug"]
    assert runtime["event"] == "runtime"
    assert runtime["credentials"]["token"]["source"] == "process_environment"
    assert runtime["credentials"]["cookie"]["source"] == "env_file"
    assert runtime["credentials"]["both_token_and_cookie"] is True
    assert runtime["credentials"]["cookie"]["fingerprint"].startswith("sha256:")
    assert runtime["config_read_path"] == str(config.resolve())
    assert runtime["env_file_loaded"] == str(env_file.resolve())
    assert len(runtime["source_hashes"]["api/client.py"]) == 16
    assert all(value not in result.stderr for value in (
        "config-token", "config-cookie", "private-user", "file-token", "file-cookie", "process-token", "cli-token", "cli-cookie",
    ))


def test_debug_runtime_distinguishes_cli_and_config_credentials(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"token": "config-token", "cookie": "sid=config-cookie"}))
    result = CliRunner().invoke(main, [
        "--config", str(config), "--env-file", "/dev/null", "--token", "cli-token",
        "--format", "json", "--debug", "auth", "status",
    ])
    runtime = json.loads(result.stderr)["debug"]
    assert runtime["credentials"]["token"]["source"] == "command_line"
    assert runtime["credentials"]["cookie"]["source"] == "config_file"


def test_debug_runtime_reports_legacy_read_path(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"cookie": "sid=legacy-cookie"}))
    target = tmp_path / "new.json"
    monkeypatch.setattr("jqcli.config.default_config_path", lambda: target)
    monkeypatch.setattr("jqcli.config.legacy_config_path", lambda: legacy)
    result = CliRunner().invoke(main, ["--env-file", "/dev/null", "--format", "json", "--debug", "auth", "status"])
    runtime = json.loads(result.stderr)["debug"]
    assert runtime["config_read_path"] == str(legacy.resolve())
    assert runtime["config_write_path"] == str(target.resolve())
    assert not target.exists()
