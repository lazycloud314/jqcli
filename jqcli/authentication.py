"""Validate CLI credentials before business requests; refresh once when expired."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .api.auth import login_with_password
from .api.client import ApiClient
from .api.strategy import _read_strategy_list
from .config import resolve_login_credentials, save_login_credentials
from .errors import ApiError, NotAuthenticatedError

if TYPE_CHECKING:
    from .cli import AppContext


def _probe_login(app: AppContext, *, cookie: str | None, token: str | None = None) -> str | None:
    # Only one protected, read-only page; do not enumerate folders or assets.
    with ApiClient(app.api_base, cookie=cookie, token=token, timeout=app.timeout, debug=app.debug) as client:
        html = client.get_text("/algorithm/index/list")
        _read_strategy_list(client, html)
        return client.get_cookie_header("/algorithm/index/list")


def ensure_authentication(app: AppContext, *, required: bool = True, cookie_only: bool = False) -> None:
    if app.authentication_checked and (not cookie_only or app.cookie):
        return

    username, password = resolve_login_credentials()
    username = username or app.config.data.get("username")
    has_credentials = bool(app.cookie or (app.token and not cookie_only))
    if not has_credentials and not required and not (username and password):
        # Public community/read-only shared results still permit anonymous use.
        return

    expired = False
    if has_credentials:
        using_cookie = bool(app.cookie)
        try:
            # Validate the cookie independently of a possibly stale Bearer token.
            cookie = _probe_login(app, cookie=app.cookie, token=None if app.cookie else app.token)
        except NotAuthenticatedError:
            expired = True
        else:
            app.cookie = cookie
            if using_cookie:
                app.token = None
            app.authentication_checked = True
            return

    if not username or not password:
        reason = "登录凭据已过期" if expired else "研究平台需要有效 cookie" if cookie_only else "未登录"
        raise NotAuthenticatedError(
            reason + "；自动登录需要配置 JQCLI_USERNAME 和 JQCLI_PASSWORD，或先执行 auth login"
        )

    # Never replay the user's command: writes may already have taken effect.
    # Only the read-only probe and a single password login run before it starts.
    result = login_with_password(app.api_base, str(username), password, timeout=app.timeout)
    new_cookie = result.get("cookie")
    if not isinstance(new_cookie, str) or not new_cookie.strip():
        raise ApiError("登录接口未返回有效 Cookie，未执行原命令")
    verified_cookie = _probe_login(app, cookie=new_cookie)
    if not verified_cookie:
        raise NotAuthenticatedError("登录后未获得有效 Cookie，未执行原命令")
    save_login_credentials(
        app.config, username=str(username), cookie=verified_cookie, previous_cookie=app.cookie,
    )
    app.cookie = verified_cookie
    app.token = None
    app.authentication_checked = True
