from urllib.parse import parse_qs

import httpx
import pytest

from jqcli.api.client import ApiClient
from jqcli.api.strategy import (
    create_strategy,
    create_strategy_folder,
    delete_strategy,
    get_strategy,
    list_strategies,
    list_strategy_folders,
    move_strategy,
    parse_strategy_edit_html,
    parse_strategy_list_html,
    update_strategy,
)
from jqcli.errors import ApiError, NotFoundError, UsageError


def client_with(handler):
    return ApiClient("https://example.test", token="tok", transport=httpx.MockTransport(handler))


STRATEGY_LIST_HTML = """
<table>
  <tr class="algorithm_list">
    <td><div _algorithmId="c8546c1d149c650ec420bbee2fe12637"><input type="checkbox"></div></td>
    <td><a href="/algorithm/index/edit?algorithmId=41faea7d0299bc92c71364d9cd878259&amp;backtest=1">混合策略</a></td>
    <td>Code</td>
    <td>2025-10-08 12:48:07</td>
    <td>0</td>
    <td>1</td>
    <td><a href="/algorithm/backtest/list?algorithmId=41faea7d0299bc92c71364d9cd878259">回测</a></td>
  </tr>
  <tr class="algorithm_list">
    <td><span _algorithmId="internal-2"></span></td>
    <td><a href="/algorithm/index/edit?algorithmId=edit-2">全天候ETF</a></td>
    <td>Code</td>
    <td>2025-07-01 11:18:21</td>
    <td>2</td>
    <td>3</td>
    <td></td>
  </tr>
</table>
"""

STRATEGY_EDIT_HTML = """
<form name="AlgorithmModel">
  <input type="hidden" id="backtestId" value="bt-hidden">
  <input type="hidden" id="algorithmId" name="algorithm[algorithmId]" value="save-id">
  <input type="hidden" name="algorithm[userId]" value="u1">
  <input type="hidden" name="algorithm[accessControl]" id="accessControl" value="0">
  <input type="text" name="algorithm[name]" id="title-box" value="策略名">
  <input type="hidden" name="backtest[type]" id="type" value="1">
  <textarea name="algorithm[code]" id="code">print(1)</textarea>
</form>
"""


def test_parse_strategy_list_html():
    payload = parse_strategy_list_html(STRATEGY_LIST_HTML)

    assert payload == {
        "items": [
            {
                "id": "41faea7d0299bc92c71364d9cd878259",
                "internal_id": "c8546c1d149c650ec420bbee2fe12637",
                "name": "混合策略",
                "type": "Code",
                "created_at": "",
                "updated_at": "2025-10-08 12:48:07",
                "run_count": 0,
                "backtest_count": 1,
            },
            {
                "id": "edit-2",
                "internal_id": "internal-2",
                "name": "全天候ETF",
                "type": "Code",
                "created_at": "",
                "updated_at": "2025-07-01 11:18:21",
                "run_count": 2,
                "backtest_count": 3,
            },
        ]
    }


def test_list_strategies_requests_real_list_page():
    def handler(request):
        assert request.url.path == "/algorithm/index/list"
        return httpx.Response(200, text=STRATEGY_LIST_HTML)

    payload = list_strategies(client_with(handler), limit=1)

    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == "41faea7d0299bc92c71364d9cd878259"


def test_list_strategies_can_sort_by_name_and_return_all():
    client = client_with(lambda request: httpx.Response(200, text=STRATEGY_LIST_HTML))

    payload = list_strategies(client, sort="name", limit=1, all_items=True)

    assert [item["name"] for item in payload["items"]] == ["全天候ETF", "混合策略"]


def test_list_strategies_follows_pages_and_folders():
    root_page_1 = """
    <table>
      <tbody>
        <tr class="algorithm_list">
          <td _fId="f1"></td>
          <td><a href="/algorithm/index/list?fId=f1">文件夹</a></td>
          <td></td><td></td><td></td><td></td><td></td>
        </tr>
      </tbody>
    </table>
    <a href="/algorithm/index/list?page=2">2</a>
    """
    root_page_2 = """
    <table>
      <tbody>
        <tr class="algorithm_list">
          <td><span _algorithmId="internal-root"></span></td>
          <td><a href="/algorithm/index/edit?algorithmId=root-id">根策略</a></td>
          <td>Code</td><td>2025-01-02 00:00:00</td><td>1</td><td>2</td><td></td>
        </tr>
      </tbody>
    </table>
    """
    folder_page = """
    <table>
      <tbody>
        <tr class="algorithm_list">
          <td><span _algorithmId="internal-folder"></span></td>
          <td><a href="/algorithm/index/edit?algorithmId=folder-id">文件夹策略</a></td>
          <td>Code</td><td>2025-01-03 00:00:00</td><td>3</td><td>4</td><td></td>
        </tr>
      </tbody>
    </table>
    """
    seen: list[tuple[str, dict[str, str]]] = []

    def handler(request):
        seen.append((request.url.path, dict(request.url.params)))
        params = request.url.params
        if params.get("fId") == "f1":
            return httpx.Response(200, text=folder_page)
        if params.get("page") == "2":
            return httpx.Response(200, text=root_page_2)
        return httpx.Response(200, text=root_page_1)

    payload = list_strategies(client_with(handler), all_items=True)

    assert [item["id"] for item in payload["items"]] == ["folder-id", "root-id"]
    assert payload["items"][0]["folder_id"] == "f1"
    assert seen == [
        ("/algorithm/index/list", {}),
        ("/algorithm/index/list", {"page": "2"}),
        ("/algorithm/index/list", {"fId": "f1"}),
    ]


