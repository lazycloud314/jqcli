from __future__ import annotations

import json
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit

from jqcli.errors import ApiError, NotAuthenticatedError, UsageError

from .client import ApiClient


_JS_STRING = r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')'''
_SSO_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_SSO_REDIRECTS = 12
_RESEARCH_ITEM_FIELDS = (
    "name",
    "path",
    "type",
    "writable",
    "created",
    "last_modified",
    "mimetype",
    "format",
    "size",
)


class _BaseUrlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "body":
            return
        for name, value in attrs:
            if name.lower() == "data-base-url" and value is not None:
                self.values.append(value)


def normalize_research_path(path: str) -> str:
    """Return the canonical, root-relative path accepted by Jupyter Contents."""
    if not isinstance(path, str):
        raise UsageError("研究平台路径必须是字符串")

    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        return ""

    components = normalized.split("/")
    if any(component in {"", ".", ".."} or "\x00" in component for component in components):
        raise UsageError("研究平台路径包含无效组件")
    return "/".join(components)


def list_research_items(client: ApiClient, path: str = "") -> dict[str, Any]:
    normalized_path = normalize_research_path(path)
    base_path = _bootstrap_research(client)
    payload = _get_contents(client, base_path, normalized_path, include_content=True)
    content = payload.get("content")
    if payload.get("type") != "directory" or not isinstance(content, list):
        raise ApiError("研究平台返回了无效的目录内容")

    items = [_normalize_item(item, include_content=False) for item in content]
    return {"path": normalized_path, "items": items, "total": len(items)}


def get_research_item(client: ApiClient, path: str, include_content: bool = False) -> dict[str, Any]:
    normalized_path = normalize_research_path(path)
    base_path = _bootstrap_research(client)
    payload = _get_contents(client, base_path, normalized_path, include_content=include_content)
    return _normalize_item(payload, include_content=include_content)


def save_research_item(
    client: ApiClient,
    path: str,
    *,
    content: Any,
    item_type: str,
    content_format: str,
) -> dict[str, Any]:
    normalized_path = _normalize_write_path(path)
    _validate_save_payload(content, item_type=item_type, content_format=content_format)
    base_path, xsrf_token = _prepare_research_write(client, normalized_path)
    payload = client.put(
        _contents_endpoint(base_path, normalized_path),
        json={"type": item_type, "format": content_format, "content": content},
        headers={"X-XSRFToken": xsrf_token},
    )
    return _normalize_item(payload, include_content=False)


def create_research_directory(client: ApiClient, path: str) -> dict[str, Any]:
    normalized_path = _normalize_write_path(path)
    base_path, xsrf_token = _prepare_research_write(client, normalized_path)
    payload = client.put(
        _contents_endpoint(base_path, normalized_path),
        json={"type": "directory"},
        headers={"X-XSRFToken": xsrf_token},
    )
    return _normalize_item(payload, include_content=False)


def move_research_item(client: ApiClient, path: str, destination: str) -> dict[str, Any]:
    normalized_path = _normalize_write_path(path)
    normalized_destination = _normalize_write_path(destination)
    base_path, xsrf_token = _prepare_research_write(client, normalized_path)
    payload = client.patch(
        _contents_endpoint(base_path, normalized_path),
        json={"path": normalized_destination},
        headers={"X-XSRFToken": xsrf_token},
    )
    return _normalize_item(payload, include_content=False)


def delete_research_item(client: ApiClient, path: str) -> None:
    normalized_path = _normalize_write_path(path)
    base_path, xsrf_token = _prepare_research_write(client, normalized_path)
    response = client.request_response(
        "DELETE",
        _contents_endpoint(base_path, normalized_path),
        headers={"X-XSRFToken": xsrf_token},
    )
    if response.status_code != 204:
        raise ApiError(f"研究平台删除返回了意外状态（HTTP {response.status_code}）")
    return None


def _bootstrap_research(client: ApiClient) -> str:
    script = client.get_text("/default/research/redirect")
    mob, session_id, login_url = _parse_sso_script(script, details=client.last_response_details)
    validated_login_url = _validate_sso_url(client.api_base, login_url)

    login_html = _submit_sso_login(client, validated_login_url, mob=mob, session_id=session_id)
    return _parse_base_path(login_html, details=client.last_response_details)


