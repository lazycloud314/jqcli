import json
from importlib import import_module
import socket
from types import SimpleNamespace

import httpx
import pytest

from jqcli.api.client import ApiClient
from jqcli.api.diagnostics import payload_summary, redact_message, response_summary
from jqcli.api.strategy import list_strategies, list_strategy_folders
from jqcli.errors import ApiError, NetworkError


@pytest.mark.parametrize("message", [
    'token=private-value password="private-value"',
    '{"access_token":"private-value","client_secret":"private-value"}',
    "Cookie: sid=private-value; another=private-value",
    "Authorization: Bearer private-value",
    "https://user:private-value@example.test/path?token=private-value",
    "session_id: private-value api-key=private-value",
])
def test_messages_redact_credential_patterns(message):
    assert "private-value" not in redact_message(message)


def test_summary_redacts_known_and_payload_credentials():
    payload = {
        "code": "10001", "status": "1",
        "msg": "请求失败：known-secret reflected-secret",
        "token": "reflected-secret",
    }
    summary = payload_summary(payload, secret_values=("known-secret",))
    assert summary["code"] == "10001"
    assert summary["msg"].startswith("请求失败")
    assert "secret" not in json.dumps(summary)


def test_short_known_token_does_not_disable_field_redaction():
    result = redact_message("token=private-value password=private-password", ("tok",))
    assert "private-value" not in result
    assert "private-password" not in result


def test_credential_spanning_analysis_limit_is_not_partially_exposed():
    secret = "private-value" * 500
    result = redact_message("请求失败 " + secret, (secret,))
    assert "private-value" not in result


def test_messages_are_bounded_and_omit_html_and_source():
    assert len(redact_message("a" * 1000)) < 520
    assert "private" not in redact_message("<script>private</script>")
    assert "private" not in redact_message("def initialize(context): private()")
    assert "\x1b" not in redact_message("failed\x1b[31m")
    assert payload_summary({"msg": "", "code": "10001"})["msg"] == ""


def test_success_messages_are_not_collected():
    response = httpx.Response(200, json={"code": "00000", "msg": "private-success-message"},
                              request=httpx.Request("GET", "https://example.test"))
    assert "private-success-message" not in json.dumps(response_summary(response))


def test_http_200_html_typed_failure_json_is_collected_and_rejected(capsys):
    payload = {"code": "10001", "status": "1", "msg": "服务端拒绝请求：known-secret"}
    def handler(request):
        return httpx.Response(200, json=payload, headers={
            "content-type": "text/html", "x-request-id": "request-123", "set-cookie": "sid=secret-cookie",
        })
    with ApiClient("https://example.test", token="known-secret", debug=True,
                   transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ApiError) as caught:
            list_strategies(client)
    details = caught.value.details
    assert details["response"]["code"] == "10001"
    assert "服务端拒绝请求" in details["response"]["msg"]
    assert details["http"]["http_status"] == 200
    assert details["http"]["trace_ids"] == {"x-request-id": "request-123"}
    assert details["http"]["elapsed_ms"] >= 0
    assert len(details["http"]["body_sha256"]) == 64
    output = capsys.readouterr().err + json.dumps(details)
    assert "known-secret" not in output
    assert "secret-cookie" not in output