def test_parse_strategy_edit_html():
    payload = parse_strategy_edit_html(STRATEGY_EDIT_HTML, requested_id="public-id")

    assert payload["id"] == "public-id"
    assert payload["save_id"] == "save-id"
    assert payload["backtest_id"] == "bt-hidden"
    assert payload["name"] == "策略名"
    assert payload["code"] == "print(1)"


def test_get_strategy_reads_edit_page():
    def handler(request):
        assert request.url.path == "/algorithm/index/edit"
        assert request.url.params["algorithmId"] == "s1"
        return httpx.Response(200, text=STRATEGY_EDIT_HTML)

    assert get_strategy(client_with(handler), "s1", include_code=True)["code"] == "print(1)"


def test_update_strategy_payload():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/algorithm/index/edit":
            return httpx.Response(200, text=STRATEGY_EDIT_HTML)
        if request.url.path == "/algorithm/index/list":
            return httpx.Response(200, text=STRATEGY_LIST_HTML)
        assert request.url.path == "/algorithm/index/save"
        form = parse_qs(request.content.decode())
        assert form["algorithm[algorithmId]"] == ["save-id"]
        assert form["algorithm[name]"] == ["new"]
        assert form["encrType"] == ["base64"]
        return httpx.Response(200, json={"data": {"algorithmId": "saved"}})

    payload = update_strategy(client_with(handler), "s1", name="new", code="print(2)")

    assert payload["save_id"] == "saved"
    assert seen == ["/algorithm/index/edit", "/algorithm/index/save", "/algorithm/index/list"]


def test_create_strategy_payload():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/algorithm/index/new":
            form = parse_qs(request.content.decode())
            assert form["code"] == ["print(1)"]
            return httpx.Response(200, json={"data": {"algorithmId": "s1"}})
        if request.url.path == "/algorithm/index/edit":
            return httpx.Response(200, text=STRATEGY_EDIT_HTML)
        if request.url.path == "/algorithm/index/save":
            return httpx.Response(200, json={"data": {"algorithmId": "save-id"}})
        if request.url.path == "/algorithm/index/list":
            return httpx.Response(200, text=STRATEGY_LIST_HTML)
        raise AssertionError(request.url.path)

    assert create_strategy(client_with(handler), name="n", strategy_type="stock", code="print(1)")["id"] == "s1"
    assert seen == [
        "/algorithm/index/new",
        "/algorithm/index/edit",
        "/algorithm/index/save",
        "/algorithm/index/list",
        "/algorithm/index/list",
    ]


def test_delete_strategy():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/algorithm/index/list":
            return httpx.Response(200, text=STRATEGY_LIST_HTML)
        assert request.url.path == "/algorithm/index/del"
        form = parse_qs(request.content.decode())
        assert form["algorithmId"] == ["c8546c1d149c650ec420bbee2fe12637"]
        return httpx.Response(200, json={"status": 0})

    payload = delete_strategy(client_with(handler), "41faea7d0299bc92c71364d9cd878259")

    assert payload["ok"] is True
    assert seen == ["/algorithm/index/list", "/algorithm/index/del"]


def folder_response():
    return {"code": "00000", "data": {"fileArr": [{"fId": "f1", "name": "对比研究"}]}}


def test_list_strategy_folders_uses_frontend_protocol():
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/algorithm/index/GetFileList"
        assert dict(request.url.params) == {"pId": "parent", "ajax": "1"}
        return httpx.Response(200, json=folder_response())

    assert list_strategy_folders(client_with(handler), parent_id="parent") == {
        "items": [{"id": "f1", "name": "对比研究", "parent_id": "parent"}],
    }


@pytest.mark.parametrize("response", [
    {"code": "10001", "msg": "拒绝"},
    {"code": "00000", "status": 2, "msg": "拒绝"},
    {"data": {"fileArr": []}},
    {"code": "00000", "data": {}},
    {"code": "00000", "data": {"fileArr": [{"name": "missing-id"}]}},
])
def test_folders_reject_failed_or_malformed_response(response):
    with pytest.raises(ApiError):
        list_strategy_folders(client_with(lambda request: httpx.Response(200, json=response)))