def _submit_sso_login(client: ApiClient, login_url: str, *, mob: str, session_id: str) -> str:
    method = "POST"
    target = login_url
    form: dict[str, str] | None = {"username": mob, "token": session_id}

    for _ in range(_MAX_SSO_REDIRECTS + 1):
        kwargs: dict[str, Any] = {"data": form} if form is not None else {}
        response = client.request_response(
            method,
            target,
            allow_redirect_status=True,
            **kwargs,
        )
        if response.status_code not in _SSO_REDIRECT_STATUSES:
            if 300 <= response.status_code < 400:
                raise ApiError(f"研究平台返回了不支持的重定向（HTTP {response.status_code}）")
            return response.text

        location = response.headers.get("location")
        if not location:
            raise ApiError("研究平台登录重定向缺少目标地址")
        target = _validate_sso_url(client.api_base, urljoin(str(response.url), location))

        if response.status_code == 303 or (response.status_code in {301, 302} and method != "GET"):
            method = "GET"
            form = None

    raise ApiError("研究平台登录重定向次数过多")


def _parse_sso_script(script: str, *, details: dict[str, Any] | None = None) -> tuple[str, str, str]:
    try:
        mob = _extract_js_assignment(script, "mob")
        session_id = _extract_js_assignment(script, "sessionId")
        call = re.search(
            rf"\bCy\s*\.\s*postRedirect\s*\(\s*(?P<url>{_JS_STRING})\s*,\s*"
            rf"(?P<form>\{{.*?\}}|[A-Za-z_$][\w$]*)\s*\)",
            script,
            flags=re.DOTALL,
        )
        if call is None:
            raise ValueError
        form = _resolve_sso_form(script, call.group("form"))
        if not _has_js_binding(form, "username", "mob") or not _has_js_binding(form, "token", "sessionId"):
            raise ValueError
        login_url = _decode_js_string(call.group("url"))
        if not mob or not session_id or not login_url:
            raise ValueError
    except (TypeError, ValueError):
        # Never include the script: it contains the user's login identifier and
        # one-time SSO token.
        raise NotAuthenticatedError("无法获取有效的研究平台登录信息", details=details) from None
    return mob, session_id, login_url


def _resolve_sso_form(script: str, expression: str) -> str:
    value = expression.strip()
    if value.startswith("{") and value.endswith("}"):
        return value[1:-1]
    if re.fullmatch(r"[A-Za-z_$][\w$]*", value) is None:
        raise ValueError
    assignment = re.search(
        rf"\b(?:var|let|const)\s+{re.escape(value)}\s*=\s*\{{(?P<form>.*?)\}}\s*;?",
        script,
        flags=re.DOTALL,
    )
    if assignment is None:
        raise ValueError
    return assignment.group("form")


def _extract_js_assignment(script: str, name: str) -> str:
    match = re.search(rf"\bvar\s+{re.escape(name)}\s*=\s*(?P<value>{_JS_STRING})\s*;", script)
    if match is None:
        raise ValueError
    return _decode_js_string(match.group("value"))


def _has_js_binding(value: str, key: str, variable: str) -> bool:
    key_pattern = rf'''(?:["']{re.escape(key)}["']|\b{re.escape(key)}\b)'''
    return re.search(rf"{key_pattern}\s*:\s*\b{re.escape(variable)}\b", value) is not None


def _decode_js_string(literal: str) -> str:
    if len(literal) < 2 or literal[0] not in {'"', "'"} or literal[-1] != literal[0]:
        raise ValueError
    value = literal[1:-1]
    decoded: list[str] = []
    simple_escapes = {
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "v": "\v",
        "0": "\x00",
    }
    index = 0
    while index < len(value):
        character = value[index]
        if character != "\\":
            decoded.append(character)
            index += 1
            continue
        index += 1
        if index >= len(value):
            raise ValueError
        escape = value[index]
        if escape in simple_escapes:
            decoded.append(simple_escapes[escape])
            index += 1
        elif escape in {"x", "u"}:
            width = 2 if escape == "x" else 4
            digits = value[index + 1 : index + 1 + width]
            if len(digits) != width or re.fullmatch(r"[0-9a-fA-F]+", digits) is None:
                raise ValueError
            decoded.append(chr(int(digits, 16)))
            index += width + 1
        elif escape in {"\n", "\r"}:
            # JavaScript line continuation.
            if escape == "\r" and index + 1 < len(value) and value[index + 1] == "\n":
                index += 1
            index += 1
        else:
            decoded.append(escape)
            index += 1
    result = "".join(decoded)
    if any(0xD800 <= ord(character) <= 0xDFFF for character in result):
        raise ValueError
    return result


def _validate_sso_url(api_base: str, login_url: str) -> str:
    try:
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in login_url):
            raise ValueError
        absolute_url = urljoin(f"{api_base.rstrip('/')}/", unescape(login_url))
        api = urlsplit(api_base)
        login = urlsplit(absolute_url)
        if (
            api.scheme.lower() not in {"http", "https"}
            or login.scheme.lower() not in {"http", "https"}
            or not login.hostname
            or login.username is not None
            or login.password is not None
            or login.fragment
            or _url_origin(api) != _url_origin(login)
        ):
            raise ValueError
        # Accessing .port above/below also rejects malformed port syntax.
        _ = login.port
    except (UnicodeError, ValueError):
        raise ApiError("研究平台返回了无效的登录地址") from None
    return absolute_url


