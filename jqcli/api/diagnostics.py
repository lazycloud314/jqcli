"""Bounded, redacted diagnostics without raw headers, pages, or strategy data."""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urlsplit


_REDIRECT_FIELDS = {"url", "redirect", "redirect_url", "redirectUrl", "location", "login_url", "loginUrl", "return_url", "returnUrl"}
_LOGIN_PATHS = {"/user/login", "/user/login/index", "/hub/login", "/hub/login/index"}


def redirect_summary(value: Any, *, origin_host: str | None = None) -> dict[str, Any]:
    """Classify redirects without exposing arbitrary paths, queries, or user info."""
    summary: dict[str, Any] = {"type": type(value).__name__}
    if not isinstance(value, str):
        return summary
    if not value:
        return {**summary, "kind": "empty"}
    if len(value) > 4096:
        return {**summary, "kind": "overlong"}
    try:
        target = urlsplit(value)
        host = target.hostname
    except ValueError:
        return {**summary, "kind": "invalid"}
    scope = "relative_root" if value.startswith("/") and not target.netloc else "relative"
    if target.netloc:
        scope = "same_host" if origin_host and host == origin_host else "external"
    path = unquote(target.path).rstrip("/")
    kind = "login" if path in _LOGIN_PATHS else "other"
    if target.scheme and target.scheme not in {"http", "https"}:
        kind = "unsupported_scheme"
    summary.update({"kind": kind, "scope": scope, "has_query": bool(target.query), "has_fragment": bool(target.fragment)})
    if kind == "login":
        summary["known_path"] = path
    return summary


def has_login_redirect(payload: Any, *, origin_host: str | None = None, depth: int = 0) -> bool:
    if not isinstance(payload, dict) or depth > 2:
        return False
    for key in _REDIRECT_FIELDS & payload.keys():
        target = redirect_summary(payload[key], origin_host=origin_host)
        if target.get("kind") == "login" and target.get("scope") in {"relative_root", "same_host"}:
            return True
    return any(has_login_redirect(payload.get(key), origin_host=origin_host, depth=depth + 1) for key in ("data", "error"))


def credential_fingerprint(value: str | None) -> str | None:
    """Compare exact credential bytes across runs without logging credentials."""
    if not value:
        return None
    return "sha256:" + hashlib.sha256(("jqcli-diagnostic-v1\0" + value).encode("utf-8")).hexdigest()[:16]


def endpoint_label(url: str) -> str:
    return {
        "/algorithm/index/list": "strategy_list",
        "/algorithm/index/GetFileList": "strategy_folders",
    }.get(urlsplit(url).path, "other")


def _payload_secrets(payload: Any) -> tuple[str, ...]:
    values = []
    pending = [(payload, 0)]
    for _ in range(256):
        if not pending:
            break
        item, depth = pending.pop()
        if depth > 6:
            continue
        if isinstance(item, dict):
            for key, value in item.items():
                name = re.sub(r"[_-]", "", str(key)).lower()
                if isinstance(value, str) and (
                    any(word in name for word in ("token", "cookie", "password", "secret", "session"))
                    or name in {"authorization", "pwd", "sid", "xsrf", "apikey"}
                ):
                    values.append(value)
                elif isinstance(value, (dict, list)):
                    pending.append((value, depth + 1))
        elif isinstance(item, list):
            pending.extend((value, depth + 1) for value in item[:256] if isinstance(value, (dict, list)))
    return tuple(values)


def redact_message(value: str, secret_values: tuple[str, ...] = ()) -> str:
    """Retain an error explanation while removing credentials and URL queries."""
    # Bound pattern matching as well as the final output for very long messages.
    input_truncated = len(value) > 4096
    if input_truncated:
        for secret in secret_values:
            if secret:
                position = value.find(secret, max(0, 4097 - len(secret)))
                if 0 <= position < 4096:
                    return "[message containing long credential omitted]"
    value = value[:4096]
    if re.search(r"<[a-z!/][^>]*>", value, re.I):
        return "[HTML message omitted]"
    if re.search(r"\bdef\s+\w+\s*\(|\b(?:strategy_code|source_code|algorithm\[code\])\s*[:=]", value):
        return "[code message omitted]"
    value = re.sub(r"https?://[^\s<>\"']+", "[URL omitted]", value, flags=re.I)
    value = re.sub(r"\b(?:cookie|set-cookie|authorization)\s*:\s*[^\r\n]*", "[credential header omitted]", value, flags=re.I)
    value = re.sub(r"\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", value, flags=re.I)
    value = re.sub(
        r'''(?i)(["']?(?:[\w-]*(?:token|cookie|password|secret|session|api[_-]?key)[\w-]*|authorization|pwd|sid|_xsrf)["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}&]+)''',
        r"\1[REDACTED]", value,
    )
    value = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[REDACTED]", value)
    # Apply literals after patterns, so a short token cannot obscure a field
    # name such as 'token=' before that field's value has been redacted.
    for secret in sorted(set(secret_values), key=len, reverse=True):
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    return value[:500] + (" [truncated]" if len(value) > 500 or input_truncated else "")


