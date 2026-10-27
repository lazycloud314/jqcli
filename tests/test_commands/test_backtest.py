import json

from click.testing import CliRunner

from jqcli.cli import main


def test_backtest_run_json(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr("jqcli.commands.backtest.make_client", lambda app: object())

    def fake_run(client, **kwargs):
        captured.update(kwargs)
        return {"id": "bt1", "status": "running"}

    monkeypatch.setattr("jqcli.commands.backtest.run_backtest", fake_run)

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "backtest",
            "run",
            "s1",
            "--start",
            "2023-01-01",
            "--end",
            "2023-12-31",
        ],
    )

    assert result.exit_code == 0
    assert captured["strategy_id"] == "s1"
    assert captured["compile_only"] is False
    assert captured["use_credit"] is False
    assert json.loads(result.output)["id"] == "bt1"


def test_backtest_run_compile_flag(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr("jqcli.commands.backtest.make_client", lambda app: object())

    def fake_run(client, **kwargs):
        captured.update(kwargs)
        return {"id": "bt1", "status": "running", "mode": "compile"}

    monkeypatch.setattr("jqcli.commands.backtest.run_backtest", fake_run)

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "backtest",
            "run",
            "s1",
            "--start",
            "2023-01-01",
            "--compile",
        ],
    )

    assert result.exit_code == 0
    assert captured["compile_only"] is True
    assert json.loads(result.output)["mode"] == "compile"


def test_backtest_run_use_credit_flag(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr("jqcli.commands.backtest.make_client", lambda app: object())

    def fake_run(client, **kwargs):
        captured.update(kwargs)
        return {"id": "bt1", "status": "running"}

    monkeypatch.setattr("jqcli.commands.backtest.run_backtest", fake_run)

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "backtest",
            "run",
            "s1",
            "--start",
            "2023-01-01",
            "--use-credit",
        ],
    )

    assert result.exit_code == 0
    assert captured["use_credit"] is True


def test_backtest_run_wait(monkeypatch, tmp_path):
    monkeypatch.setattr("jqcli.commands.backtest.make_client", lambda app: object())
    monkeypatch.setattr("jqcli.commands.backtest.run_backtest", lambda client, **kwargs: {"id": "bt1", "status": "running"})
    monkeypatch.setattr(
        "jqcli.commands.backtest.wait_for_backtest",
        lambda client, backtest_id, timeout, poll_interval, **kwargs: {"id": backtest_id, "status": "done"},
    )

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "backtest",
            "run",
            "s1",
            "--start",
            "2023-01-01",
            "--wait",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["status"] == "done"


def test_backtest_ls_json(monkeypatch, tmp_path):
    monkeypatch.setattr("jqcli.commands.backtest.make_client", lambda app: object())
    monkeypatch.setattr("jqcli.commands.backtest.list_backtests", lambda client, **kwargs: {"items": [{"id": "bt1"}]})

    result = CliRunner().invoke(
        main,
        ["--config", str(tmp_path / "c.json"), "--token", "tok", "--format", "json", "backtest", "ls", "s1"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"items": [{"id": "bt1"}]}


def test_backtest_ls_compile_flag(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr("jqcli.commands.backtest.make_client", lambda app: object())

    def fake_list(client, **kwargs):
        captured.update(kwargs)
        return {"items": []}

    monkeypatch.setattr("jqcli.commands.backtest.list_backtests", fake_list)

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "backtest",
            "ls",
            "s1",
            "--compile",
        ],
    )

    assert result.exit_code == 0
    assert captured["compile_only"] is True