def test_create_folder_checks_duplicate_and_verifies_result():
    created = False

    def handler(request):
        nonlocal created
        if request.url.path.endswith("AddFile"):
            assert request.method == "POST"
            assert request.url.params["pId"] == "0"
            assert parse_qs(request.content.decode())["name"] == ["对比研究"]
            created = True
            return httpx.Response(200, json={"code": "00000", "data": "f1"})
        return httpx.Response(200, json=folder_response() if created else {
            "code": "00000", "data": {"fileArr": []},
        })

    assert create_strategy_folder(client_with(handler), name="对比研究") == {
        "ok": True, "id": "f1", "name": "对比研究", "parent_id": "0",
    }


def test_create_folder_refuses_duplicate_without_mutation():
    def handler(request):
        assert request.url.path.endswith("GetFileList")
        return httpx.Response(200, json=folder_response())

    with pytest.raises(UsageError, match="同名"):
        create_strategy_folder(client_with(handler), name="对比研究")


@pytest.mark.parametrize("name", ["", "  ", "bad&name"])
def test_create_folder_validates_name_before_requests(name):
    def handler(request):
        pytest.fail("invalid name must not issue a request")

    with pytest.raises(UsageError):
        create_strategy_folder(client_with(handler), name=name)


def test_move_strategy_uses_internal_id_and_verifies_destination():
    moved = False

    def handler(request):
        nonlocal moved
        if request.url.path.endswith("GetFileList"):
            return httpx.Response(200, json=folder_response())
        if request.url.path.endswith("AlgorithmToFile"):
            assert request.method == "POST"
            assert request.url.params["ids"] == "internal-2"
            assert request.url.params["fId"] == "f1"
            moved = True
            return httpx.Response(200, json={"code": "00000"})
        assert request.url.path.endswith("/list")
        if request.url.params.get("fId"):
            assert moved
        return httpx.Response(200, text=STRATEGY_LIST_HTML)

    result = move_strategy(client_with(handler), "edit-2", folder_id="f1")
    assert result == {"ok": True, "id": "edit-2", "internal_id": "internal-2", "name": "全天候ETF", "folder_id": "f1"}
    assert moved


def test_move_strategy_rejects_unknown_folder_before_mutation():
    def handler(request):
        assert request.url.path.endswith("GetFileList")
        return httpx.Response(200, json={"code": "00000", "data": {"fileArr": []}})

    with pytest.raises(NotFoundError):
        move_strategy(client_with(handler), "edit-2", folder_id="unknown")


def test_move_strategy_rejects_unknown_strategy_before_mutation():
    def handler(request):
        if request.url.path.endswith("GetFileList"):
            return httpx.Response(200, json=folder_response())
        assert request.url.path.endswith(("/list", "/edit"))
        return httpx.Response(200, text="<html></html>")

    with pytest.raises(NotFoundError):
        move_strategy(client_with(handler), "unknown", folder_id="f1")


def test_move_strategy_does_not_claim_success_without_confirmation():
    moved = False

    def handler(request):
        nonlocal moved
        if request.url.path.endswith("GetFileList"):
            return httpx.Response(200, json=folder_response())
        if request.url.path.endswith("AlgorithmToFile"):
            moved = True
            return httpx.Response(200, json={"code": "00000"})
        return httpx.Response(200, text="" if moved else STRATEGY_LIST_HTML)

    with pytest.raises(ApiError, match="尚未确认"):
        move_strategy(client_with(handler), "edit-2", folder_id="f1")


def test_move_strategy_resolves_stale_edit_id_without_editing_source():
    def handler(request):
        if request.url.path.endswith("/edit"):
            return httpx.Response(200, text=STRATEGY_EDIT_HTML.replace("策略名", "全天候ETF"))
        if request.url.path.endswith("AlgorithmToFile"):
            assert request.url.params["ids"] == "internal-2"
            return httpx.Response(200, json={"code": "00000"})
        assert request.url.path.endswith("/list")
        return httpx.Response(200, text=STRATEGY_LIST_HTML)

    assert move_strategy(client_with(handler), "old-edit-id", folder_id="0")["internal_id"] == "internal-2"


def test_move_strategy_handles_ids_rotating_after_mutation():
    moved = False

    def handler(request):
        nonlocal moved
        if request.url.path.endswith("GetFileList"):
            return httpx.Response(200, json=folder_response())
        if request.url.path.endswith("AlgorithmToFile"):
            moved = True
            return httpx.Response(200, json={"code": "00000"})
        html = STRATEGY_LIST_HTML
        if moved:
            html = html.replace("internal-2", "rotated-internal").replace("edit-2", "rotated-edit")
        return httpx.Response(200, text=html)

    result = move_strategy(client_with(handler), "edit-2", folder_id="f1")
    assert result["id"] == "rotated-edit"
    assert result["internal_id"] == "rotated-internal"


def test_move_strategy_rejects_duplicate_names_before_mutation():
    def handler(request):
        assert request.url.path.endswith("/list")
        return httpx.Response(200, text=STRATEGY_LIST_HTML.replace("混合策略", "全天候ETF"))

    with pytest.raises(UsageError, match="同名"):
        move_strategy(client_with(handler), "edit-2", folder_id="0")
