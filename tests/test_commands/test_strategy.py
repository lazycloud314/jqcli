import json

from click.testing import CliRunner

from jqcli.cli import main


def test_strategy_ls_json(monkeypatch, tmp_path):
    monkeypatch.setattr("jqcli.commands.strategy.make_client", lambda app: object())
    monkeypatch.setattr("jqcli.commands.strategy.list_strategies", lambda client, **kwargs: {"items": [{"id": "s1"}]})

    result = CliRunner().invoke(
        main,
        ["--config", str(tmp_path / "c.json"), "--token", "tok", "--format", "json", "strategy", "ls"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"items": [{"id": "s1"}]}


def test_strategy_requires_auth(tmp_path):
    result = CliRunner().invoke(
        main,
        ["--config", str(tmp_path / "c.json"), "--format", "json", "strategy", "ls"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"]["code"] == "not_authenticated"


def test_strategy_show_writes_output(monkeypatch, tmp_path):
    monkeypatch.setattr("jqcli.commands.strategy.make_client", lambda app: object())
    monkeypatch.setattr(
        "jqcli.commands.strategy.get_strategy",
        lambda client, strategy_id, include_code=False: {"id": strategy_id, "code": "print(1)"},
    )
    output = tmp_path / "strategy.py"

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "strategy",
            "show",
            "s1",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    assert output.read_text(encoding="utf-8") == "print(1)"


def test_strategy_new_reads_code_stdin(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr("jqcli.commands.strategy.make_client", lambda app: object())

    def fake_create(client, **kwargs):
        captured.update(kwargs)
        return {"id": "s1"}

    monkeypatch.setattr("jqcli.commands.strategy.create_strategy", fake_create)
    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "strategy",
            "new",
            "n",
            "--code-stdin",
        ],
        input="print(1)",
    )

    assert result.exit_code == 0
    assert captured["code"] == "print(1)"
    assert json.loads(result.output) == {"id": "s1"}


def test_strategy_edit_requires_change(monkeypatch, tmp_path):
    monkeypatch.setattr("jqcli.commands.strategy.make_client", lambda app: object())

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--token",
            "tok",
            "--format",
            "json",
            "strategy",
            "edit",
            "s1",
        ],
    )

    assert result.exit_code == 3
    assert json.loads(result.stderr)["error"]["code"] == "usage_error"


def test_strategy_rm_non_interactive_requires_yes(tmp_path):
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
            "strategy",
            "rm",
            "s1",
        ],
    )

    assert result.exit_code == 7
    assert json.loads(result.stderr)["error"]["code"] == "confirmation_required"


def test_strategy_rm_with_yes(monkeypatch, tmp_path):
    monkeypatch.setattr("jqcli.commands.strategy.make_client", lambda app: object())
    monkeypatch.setattr("jqcli.commands.strategy.delete_strategy", lambda client, strategy_id: {"ok": True, "id": strategy_id})

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
            "strategy",
            "rm",
            "s1",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"ok": True, "id": "s1"}



def test_strategy_folder_commands(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr("jqcli.commands.strategy.make_client", lambda app: object())

    def fake_folders(client, **kwargs):
        captured.append(kwargs)
        return {"items": [{"id": "f1", "name": "对比研究", "parent_id": "0"}]}

    def fake_mkdir(client, **kwargs):
        captured.append(kwargs)
        return {"ok": True, "id": "f1", "name": kwargs["name"]}

    def fake_move(client, strategy_id, **kwargs):
        captured.append({"strategy_id": strategy_id, **kwargs})
        return {"ok": True, "id": strategy_id, **kwargs}

    monkeypatch.setattr("jqcli.commands.strategy.list_strategy_folders", fake_folders)
    monkeypatch.setattr("jqcli.commands.strategy.create_strategy_folder", fake_mkdir)
    monkeypatch.setattr("jqcli.commands.strategy.move_strategy", fake_move)
    base = ["--config", str(tmp_path / "c.json"), "--token", "tok", "--non-interactive", "--format", "json", "strategy"]
    runner = CliRunner()
    for args in (["folders", "--parent", "f0"], ["mkdir", "对比研究"], ["mv", "s1", "--folder", "f1"]):
        result = runner.invoke(main, base + args)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)
    assert captured == [{"parent_id": "f0"}, {"name": "对比研究", "parent_id": "0"}, {"strategy_id": "s1", "folder_id": "f1"}]