class _PageFeatures(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.features = {
            "login_form": False,
            "password_field": False,
            "strategy_list_script": False,
            "strategy_rows": 0,
            "strategy_links": 0,
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            action = str(values.get("action") or "").lower()
            self.features["login_form"] |= any(path in action for path in ("/user/login", "/hub/login"))
        if tag == "input" and str(values.get("type") or "").lower() == "password":
            self.features["password_field"] = True
        if tag == "script":
            source = str(values.get("src") or "").split("?", 1)[0]
            self.features["strategy_list_script"] |= source.endswith("/algorithm/js/list.min.js")
        if tag == "tr" and "algorithm_list" in str(values.get("class") or "").split():
            self.features["strategy_rows"] += 1
        if tag == "a" and "/algorithm/index/edit?" in str(values.get("href") or ""):
            self.features["strategy_links"] += 1


def page_features(html: str) -> dict[str, Any]:
    parser = _PageFeatures()
    parser.feed(html)
    return parser.features


def payload_summary(
    payload: Any, *, depth: int = 0, secret_values: tuple[str, ...] = (), include_messages: bool = True,
    origin_host: str | None = None,
) -> dict[str, Any]:
    """Summarize known envelope fields and bounded, redacted error messages."""
    summary: dict[str, Any] = {"type": type(payload).__name__}
    if not isinstance(payload, dict):
        return summary
    secret_values = secret_values + _payload_secrets(payload)
    allowed = {"code", "status", "success", "msg", "message", "data", "error", "fileArr"} | _REDIRECT_FIELDS
    summary["fields"] = {key: type(payload[key]).__name__ for key in sorted(allowed & payload.keys())}
    summary["other_field_count"] = len(payload.keys() - allowed)
    for key in sorted(_REDIRECT_FIELDS & payload.keys()):
        summary[key] = redirect_summary(payload[key], origin_host=origin_host)
    for key in ("code", "status"):
        value = payload.get(key)
        # Only short numeric status codes are safe to echo. Strings may contain secrets.
        if type(value) in (str, int) and re.fullmatch(r"-?[0-9]{1,10}", str(value)):
            summary[key] = value
    if include_messages:
        for key in ("msg", "message", "error"):
            if isinstance(payload.get(key), str):
                summary[key] = redact_message(payload[key], secret_values)
    if isinstance(payload.get("fileArr"), list):
        summary["folder_count"] = len(payload["fileArr"])
    if depth < 2:
        for key in ("data", "error"):
            if key in payload:
                if isinstance(payload[key], str) and key == "error":
                    continue
                summary[key] = payload_summary(
                    payload[key], depth=depth + 1, secret_values=secret_values, include_messages=include_messages,
                    origin_host=origin_host,
                )
    return summary


def response_summary(response: Any, *, secret_values: tuple[str, ...] = (), elapsed_ms: float | None = None) -> dict[str, Any]:
    # Never emit URL queries, user info, redirects, cookies, titles, or raw body text.
    endpoint = endpoint_label(str(response.request.url))
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
    if content_type not in {"application/json", "text/html", "text/plain"}:
        content_type = "other_or_missing"
    summary = {
        "endpoint": endpoint,
        "method": response.request.method,
        "http_status": response.status_code,
        "content_type": content_type,
        "body_bytes": len(response.content),
        "body_sha256": hashlib.sha256(response.content).hexdigest(),
    }
    if elapsed_ms is not None:
        summary["elapsed_ms"] = round(elapsed_ms, 2)
    trace_ids = {}
    for name in ("x-request-id", "x-correlation-id", "traceparent"):
        value = response.headers.get(name, "")
        if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
            trace_ids[name] = redact_message(value, secret_values)
    if trace_ids:
        summary["trace_ids"] = trace_ids
    if response.headers.get("location"):
        summary["redirect"] = redirect_summary(response.headers["location"], origin_host=response.request.url.host)
    if len(response.content) > 2 * 1024 * 1024:
        summary["body_analysis_skipped"] = "over_2mb"
        return summary
    if content_type == "other_or_missing" and not response.content.lstrip().startswith((b"{", b"[", b"<")):
        return summary
    try:
        payload = response.json()
    except ValueError:
        summary["page"] = page_features(response.text)
    else:
        failed = isinstance(payload, dict) and (
            any(str(payload[key]) not in {"00000", "0"} for key in ("code", "status") if key in payload)
            or payload.get("success") is False or bool(payload.get("error"))
        )
        summary["json"] = payload_summary(
            payload, secret_values=secret_values, include_messages=failed, origin_host=response.request.url.host,
        )
    return summary


def network_summary(error: Exception, *, method: str, elapsed_ms: float) -> dict[str, Any]:
    """Exception names and errno only: exception messages may contain credentials."""
    chain = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        item: dict[str, Any] = {"type": type(current).__name__}
        if isinstance(current, OSError) and isinstance(current.errno, int):
            item["errno"] = current.errno
        chain.append(item)
        current = current.__cause__ or current.__context__
    names = {item["type"] for item in chain}
    category = "network"
    if "gaierror" in names:
        category = "dns"
    elif any("Timeout" in name for name in names):
        category = "timeout"
    elif names & {"SSLError", "SSLCertVerificationError", "CertificateError"}:
        category = "tls"
    elif "ConnectionRefusedError" in names:
        category = "connection_refused"
    return {"method": method, "category": category, "elapsed_ms": round(elapsed_ms, 2), "exception_chain": chain}
