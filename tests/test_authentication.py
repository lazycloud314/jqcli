import json

import httpx
import pytest
from click.testing import CliRunner

from jqcli.api.client import ApiClient
from jqcli.authentication import ensure_authentication
from jqcli.cli import AppContext, main
from jqcli.config import Config, load_config, resolve_credentials
from jqcli.errors import ApiError, NetworkError, NotAuthenticatedError


VALID_PAGE = '<script src="/std/algorithm/js/list.min.js"></script><table></table>'


def app_context(tmp_path, *, cookie="sid=valid", token=None, debug=False):
    return AppContext(
        config=Config(tmp_path / "config.json", {}), api_base="https://example.test",
        token=token, cookie=cookie, output_format="json", non_interactive=True,
        quiet=False, debug=debug, timeout=3,
    )


def install_transport(monkeypatch, handler):
    def factory(api_base, **kwargs):
        return ApiClient(api_base, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("jqcli.authentication.ApiClient", factory)
    monkeypatch.setattr("jqcli.commands.strategy.ApiClient", factory)
    return factory


def login_credentials(monkeypatch):
    monkeypatch.setenv("JQCLI_USERNAME", "test-user")
    monkeypatch.setenv("JQCLI_PASSWORD", "test-password")


def test_valid_cookie_checked_once_without_password_login(monkeypatch, tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/algorithm/index/list"
        assert request.headers["cookie"] == "sid=valid"
        assert "authorization" not in request.headers
        return httpx.Response(200, text=VALID_PAGE)

    install_transport(monkeypatch, handler)
    monkeypatch.setattr("jqcli.authentication.login_with_password", lambda *a, **kw: pytest.fail("must not login"))
    app = app_context(tmp_path, token="stale-token")
    ensure_authentication(app)
    ensure_authentication(app)
    assert len(requests) == 1
    assert app.cookie == "sid=valid" and app.token is None
    assert not app.config.path.exists()


@pytest.mark.parametrize("expired_response", [
    httpx.Response(401),
    httpx.Response(403),
    httpx.Response(302, headers={"location": "/user/login/index"}),
    httpx.Response(200, text='<form action="/user/login/index"><input type="password"></form>'),
    httpx.Response(200, json={"status": 2, "redirect": "/user/login/index"}),
])
def test_expired_cookie_logs_in_then_verifies_before_persisting(monkeypatch, tmp_path, expired_response):
    events = []
    login_credentials(monkeypatch)

    def handler(request):
        events.append(request.headers["cookie"])
        if request.headers["cookie"] == "sid=expired":
            return expired_response
        assert request.headers["cookie"] == "sid=new"
        assert "authorization" not in request.headers
        return httpx.Response(200, text=VALID_PAGE)

    def login(api_base, username, password, *, timeout):
        assert (api_base, username, password, timeout) == ("https://example.test", "test-user", "test-password", 3)
        events.append("login")
        return {"cookie": "sid=new"}

    install_transport(monkeypatch, handler)
    monkeypatch.setattr("jqcli.authentication.login_with_password", login)
    app = app_context(tmp_path, cookie="sid=expired", token="stale-token")
    ensure_authentication(app)
    assert events == ["sid=expired", "login", "sid=new"]
    assert app.authentication_checked and app.cookie == "sid=new" and app.token is None
    saved = json.loads(app.config.path.read_text())
    assert saved["cookie"] == "sid=new" and saved["username"] == "test-user"
    assert "password" not in saved and "test-password" not in str(saved)


@pytest.mark.parametrize("error", [NetworkError(), ApiError("platform unavailable")])
def test_network_and_platform_errors_do_not_trigger_login(monkeypatch, tmp_path, error):
    login_credentials(monkeypatch)

    def probe(*args, **kwargs):
        raise error

    monkeypatch.setattr("jqcli.authentication._probe_login", probe)
    monkeypatch.setattr("jqcli.authentication.login_with_password", lambda *a, **kw: pytest.fail("must not login"))
    with pytest.raises(type(error)):
        ensure_authentication(app_context(tmp_path))


def test_expired_cookie_without_password_reports_actionable_error(monkeypatch, tmp_path):
    install_transport(monkeypatch, lambda request: httpx.Response(401))
    with pytest.raises(NotAuthenticatedError, match="JQCLI_PASSWORD"):
        ensure_authentication(app_context(tmp_path))


def test_failed_refresh_is_not_repeated_or_saved(monkeypatch, tmp_path):
    calls = []
    login_credentials(monkeypatch)
    install_transport(monkeypatch, lambda request: httpx.Response(401))

    def login(*args, **kwargs):
        calls.append("login")
        return {"cookie": "sid=still-invalid"}

    monkeypatch.setattr("jqcli.authentication.login_with_password", login)
    app = app_context(tmp_path)
    with pytest.raises(NotAuthenticatedError):
        ensure_authentication(app)
    assert calls == ["login"]
    assert not app.authentication_checked and not app.config.path.exists()


def test_missing_cookie_can_login_using_saved_username_for_research(monkeypatch, tmp_path):
    monkeypatch.setenv("JQCLI_PASSWORD", "test-password")
    install_transport(monkeypatch, lambda request: httpx.Response(200, text=VALID_PAGE))

    def login(api_base, username, password, **kwargs):
        assert (username, password) == ("saved-user", "test-password")
        return {"cookie": "sid=new"}

    monkeypatch.setattr("jqcli.authentication.login_with_password", login)
    app = app_context(tmp_path, cookie=None, token="not-a-research-cookie")
    app.config.data["username"] = "saved-user"
    ensure_authentication(app, cookie_only=True)
    assert app.cookie == "sid=new" and app.token is None


def test_token_only_credentials_can_still_be_checked(monkeypatch, tmp_path):
    def handler(request):
        assert request.headers["authorization"] == "Bearer valid-token"
        assert "cookie" not in request.headers
        return httpx.Response(200, text=VALID_PAGE)

    install_transport(monkeypatch, handler)
    app = app_context(tmp_path, cookie=None, token="valid-token")
    ensure_authentication(app)
    assert app.token == "valid-token" and app.cookie is None


def test_probe_cookie_rotation_is_used_by_business_client(monkeypatch, tmp_path):
    install_transport(monkeypatch, lambda request: httpx.Response(
        200, text=VALID_PAGE, headers={"set-cookie": "sid=rotated; Path=/"},
    ))
    app = app_context(tmp_path)
    ensure_authentication(app)
    assert app.cookie == "sid=rotated"


@pytest.mark.parametrize("cookie_source", ["environment", "command-line"])
def test_cli_refresh_runs_command_and_next_invocation_uses_saved_cookie(monkeypatch, tmp_path, cookie_source):
    requests = []
    logins = []
    login_credentials(monkeypatch)
    if cookie_source == "environment":
        monkeypatch.setenv("JQCLI_COOKIE", "sid=expired")
    monkeypatch.setenv("JQCLI_TOKEN", "stale-token")

    def handler(request):
        cookie = request.headers["cookie"]
        requests.append(cookie)
        assert "authorization" not in request.headers
        if cookie == "sid=expired":
            return httpx.Response(302, headers={"location": "/user/login/index"})
        assert cookie == "sid=new"
        return httpx.Response(200, text=VALID_PAGE)

    def login(*args, **kwargs):
        logins.append(1)
        return {"cookie": "sid=new"}

    install_transport(monkeypatch, handler)
    monkeypatch.setattr("jqcli.authentication.login_with_password", login)
    config_path = tmp_path / "config.json"
    args = ["--config", str(config_path), "--env-file", "/dev/null", "--format", "json", "--non-interactive"]
    if cookie_source == "command-line":
        args += ["--cookie", "sid=expired"]
    runner = CliRunner()
    for _ in range(2):
        result = runner.invoke(main, args + ["strategy", "ls"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {"items": []}
        assert result.stderr == ""
    assert logins == [1]
    assert requests == ["sid=expired", "sid=new", "sid=new", "sid=new", "sid=new"]
    config = load_config(config_path)
    assert resolve_credentials(config, cookie="sid=expired")[1] == "sid=new"
    assert resolve_credentials(config, cookie="sid=another")[1] == (
        "sid=new" if cookie_source == "environment" else "sid=another"
    )


def test_business_write_is_never_replayed_after_authentication_error(monkeypatch, tmp_path):
    writes = []
    install_transport(monkeypatch, lambda request: httpx.Response(200, text=VALID_PAGE))

    def update(client, strategy_id, **kwargs):
        writes.append(strategy_id)
        raise NotAuthenticatedError("expired during write")

    monkeypatch.setattr("jqcli.commands.strategy.update_strategy", update)
    monkeypatch.setattr("jqcli.authentication.login_with_password", lambda *a, **kw: pytest.fail("do not replay writes"))
    result = CliRunner().invoke(main, [
        "--config", str(tmp_path / "config.json"), "--cookie", "sid=valid", "--format", "json",
        "strategy", "edit", "s1", "--name", "new-name",
    ])
    assert result.exit_code == 1 and writes == ["s1"]


def test_status_live_checks_actual_server_state(monkeypatch, tmp_path):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, text=VALID_PAGE)

    install_transport(monkeypatch, handler)
    result = CliRunner().invoke(main, [
        "--config", str(tmp_path / "config.json"), "--cookie", "sid=valid", "--format", "json",
        "auth", "status", "--live",
    ])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["authenticated"] is True
    assert requests == ["/algorithm/index/list"]


@pytest.mark.parametrize("command", [
    ["--help"], ["--version"], ["strategy", "ls", "--help"],
    ["research", "exec", "--help"], ["auth", "status"], ["auth", "logout"],
])
def test_local_commands_never_check_or_login(monkeypatch, tmp_path, command):
    login_credentials(monkeypatch)
    monkeypatch.setattr("jqcli.authentication._probe_login", lambda *a, **kw: pytest.fail("must not probe"))
    monkeypatch.setattr("jqcli.authentication.login_with_password", lambda *a, **kw: pytest.fail("must not login"))
    result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.json")] + command)
    assert result.exit_code == 0, result.output


def test_research_stdin_survives_automatic_login(monkeypatch, tmp_path):
    login_credentials(monkeypatch)
    factory = install_transport(monkeypatch, lambda request: httpx.Response(200, text=VALID_PAGE))
    monkeypatch.setattr("jqcli.commands.research.ApiClient", factory)
    monkeypatch.setattr("jqcli.authentication.login_with_password", lambda *a, **kw: {"cookie": "sid=new"})

    def execute(client, code, **kwargs):
        assert client.cookie == "sid=new" and client.token is None
        assert code == "print(123)\n"
        return {"status": "ok", "outputs": []}

    monkeypatch.setattr("jqcli.commands.research.execute_research_code", execute)
    result = CliRunner().invoke(main, [
        "--config", str(tmp_path / "config.json"), "--format", "json", "--non-interactive",
        "research", "exec", "--code-stdin", "--yes",
    ], input="print(123)\n")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "ok"