def _url_origin(parsed: Any) -> tuple[str, str, int | None]:
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, hostname, port


def _parse_base_path(html: str, *, details: dict[str, Any] | None = None) -> str:
    parser = _BaseUrlParser()
    try:
        parser.feed(html)
    except (TypeError, ValueError):
        raise NotAuthenticatedError("无法建立研究平台登录会话", details=details) from None
    if len(parser.values) != 1:
        raise NotAuthenticatedError(
            "无法建立研究平台登录会话",
            details={**(details or {}), "base_url_count": len(parser.values)},
        )

    value = unescape(parser.values[0])
    try:
        parsed = urlsplit(value)
        match = re.fullmatch(r"/user/([^/]+)/", parsed.path)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or match is None:
            raise ValueError
        user_segment = match.group(1)
        if re.search(r"%(?![0-9a-fA-F]{2})", user_segment):
            raise ValueError
        decoded_user = unquote(user_segment, errors="strict")
        if decoded_user in {"", ".", ".."} or any(character in decoded_user for character in ("/", "\\", "\x00")):
            raise ValueError
    except (UnicodeError, ValueError):
        raise ApiError("研究平台返回了无效的用户路径") from None
    return parsed.path


def _get_contents(
    client: ApiClient,
    base_path: str,
    path: str,
    *,
    include_content: bool,
) -> dict[str, Any]:
    endpoint = _contents_endpoint(base_path, path)
    payload = client.get(endpoint, params={"content": "1" if include_content else "0"})
    if not isinstance(payload, dict):
        raise ApiError("研究平台返回了无效的 Contents 响应")
    return payload


def _contents_endpoint(base_path: str, path: str) -> str:
    endpoint = f"{base_path}api/contents"
    if path:
        endpoint = f"{endpoint}/{quote(path, safe='/')}"
    return endpoint


def _normalize_write_path(path: str) -> str:
    normalized_path = normalize_research_path(path)
    if not normalized_path:
        raise UsageError("研究平台写操作不能以根目录为目标")
    return normalized_path


def _validate_save_payload(content: Any, *, item_type: str, content_format: str) -> None:
    if item_type == "notebook":
        if content_format != "json" or not isinstance(content, dict):
            raise ApiError("Notebook 内容必须是 JSON 对象")
        try:
            json.dumps(content, allow_nan=False)
        except (OverflowError, TypeError, ValueError):
            raise ApiError("Notebook 内容不是可序列化的 JSON 对象") from None
        return
    if item_type == "file":
        if content_format not in {"text", "base64"} or not isinstance(content, str):
            raise ApiError("文件内容必须是 text 或 base64 字符串")
        return
    raise ApiError("研究平台只支持保存 file 或 notebook")


def _prepare_research_write(client: ApiClient, target_path: str) -> tuple[str, str]:
    base_path = _bootstrap_research(client)
    client.get(f"{base_path}api")
    xsrf_cookie = client.get_cookie("_xsrf", _contents_endpoint(base_path, target_path))
    if not xsrf_cookie:
        raise ApiError("研究平台未提供写操作所需的 XSRF 令牌")
    xsrf_token = _decode_xsrf_cookie(xsrf_cookie)
    return base_path, xsrf_token


def _decode_xsrf_cookie(value: str) -> str:
    token = value
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
        token = token[1:-1]
    try:
        token = unquote(token, errors="strict")
    except UnicodeError:
        raise ApiError("研究平台返回了无效的 XSRF 令牌") from None
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
        token = token[1:-1]
    if not token or any(ord(character) < 0x20 or ord(character) == 0x7F for character in token):
        raise ApiError("研究平台返回了无效的 XSRF 令牌")
    return token


def _normalize_item(raw: Any, *, include_content: bool) -> dict[str, Any]:
    if not isinstance(raw, dict) or not all(isinstance(raw.get(key), str) for key in ("name", "path", "type")):
        raise ApiError("研究平台返回了无效的 Contents 项")
    item = {field: raw.get(field) for field in _RESEARCH_ITEM_FIELDS}
    if include_content:
        item["content"] = raw.get("content")
    return item


__all__ = [
    "create_research_directory",
    "delete_research_item",
    "get_research_item",
    "list_research_items",
    "move_research_item",
    "normalize_research_path",
    "save_research_item",
]
