"""Allowlisted response metadata; never include headers or response body values."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit


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


def payload_summary(payload: Any, *, depth: int = 0) -> dict[str, Any]:
    """Summarize only known envelope fields, never arbitrary keys or values."""
    summary: dict[str, Any] = {"type": type(payload).__name__}
    if not isinstance(payload, dict):
        return summary
    allowed = {"code", "status", "success", "msg", "message", "data", "error", "fileArr"}
    summary["fields"] = {key: type(payload[key]).__name__ for key in sorted(allowed & payload.keys())}
    summary["other_field_count"] = len(payload.keys() - allowed)
    for key in ("code", "status"):
        value = payload.get(key)
        # Only short numeric status codes are safe to echo. Strings may contain secrets.
        if type(value) in (str, int) and re.fullmatch(r"-?[0-9]{1,10}", str(value)):
            summary[key] = value
    if isinstance(payload.get("fileArr"), list):
        summary["folder_count"] = len(payload["fileArr"])
    if depth < 2:
        for key in ("data", "error"):
            if key in payload:
                summary[key] = payload_summary(payload[key], depth=depth + 1)
    return summary


def response_summary(response: Any) -> dict[str, Any]:
    # Never emit URL queries, user info, redirects, cookies, titles, or raw body text.
    path = urlsplit(str(response.request.url)).path
    endpoint = {
        "/algorithm/index/list": "strategy_list",
        "/algorithm/index/GetFileList": "strategy_folders",
    }.get(path, "other")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
    if content_type not in {"application/json", "text/html", "text/plain"}:
        content_type = "other_or_missing"
    summary = {
        "endpoint": endpoint,
        "http_status": response.status_code,
        "content_type": content_type,
        "body_bytes": len(response.content),
    }
    try:
        payload = response.json()
    except ValueError:
        summary["page"] = page_features(response.text)
    else:
        summary["json"] = payload_summary(payload)
    return summary