def test_backtest_show_json(monkeypatch, tmp_path):
    monkeypatch.setattr("jqcli.commands.backtest.make_client", lambda app: object())
    monkeypatch.setattr("jqcli.commands.backtest.get_backtest", lambda client, backtest_id: {"id": backtest_id, "status": "done"})

    result = CliRunner().invoke(
        main,
        ["--config", str(tmp_path / "c.json"), "--token", "tok", "--format", "json", "backtest", "show", "bt1"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["id"] == "bt1"


def test_backtest_stats_json(monkeypatch, tmp_path):
    monkeypatch.setattr("jqcli.commands.backtest.make_client", lambda app: object())
    monkeypatch.setattr("jqcli.commands.backtest.make_optional_client", lambda app: object())
    monkeypatch.setattr(
        "jqcli.commands.backtest.get_backtest_stats",
        lambda client, backtest_id: {"id": backtest_id, "metrics": {"annual_algo_return": 0.3}},
    )

    result = CliRunner().invoke(
        main,
        ["--config", str(tmp_path / "c.json"), "--token", "tok", "--format", "json", "backtest", "stats", "bt1"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["metrics"]["annual_algo_return"] == 0.3


def test_backtest_result_json(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr("jqcli.commands.backtest.make_client", lambda app: object())
    monkeypatch.setattr("jqcli.commands.backtest.make_optional_client", lambda app: object())

    def fake_result(client, backtest_id, **kwargs):
        captured["backtest_id"] = backtest_id
        captured["kwargs"] = kwargs
        return {"id": backtest_id, "data": {"state": "2"}}

    monkeypatch.setattr("jqcli.commands.backtest.get_backtest_result", fake_result)

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "backtest",
            "result",
            "bt1",
            "--offset",
            "10",
            "--user-record-offset",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert captured == {"backtest_id": "bt1", "kwargs": {"offset": 10, "user_record_offset": 2}}
    assert json.loads(result.output)["data"]["state"] == "2"


def test_backtest_logs_json(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr("jqcli.commands.backtest.make_optional_client", lambda app: object())

    def fake_logs(client, backtest_id, **kwargs):
        captured["backtest_id"] = backtest_id
        captured["kwargs"] = kwargs
        return {"id": backtest_id, "kind": "log", "logs": ["line"]}

    monkeypatch.setattr("jqcli.commands.backtest.get_backtest_logs", fake_logs)

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "backtest",
            "logs",
            "bt1",
            "--offset",
            "10",
            "--all",
            "--max-pages",
            "3",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "backtest_id": "bt1",
        "kwargs": {"offset": 10, "error": False, "all_items": True, "max_pages": 3},
    }
    assert json.loads(result.output)["logs"] == ["line"]


def test_backtest_error_logs_json(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr("jqcli.commands.backtest.make_optional_client", lambda app: object())

    def fake_logs(client, backtest_id, **kwargs):
        captured["kwargs"] = kwargs
        return {"id": backtest_id, "kind": "error", "logs": ["Traceback"]}

    monkeypatch.setattr("jqcli.commands.backtest.get_backtest_logs", fake_logs)

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "backtest",
            "logs",
            "bt1",
            "--error",
        ],
    )

    assert result.exit_code == 0
    assert captured["kwargs"]["error"] is True
    assert json.loads(result.output)["kind"] == "error"


def test_backtest_rm_non_interactive_requires_yes(tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "--non-interactive",
            "backtest",
            "rm",
            "bt1",
        ],
    )

    assert result.exit_code == 7
    assert json.loads(result.stderr)["error"]["code"] == "confirmation_required"


def test_backtest_rm_with_yes(monkeypatch, tmp_path):
    monkeypatch.setattr("jqcli.commands.backtest.make_client", lambda app: object())
    monkeypatch.setattr(
        "jqcli.commands.backtest.delete_backtest_record",
        lambda client, backtest_id, compile_only=False: {"ok": True, "id": backtest_id, "compile_only": compile_only},
    )

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "--non-interactive",
            "backtest",
            "rm",
            "bt1",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"ok": True, "id": "bt1", "compile_only": False}


def test_backtest_rm_compile_flag(monkeypatch, tmp_path):
    monkeypatch.setattr("jqcli.commands.backtest.make_client", lambda app: object())
    monkeypatch.setattr(
        "jqcli.commands.backtest.delete_backtest_record",
        lambda client, backtest_id, compile_only=False: {"ok": True, "id": backtest_id, "compile_only": compile_only},
    )

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "--non-interactive",
            "backtest",
            "rm",
            "bt1",
            "--yes",
            "--compile",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"ok": True, "id": "bt1", "compile_only": True}


def test_wait_for_backtest_reaches_done(monkeypatch):
    from jqcli.commands import backtest

    result_states = iter([
        {"id": "bt1", "data": {"state": "0"}},
    ])
    states = iter([
        {"id": "bt1", "resolved_id": "bt1", "metrics": []},
        {"id": "bt1", "resolved_id": "bt1", "metrics": {"annual_algo_return": 0.3, "sharpe": 1.2}},
    ])
    monkeypatch.setattr(backtest, "get_backtest_stats", lambda client, backtest_id: next(states))
    monkeypatch.setattr(backtest, "get_backtest", lambda client, backtest_id: {"id": backtest_id, "status": "running"})
    monkeypatch.setattr(backtest, "get_backtest_result", lambda client, backtest_id: next(result_states))
    monkeypatch.setattr(backtest.time, "sleep", lambda seconds: None)

    payload = backtest.wait_for_backtest(object(), "bt1", timeout=1, poll_interval=0)

    assert payload["status"] == "done"
    assert payload["metrics"]["sharpe"] == 1.2


def test_wait_for_backtest_returns_failed_state(monkeypatch):
    from jqcli.commands import backtest

    monkeypatch.setattr(backtest, "get_backtest_stats", lambda client, backtest_id: {"id": backtest_id, "metrics": []})
    monkeypatch.setattr(backtest, "get_backtest", lambda client, backtest_id: {"id": backtest_id, "status": "running"})
    monkeypatch.setattr(
        backtest,
        "find_backtest_list_item",
        lambda client, backtest_id, strategy_id, compile_only: {"id": backtest_id, "status": "failed"},
    )
    monkeypatch.setattr(
        backtest,
        "get_backtest_result",
        lambda client, backtest_id: {"id": backtest_id, "data": {"state": "1", "message": "failed"}},
    )
    monkeypatch.setattr(
        backtest,
        "get_backtest_logs",
        lambda client, backtest_id, error=False: {"id": backtest_id, "kind": "error", "logs": ["Traceback"]},
    )
    monkeypatch.setattr(backtest.time, "sleep", lambda seconds: None)

    payload = backtest.wait_for_backtest(object(), "bt1", timeout=60, poll_interval=5)

    assert payload["status"] == "failed"
    assert payload["error_logs"] == ["Traceback"]


def test_wait_for_backtest_returns_cancelled_state(monkeypatch):
    from jqcli.commands import backtest

    monkeypatch.setattr(backtest, "get_backtest_stats", lambda client, backtest_id: {"id": backtest_id, "metrics": {}})
    monkeypatch.setattr(backtest, "get_backtest", lambda client, backtest_id: {"id": backtest_id, "status": "running"})
    monkeypatch.setattr(
        backtest,
        "find_backtest_list_item",
        lambda client, backtest_id, strategy_id, compile_only: {"id": backtest_id, "status": "cancelled"},
    )
    monkeypatch.setattr(
        backtest,
        "get_backtest_result",
        lambda client, backtest_id: {"id": backtest_id, "data": {"state": 3}},
    )
    monkeypatch.setattr(
        backtest,
        "get_backtest_logs",
        lambda client, backtest_id, error=False: {"id": backtest_id, "kind": "error", "logs": []},
    )
    monkeypatch.setattr(backtest.time, "sleep", lambda seconds: None)

    payload = backtest.wait_for_backtest(object(), "bt1", timeout=60, poll_interval=5)

    assert payload["status"] == "cancelled"
    assert payload["error_logs"] == []


def test_wait_for_backtest_treats_cancelled_with_error_logs_as_failed(monkeypatch):
    from jqcli.commands import backtest

    monkeypatch.setattr(backtest, "get_backtest_stats", lambda client, backtest_id: {"id": backtest_id, "metrics": {}})
    monkeypatch.setattr(backtest, "get_backtest", lambda client, backtest_id: {"id": backtest_id, "status": "running"})
    monkeypatch.setattr(
        backtest,
        "find_backtest_list_item",
        lambda client, backtest_id, strategy_id, compile_only: None,
    )
    monkeypatch.setattr(
        backtest,
        "get_backtest_result",
        lambda client, backtest_id: {"id": backtest_id, "data": {"state": 3}},
    )
    monkeypatch.setattr(
        backtest,
        "get_backtest_logs",
        lambda client, backtest_id, error=False: {"id": backtest_id, "kind": "error", "logs": ["Traceback"]},
    )
    monkeypatch.setattr(backtest.time, "sleep", lambda seconds: None)

    payload = backtest.wait_for_backtest(object(), "bt1", timeout=60, poll_interval=5)

    assert payload["status"] == "failed"
    assert payload["result_status"] == "failed"
    assert payload["error_logs"] == ["Traceback"]


def test_wait_for_backtest_returns_failed_result_state_without_list_item(monkeypatch):
    from jqcli.commands import backtest

    monkeypatch.setattr(backtest, "get_backtest_stats", lambda client, backtest_id: {"id": backtest_id, "metrics": {}})
    monkeypatch.setattr(backtest, "get_backtest", lambda client, backtest_id: {"id": backtest_id, "status": "running"})
    monkeypatch.setattr(
        backtest,
        "find_backtest_list_item",
        lambda client, backtest_id, strategy_id, compile_only: None,
    )
    monkeypatch.setattr(
        backtest,
        "get_backtest_result",
        lambda client, backtest_id: {"id": backtest_id, "data": {"state": "1", "message": "failed"}},
    )
    monkeypatch.setattr(
        backtest,
        "get_backtest_logs",
        lambda client, backtest_id, error=False: {"id": backtest_id, "kind": "error", "logs": ["NameError"]},
    )
    monkeypatch.setattr(backtest.time, "sleep", lambda seconds: None)

    payload = backtest.wait_for_backtest(object(), "bt1", timeout=60, poll_interval=5)

    assert payload["status"] == "failed"
    assert payload["result_status"] == "failed"
    assert payload["error_logs"] == ["NameError"]


def test_wait_for_backtest_returns_done_list_state_before_stats_core(monkeypatch):
    from jqcli.commands import backtest

    monkeypatch.setattr(backtest, "get_backtest_stats", lambda client, backtest_id: {"id": backtest_id, "metrics": {"report_done_seconds": 1}})
    monkeypatch.setattr(backtest, "get_backtest", lambda client, backtest_id: {"id": backtest_id, "status": "running"})
    monkeypatch.setattr(
        backtest,
        "find_backtest_list_item",
        lambda client, backtest_id, strategy_id, compile_only: {"id": "detail-id", "status": "done"},
    )
    monkeypatch.setattr(
        backtest,
        "get_backtest_result",
        lambda client, backtest_id: {"id": backtest_id, "data": {"state": "0"}},
    )
    monkeypatch.setattr(backtest.time, "sleep", lambda seconds: None)

    payload = backtest.wait_for_backtest(object(), "bt1", timeout=60, poll_interval=5)

    assert payload["status"] == "done"
    assert payload["resolved_id"] == "detail-id"
    assert payload["metrics"] == {"report_done_seconds": 1}


def test_wait_for_backtest_prefers_failed_result_over_done_list_without_core_metrics(monkeypatch):
    from jqcli.commands import backtest

    monkeypatch.setattr(backtest, "get_backtest_stats", lambda client, backtest_id: {"id": backtest_id, "metrics": []})
    monkeypatch.setattr(backtest, "get_backtest", lambda client, backtest_id: {"id": backtest_id, "status": "running"})
    monkeypatch.setattr(
        backtest,
        "find_backtest_list_item",
        lambda client, backtest_id, strategy_id, compile_only: {"id": "detail-id", "status": "done"},
    )
    monkeypatch.setattr(
        backtest,
        "get_backtest_result",
        lambda client, backtest_id: {"id": backtest_id, "data": {"state": "1", "message": "failed"}},
    )
    monkeypatch.setattr(
        backtest,
        "get_backtest_logs",
        lambda client, backtest_id, error=False: {"id": backtest_id, "kind": "error", "logs": ["Traceback"]},
    )
    monkeypatch.setattr(backtest.time, "sleep", lambda seconds: None)

    payload = backtest.wait_for_backtest(object(), "bt1", timeout=60, poll_interval=5)

    assert payload["status"] == "failed"
    assert payload["result_status"] == "failed"
    assert payload["error_logs"] == ["Traceback"]


def test_wait_for_backtest_ignores_early_failed_result_state_when_list_is_running(monkeypatch):
    from jqcli.commands import backtest

    list_items = iter([
        {"id": "detail-id", "list_id": "list-id", "status": "running"},
        {"id": "detail-id", "list_id": "list-id", "status": "done"},
    ])

    def fake_stats(client, backtest_id):
        if backtest_id == "detail-id":
            return {"id": backtest_id, "resolved_id": backtest_id, "metrics": {"annual_algo_return": 0.3, "sharpe": 1.2}}
        return {"id": backtest_id, "metrics": []}

    monkeypatch.setattr(backtest, "get_backtest_stats", fake_stats)
    monkeypatch.setattr(backtest, "get_backtest", lambda client, backtest_id: {"id": backtest_id, "status": "running"})
    monkeypatch.setattr(
        backtest,
        "find_backtest_list_item",
        lambda client, backtest_id, strategy_id, compile_only: next(list_items),
    )
    monkeypatch.setattr(
        backtest,
        "get_backtest_result",
        lambda client, backtest_id: {"id": backtest_id, "data": {"state": "1", "result": {"count": 0}}},
    )
    monkeypatch.setattr(backtest.time, "sleep", lambda seconds: None)

    payload = backtest.wait_for_backtest(
        object(),
        "list-id",
        timeout=60,
        poll_interval=5,
        strategy_id="s1",
    )

    assert payload["status"] == "done"
    assert payload["resolved_id"] == "detail-id"
    assert payload["metrics"]["sharpe"] == 1.2
