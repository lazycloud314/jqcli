"""Local execution and credential-selection diagnostics without credential values."""
from __future__ import annotations

import hashlib
import os
import platform
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import __version__
from .api.diagnostics import credential_fingerprint
from .config import Config, default_env_path


def _credential_source(name: str, cli_value: str | None, configured: str | None, before: dict[str, bool]) -> str:
    if os.environ.get(name):
        return "process_environment" if before.get(name) else "env_file"
    if cli_value:
        return "command_line"
    return "config_file" if configured else "none"


def runtime_summary(
    *, config: Config, env_file: str | None, loaded_env: Path | None,
    env_before: dict[str, bool], cli_token: str | None, cli_cookie: str | None,
    token: str | None, cookie: str | None, api_base: str, timeout: float,
) -> dict[str, Any]:
    package = Path(__file__).resolve().parent
    hashes = {}
    for relative in ("cli.py", "api/client.py", "api/strategy.py", "api/diagnostics.py"):
        try:
            hashes[relative] = hashlib.sha256((package / relative).read_bytes()).hexdigest()[:16]
        except OSError:
            hashes[relative] = "unavailable"
    target = urlsplit(api_base)
    cookie_source = _credential_source("JQCLI_COOKIE", cli_cookie, config.cookie, env_before)
    external_cookie = os.environ.get("JQCLI_COOKIE") or cli_cookie
    if external_cookie and cookie == config.cookie and cookie != external_cookie:
        cookie_source = "refreshed_config_file"
    # No URL user info, path, query, username, password, or environment contents.
    return {
        "event": "runtime",
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "jqcli_version": __version__,
        "httpx_version": httpx.__version__,
        "package_dir": str(package),
        "source_hashes": hashes,
        "working_directory": str(Path.cwd()),
        "config_read_path": str(config.read_path.resolve()) if config.read_path else None,
        "config_write_path": str(config.path.resolve()),
        "env_file_requested": str(Path(env_file).expanduser().resolve() if env_file else default_env_path()),
        "env_file_loaded": str(loaded_env.resolve()) if loaded_env else None,
        "api_host": target.hostname,
        "api_scheme": target.scheme,
        "timeout_seconds": timeout,
        "httpx_trust_env": False,
        "proxy_environment_present": any(os.environ.get(name) for name in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
        )),
        "credentials": {
            "token": {
                "present": bool(token),
                "source": _credential_source("JQCLI_TOKEN", cli_token, config.token, env_before),
                "fingerprint": credential_fingerprint(token),
            },
            "cookie": {
                "present": bool(cookie),
                "source": cookie_source,
                "fingerprint": credential_fingerprint(cookie),
            },
            "both_token_and_cookie": bool(token and cookie),
        },
    }
