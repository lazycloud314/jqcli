from __future__ import annotations

import json
import os
import sys

import click

from jqcli.api.auth import login_with_password
from jqcli.api.diagnostics import credential_fingerprint
from jqcli.cli import AppContext
from jqcli.authentication import ensure_authentication
from jqcli.config import resolve_credentials, resolve_login_credentials, save_login_credentials
from jqcli.errors import UsageError
from jqcli.output import write_json


@click.group(name="auth")
def auth_group() -> None:
    """认证管理。"""


@auth_group.command("status")
@click.option("--live", is_flag=True, help="在线校验登录态；过期时尝试自动登录")
@click.pass_obj
def status(app: AppContext, live: bool) -> None:
    if live:
        ensure_authentication(app)
    authenticated = bool(app.token or app.cookie)
    payload = {
        "authenticated": authenticated,
        "api_base": app.api_base,
        "credential_source": "token" if app.token else "cookie" if app.cookie else None,
        "username": app.config.data.get("username"),
    }
    if app.json_output:
        write_json(payload)
    elif authenticated:
        click.echo("已配置认证凭据")
    else:
        click.echo("未登录")


@auth_group.command("logout")
@click.pass_obj
def logout(app: AppContext) -> None:
    app.config.data.pop("token", None)
    app.config.data.pop("cookie", None)
    app.config.data.pop("username", None)
    app.config.data.pop("superseded_cookie_fingerprints", None)
    app.config.save()
    if app.json_output:
        write_json({"ok": True})
    elif not app.quiet:
        click.echo("已清除本地认证凭据")


@auth_group.command("import-token")
@click.option("--token", required=True, help="要保存的 token")
@click.pass_obj
def import_token(app: AppContext, token: str) -> None:
    app.config.data["token"] = token
    app.config.data.pop("cookie", None)
    app.config.save()
    if app.json_output:
        write_json({"ok": True, "credential": "token"})
    elif not app.quiet:
        click.echo("token 已保存")


@auth_group.command("import-cookie")
@click.option("--cookie", required=True, help="要保存的 cookie")
@click.pass_obj
def import_cookie(app: AppContext, cookie: str) -> None:
    app.config.data["cookie"] = cookie
    app.config.data.pop("token", None)
    app.config.save()
    if app.json_output:
        write_json({"ok": True, "credential": "cookie"})
    elif not app.quiet:
        click.echo("cookie 已保存")


@auth_group.command("login")
@click.option("--username", help="用户名；省略时读取 JQCLI_USERNAME")
@click.option("--password-stdin", is_flag=True, help="从 stdin 读取密码")
@click.pass_obj
def login(app: AppContext, username: str | None, password_stdin: bool) -> None:
    password = sys.stdin.read() if password_stdin else None
    username, password = resolve_login_credentials(username=username, password=password)
    if not username:
        raise UsageError("缺少用户名，请传入 --username 或在 env 文件中设置 JQCLI_USERNAME")
    if not password:
        raise UsageError("缺少密码，请传入 --password-stdin 或在 env 文件中设置 JQCLI_PASSWORD")
    result = login_with_password(app.api_base, username, password, timeout=app.timeout)
    save_login_credentials(app.config, username=username, cookie=result["cookie"], previous_cookie=app.cookie)
    if app.debug:
        print(json.dumps({"debug": {
            "event": "login_saved",
            "config_write_path": str(app.config.path.resolve()),
            "saved_cookie_present": bool(app.config.cookie),
            "saved_cookie_fingerprint": credential_fingerprint(app.config.cookie),
            "saved_token_present": bool(app.config.token),
            "environment_cookie_overrides_saved_cookie": bool(
                os.environ.get("JQCLI_COOKIE") and resolve_credentials(app.config)[1] != app.config.cookie
            ),
            "environment_token_present": bool(os.environ.get("JQCLI_TOKEN")),
        }}, ensure_ascii=False), file=sys.stderr, flush=True)
    if app.json_output:
        write_json({"ok": True, "username": username, "credential": "cookie"})
    elif not app.quiet:
        click.echo("登录成功，cookie 已保存")
