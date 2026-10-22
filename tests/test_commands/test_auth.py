import json

from click.testing import CliRunner

from jqcli.cli import main


def test_auth_status_json_without_credentials(tmp_path):
    result = CliRunner().invoke(main, ["--config", str(tmp_path / "c.json"), "--format", "json", "auth", "status"])

    assert result.exit_code == 0
    assert json.loads(result.output)["authenticated"] is False


def test_import_token_and_status(tmp_path):
    config = tmp_path / "config.json"
    runner = CliRunner()

    imported = runner.invoke(main, ["--config", str(config), "--format", "json", "auth", "import-token", "--token", "abc"])
    status = runner.invoke(main, ["--config", str(config), "--format", "json", "auth", "status"])

    assert imported.exit_code == 0
    assert json.loads(imported.output) == {"ok": True, "credential": "token"}
    assert json.loads(status.output)["authenticated"] is True
    assert json.loads(status.output)["credential_source"] == "token"


def test_logout_clears_config_credentials(tmp_path):
    config = tmp_path / "config.json"
    runner = CliRunner()
    runner.invoke(main, ["--config", str(config), "auth", "import-token", "--token", "abc"])

    result = runner.invoke(main, ["--config", str(config), "--format", "json", "auth", "logout"])
    status = runner.invoke(main, ["--config", str(config), "--format", "json", "auth", "status"])

    assert result.exit_code == 0
    assert json.loads(status.output)["authenticated"] is False


def test_login_requires_password_or_env(tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--env-file",
            str(tmp_path / "missing.env"),
            "--format",
            "json",
            "auth",
            "login",
            "--username",
            "u",
        ],
    )

    assert result.exit_code == 3
    assert json.loads(result.stderr)["error"]["code"] == "usage_error"


def test_login_reads_password_stdin(monkeypatch, tmp_path):
    def fake_login(api_base, username, password, timeout=30):
        return {"payload": {"code": "00000"}, "cookie": "sid=abc"}

    monkeypatch.setattr("jqcli.commands.auth.login_with_password", fake_login)

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--format",
            "json",
            "auth",
            "login",
            "--username",
            "u",
            "--password-stdin",
        ],
        input="secret",
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["ok"] is True


def test_login_reads_username_and_password_from_env_file(monkeypatch, tmp_path):
    def fake_login(api_base, username, password, timeout=30):
        return {"payload": {"code": "00000"}, "cookie": "sid=abc"}

    monkeypatch.setattr("jqcli.commands.auth.login_with_password", fake_login)
    env_file = tmp_path / ".env"
    config = tmp_path / "c.json"
    env_file.write_text('JQCLI_USERNAME="u@example.com"\nJQCLI_PASSWORD="secret"\n', encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(config),
            "--env-file",
            str(env_file),
            "--format",
            "json",
            "auth",
            "login",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["username"] == "u@example.com"


def test_auth_status_reads_token_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("JQCLI_TOKEN=from-env-file\n", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        ["--config", str(tmp_path / "c.json"), "--env-file", str(env_file), "--format", "json", "auth", "status"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["authenticated"] is True


def test_login_debug_reports_saved_cookie_and_override_risks(monkeypatch, tmp_path):
    def fake_login(api_base, username, password, timeout=30):
        return {"payload": {"code": "00000"}, "cookie": "sid=new-private-cookie"}
    monkeypatch.setattr("jqcli.commands.auth.login_with_password", fake_login)
    monkeypatch.setenv("JQCLI_COOKIE", "sid=old-private-cookie")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"token": "old-private-token"}))
    result = CliRunner().invoke(main, [
        "--config", str(config), "--env-file", "/dev/null", "--format", "json", "--debug",
        "auth", "login", "--username", "private-user", "--password-stdin",
    ], input="private-password")
    assert result.exit_code == 0
    events = [json.loads(line)["debug"] for line in result.stderr.splitlines()]
    assert [event["event"] for event in events] == ["runtime", "login_saved"]
    saved = events[1]
    assert saved["saved_cookie_present"] is True
    assert saved["saved_token_present"] is True
    assert saved["environment_cookie_overrides_saved_cookie"] is True
    assert saved["saved_cookie_fingerprint"] != events[0]["credentials"]["cookie"]["fingerprint"]
    assert all(value not in result.stderr for value in (
        "new-private-cookie", "old-private-cookie", "old-private-token", "private-user", "private-password",
    ))