def test_folder_errors_use_the_same_redaction_without_debug():
    payload = {"code": "10001", "status": "1", "message": "拒绝 known-secret password=private-password"}
    with ApiClient("https://example.test", cookie="sid=known-secret",
                   transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        with pytest.raises(ApiError) as caught:
            list_strategy_folders(client)
    assert "拒绝" in caught.value.details["response"]["message"]
    assert "known-secret" not in json.dumps(caught.value.details)
    assert "private-password" not in json.dumps(caught.value.details)


def test_network_error_preserves_safe_cause_chain(capsys):
    def handler(request):
        try:
            raise socket.gaierror(8, "private-secret")
        except socket.gaierror as exc:
            raise httpx.ConnectError("https://user:private-secret@example.test") from exc
    with ApiClient("https://example.test", debug=True, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NetworkError) as caught:
            client.get("/path?token=private-secret")
    chain = caught.value.details["exception_chain"]
    assert chain == [{"type": "ConnectError"}, {"type": "gaierror", "errno": 8}]
    assert caught.value.details["category"] == "dns"
    assert "private-secret" not in capsys.readouterr().err + json.dumps(caught.value.details)


def test_http_errors_include_safe_server_message():
    with ApiClient("https://example.test", transport=httpx.MockTransport(
        lambda request: httpx.Response(500, json={"code": "500", "msg": "服务器繁忙"}),
    )) as client:
        with pytest.raises(ApiError) as caught:
            client.get("/path")
    assert caught.value.details["http"]["json"]["msg"] == "服务器繁忙"


def test_large_response_analysis_is_bounded():
    response = httpx.Response(200, text="x" * (2 * 1024 * 1024 + 1),
                              request=httpx.Request("GET", "https://example.test"))
    assert response_summary(response)["body_analysis_skipped"] == "over_2mb"


def test_decoded_cookie_values_are_redacted():
    with ApiClient("https://example.test", cookie=r'sid="abc\073def"', transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"code": "10001", "msg": "拒绝 abc;def"}),
    )) as client:
        with pytest.raises(ApiError) as caught:
            list_strategies(client)
    assert "abc;def" not in json.dumps(caught.value.details)


@pytest.mark.parametrize("module_name,factory_name", [
    ("strategy", "make_client"), ("backtest", "make_client"),
    ("backtest", "make_optional_client"), ("community", "make_client"), ("research", "make_client"),
])
def test_command_clients_emit_requested_debug(module_name, factory_name, monkeypatch, capsys):
    module = import_module("jqcli.commands." + module_name)
    def make_test_client(api_base, **kwargs):
        return ApiClient(api_base, **kwargs, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": 1, "msg": "请求失败"}),
        ))
    monkeypatch.setattr(module, "ApiClient", make_test_client)
    app = SimpleNamespace(api_base="https://example.test", token="private-token",
                          cookie="sid=private-cookie", timeout=10, debug=True)
    with getattr(module, factory_name)(app) as client:
        client.get("/test")
    output = capsys.readouterr().err
    assert json.loads(output.splitlines()[-1])["debug"]["json"]["msg"] == "请求失败"
    assert "private-token" not in output
    assert "private-cookie" not in output


def test_request_events_correlate_auth_and_cookie_rotation(capsys):
    seen = []
    def handler(request):
        seen.append(request.headers["cookie"])
        return httpx.Response(200, json={"status": 0}, headers={"set-cookie": "sid=rotated-secret; Path=/"})
    with ApiClient("https://example.test", token="private-token", cookie="sid=initial-secret", debug=True,
                   transport=httpx.MockTransport(handler)) as client:
        client.get("/first")
        client.get("/second")
    output = capsys.readouterr().err
    events = [json.loads(line)["debug"] for line in output.splitlines()]
    assert [event["event"] for event in events] == ["request", "response", "request", "response"]
    assert events[0]["request_id"] == events[1]["request_id"]
    assert events[2]["request_id"] == events[3]["request_id"]
    assert events[0]["request_id"] != events[2]["request_id"]
    assert events[0]["selected_auth"]["both_cookie_and_authorization"] is True
    assert events[1]["sent_auth"]["cookie_pair_count"] == 1
    assert events[1]["response_cookie_count"] == 1
    assert events[1]["cookies_persisted_to_config"] is False
    assert events[0]["selected_auth"]["cookie_fingerprint"] != events[2]["selected_auth"]["cookie_fingerprint"]
    assert seen == ["sid=initial-secret", "sid=rotated-secret"]
    assert all(secret not in output for secret in ("initial-secret", "rotated-secret", "private-token"))


def test_request_diagnostics_distinguish_cookiejar_from_cookies_sent(capsys):
    def handler(request):
        assert "cookie" not in request.headers
        return httpx.Response(200, json={"status": 0})
    with ApiClient("https://example.test", debug=True, transport=httpx.MockTransport(handler)) as client:
        client._client.cookies.set("sid", "private-value", domain="other.test")
        client.get("/test")
    events = [json.loads(line)["debug"] for line in capsys.readouterr().err.splitlines()]
    assert events[0]["cookiejar_count"] == 1
    assert events[0]["selected_auth"]["cookie_present"] is False
    assert events[1]["sent_auth"]["cookie_present"] is False
