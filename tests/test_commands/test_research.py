import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from jqcli.cli import main
from jqcli.errors import NotFoundError, UsageError


class FakeClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def command_prefix(tmp_path, *, json_output=True, token=None):
    args = ["--config", str(tmp_path / "c.json"), "--cookie", "sid=abc"]
    if token:
        args.extend(["--token", token])
    if json_output:
        args.extend(["--format", "json"])
    return args


def test_research_requires_cookie_even_with_token(tmp_path):
    result = CliRunner().invoke(
        main,
        ["--config", str(tmp_path / "c.json"), "--token", "tok", "--format", "json", "research", "ls"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stderr)
    assert payload["error"]["code"] == "not_authenticated"
    assert "cookie" in payload["error"]["message"]


def test_research_make_client_uses_cookie_without_bearer_token(monkeypatch, tmp_path):
    captured = {}

    class CapturingClient(FakeClient):
        def __init__(self, api_base, **kwargs):
            super().__init__()
            captured["api_base"] = api_base
            captured.update(kwargs)

    monkeypatch.setattr("jqcli.commands.research.ApiClient", CapturingClient)
    monkeypatch.setattr("jqcli.authentication._probe_login", lambda app, **kwargs: kwargs["cookie"])
    monkeypatch.setattr("jqcli.commands.research.list_research_items", lambda client, path="": {"items": []})

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path, token="tok") + ["--api-base", "https://example.test", "research", "ls"],
    )

    assert result.exit_code == 0
    assert captured["api_base"] == "https://example.test"
    assert captured["cookie"] == "sid=abc"
    assert "token" not in captured


def test_research_ls_json_forwards_path_and_closes(monkeypatch, tmp_path):
    client = FakeClient()
    captured = {}
    payload = {"path": "目录 A", "items": [{"name": "分析.ipynb", "type": "notebook"}]}
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)

    def fake_list(received_client, *, path=""):
        captured["client"] = received_client
        captured["path"] = path
        return payload

    monkeypatch.setattr("jqcli.commands.research.list_research_items", fake_list)

    result = CliRunner().invoke(main, command_prefix(tmp_path) + ["research", "ls", "目录 A"])

    assert result.exit_code == 0
    assert captured == {"client": client, "path": "目录 A"}
    assert json.loads(result.output) == payload
    assert client.closed is True


def test_research_ls_table_has_expected_columns(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.list_research_items",
        lambda received_client, path="": {
            "items": [
                {
                    "name": "分析.ipynb",
                    "type": "notebook",
                    "size": 42,
                    "last_modified": "2026-08-14T12:00:00Z",
                    "writable": True,
                }
            ]
        },
    )

    result = CliRunner().invoke(main, command_prefix(tmp_path, json_output=False) + ["research", "ls"])

    assert result.exit_code == 0
    for heading in ("名称", "类型", "大小", "最近修改", "可写"):
        assert heading in result.output
    assert "分析.ipynb" in result.output
    assert client.closed is True


def test_research_ls_closes_client_when_api_fails(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)

    def fail(received_client, *, path=""):
        raise UsageError("bad path")

    monkeypatch.setattr("jqcli.commands.research.list_research_items", fail)

    result = CliRunner().invoke(main, command_prefix(tmp_path) + ["research", "ls"])

    assert result.exit_code == 3
    assert client.closed is True


def test_research_show_forwards_content_flag_and_closes(monkeypatch, tmp_path):
    client = FakeClient()
    captured = {}
    payload = {"path": "分析.ipynb", "type": "notebook", "format": "json", "content": {"cells": []}}
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)

    def fake_get(received_client, path, *, include_content=False):
        captured.update(client=received_client, path=path, include_content=include_content)
        return payload

    monkeypatch.setattr("jqcli.commands.research.get_research_item", fake_get)

    result = CliRunner().invoke(main, command_prefix(tmp_path) + ["research", "show", "分析.ipynb", "--content"])

    assert result.exit_code == 0
    assert captured == {"client": client, "path": "分析.ipynb", "include_content": True}
    assert json.loads(result.output) == payload
    assert client.closed is True


@pytest.mark.parametrize(
    ("content_format", "content", "expected"),
    [
        ("text", "print('你好')", "print('你好')"),
        ("json", {"cells": []}, '"cells": []'),
        ("base64", "AP8=", "AP8="),
    ],
)
def test_research_show_renders_content_in_table_mode(
    monkeypatch, tmp_path, content_format, content, expected
):
    client = FakeClient()
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.get_research_item",
        lambda received_client, path, include_content=False: {
            "path": path,
            "name": "item",
            "type": "file",
            "format": content_format,
            "content": content,
        },
    )

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path, json_output=False) + ["research", "show", "item", "--content"],
    )

    assert result.exit_code == 0
    assert "路径: item" in result.output
    assert "内容:" in result.output
    assert expected in result.output
    assert client.closed is True


def test_research_download_text(monkeypatch, tmp_path):
    client = FakeClient()
    output = tmp_path / "nested" / "hello.txt"
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.get_research_item",
        lambda received_client, path, include_content=False: {
            "path": path,
            "name": "hello.txt",
            "type": "file",
            "format": "text",
            "content": "你好\n",
        },
    )

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["research", "download", "folder/hello.txt", "--output", str(output)],
    )

    assert result.exit_code == 0
    assert output.read_bytes() == "你好\n".encode("utf-8")
    assert json.loads(result.output) == {
        "path": "folder/hello.txt",
        "output": str(output),
        "bytes": len("你好\n".encode("utf-8")),
    }
    assert client.closed is True


def test_research_download_base64(monkeypatch, tmp_path):
    client = FakeClient()
    output = tmp_path / "data.bin"
    raw = b"\x00\xff\x10"
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.get_research_item",
        lambda received_client, path, include_content=False: {
            "path": path,
            "name": "data.bin",
            "type": "file",
            "format": "base64",
            "content": base64.b64encode(raw).decode("ascii"),
        },
    )

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["research", "download", "data.bin", "-o", str(output)],
    )

    assert result.exit_code == 0
    assert output.read_bytes() == raw
    assert json.loads(result.output)["bytes"] == len(raw)
    assert client.closed is True


def test_research_download_notebook_json_uses_safe_default_name(monkeypatch, tmp_path):
    client = FakeClient()
    notebook = {"cells": [{"cell_type": "markdown", "source": ["# 你好"]}], "metadata": {}}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.get_research_item",
        lambda received_client, path, include_content=False: {
            "path": "folder/note.ipynb",
            "name": "../note.ipynb",
            "type": "notebook",
            "format": "json",
            "content": notebook,
        },
    )

    result = CliRunner().invoke(main, command_prefix(tmp_path) + ["research", "download", "folder/note.ipynb"])

    assert result.exit_code == 0
    output = tmp_path / "note.ipynb"
    assert json.loads(output.read_text(encoding="utf-8")) == notebook
    assert output.read_bytes().endswith(b"\n")
    assert json.loads(result.output)["output"] == "note.ipynb"
    assert client.closed is True


def test_research_download_refuses_existing_output_unless_forced(monkeypatch, tmp_path):
    client = FakeClient()
    output = tmp_path / "hello.txt"
    output.write_text("old", encoding="utf-8")
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.get_research_item",
        lambda received_client, path, include_content=False: {
            "path": path,
            "name": "hello.txt",
            "type": "file",
            "format": "text",
            "content": "new",
        },
    )
    args = command_prefix(tmp_path) + ["research", "download", "hello.txt", "-o", str(output)]

    refused = CliRunner().invoke(main, args)
    forced = CliRunner().invoke(main, args + ["--force"])

    assert refused.exit_code == 6
    assert json.loads(refused.stderr)["error"]["code"] == "file_error"
    assert forced.exit_code == 0
    assert output.read_text(encoding="utf-8") == "new"
    assert client.closed is True


def test_research_download_rejects_directory(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.get_research_item",
        lambda received_client, path, include_content=False: {
            "path": path,
            "name": "folder",
            "type": "directory",
            "format": "json",
            "content": [],
        },
    )

    result = CliRunner().invoke(main, command_prefix(tmp_path) + ["research", "download", "folder"])

    assert result.exit_code == 3
    assert json.loads(result.stderr)["error"]["code"] == "usage_error"
    assert client.closed is True


@pytest.mark.parametrize(
    ("content_format", "content", "message"),
    [
        ("base64", "%%%", "base64"),
        ("json", "{bad", "JSON"),
        ("text", {"not": "text"}, "文本"),
    ],
)
def test_research_download_rejects_bad_content(monkeypatch, tmp_path, content_format, content, message):
    client = FakeClient()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.get_research_item",
        lambda received_client, path, include_content=False: {
            "path": path,
            "name": "bad.dat",
            "type": "file",
            "format": content_format,
            "content": content,
        },
    )

    result = CliRunner().invoke(main, command_prefix(tmp_path) + ["research", "download", "bad.dat"])

    assert result.exit_code == 6
    payload = json.loads(result.stderr)
    assert payload["error"]["code"] == "file_error"
    assert message in payload["error"]["message"]
    assert client.closed is True
    assert not (tmp_path / "bad.dat").exists()


@pytest.mark.parametrize(
    "command",
    [
        ["upload", "{local}"],
        ["mkdir", "folder"],
        ["mv", "source", "destination"],
        ["rm", "item"],
    ],
)
def test_research_writes_require_yes_without_calling_api(monkeypatch, tmp_path, command):
    local = tmp_path / "upload.txt"
    local.write_text("data", encoding="utf-8")
    args = [str(local) if value == "{local}" else value for value in command]

    def unexpected_client(app):
        raise AssertionError("write command must not create a client before confirmation")

    monkeypatch.setattr("jqcli.commands.research.make_client", unexpected_client)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["--non-interactive", "research"] + args,
    )

    assert result.exit_code == 7
    assert json.loads(result.stderr)["error"]["code"] == "confirmation_required"


def test_research_json_write_requires_yes_without_printing_prompt(monkeypatch, tmp_path):
    def unexpected_client(app):
        raise AssertionError("JSON write must fail before creating a client")

    monkeypatch.setattr("jqcli.commands.research.make_client", unexpected_client)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["research", "mkdir", "folder"],
    )

    assert result.exit_code == 7
    assert "确认在研究平台" not in result.output
    assert json.loads(result.stderr)["error"]["code"] == "confirmation_required"


def test_research_upload_text_uses_default_remote_name(monkeypatch, tmp_path):
    client = FakeClient()
    local = tmp_path / "你好.py"
    local.write_bytes("print('你好')\n".encode("utf-8"))
    captured = {}
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)

    def missing(received_client, path, include_content=False):
        captured["preflight"] = (received_client, path, include_content)
        raise NotFoundError("missing")

    def fake_save(received_client, path, **kwargs):
        captured["save"] = (received_client, path, kwargs)
        return {"path": path, "type": kwargs["item_type"], "format": kwargs["content_format"]}

    monkeypatch.setattr("jqcli.commands.research.get_research_item", missing)
    monkeypatch.setattr("jqcli.commands.research.save_research_item", fake_save)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["--non-interactive", "research", "upload", str(local), "--yes"],
    )

    assert result.exit_code == 0
    assert captured["preflight"] == (client, "你好.py", False)
    received_client, path, kwargs = captured["save"]
    assert received_client is client
    assert path == "你好.py"
    assert kwargs == {
        "content": "print('你好')\n",
        "item_type": "file",
        "content_format": "text",
    }
    assert json.loads(result.output)["path"] == "你好.py"
    assert client.closed is True


def test_research_upload_notebook_json_with_force(monkeypatch, tmp_path):
    client = FakeClient()
    local = tmp_path / "note.ipynb"
    notebook = {"cells": [], "metadata": {"language_info": {"name": "python"}}, "nbformat": 4}
    local.write_text(json.dumps(notebook, ensure_ascii=False), encoding="utf-8")
    captured = {}
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.get_research_item",
        lambda received_client, path, include_content=False: {"path": path, "type": "notebook"},
    )

    def fake_save(received_client, path, **kwargs):
        captured.update(client=received_client, path=path, kwargs=kwargs)
        return {"path": path, "type": "notebook", "format": "json"}

    monkeypatch.setattr("jqcli.commands.research.save_research_item", fake_save)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path)
        + [
            "--non-interactive",
            "research",
            "upload",
            str(local),
            "folder/note.ipynb",
            "--force",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "client": client,
        "path": "folder/note.ipynb",
        "kwargs": {"content": notebook, "item_type": "notebook", "content_format": "json"},
    }
    assert client.closed is True


def test_research_upload_binary_as_base64(monkeypatch, tmp_path):
    client = FakeClient()
    local = tmp_path / "data.bin"
    raw = b"\x00\x01\x02"
    local.write_bytes(raw)
    captured = {}
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.get_research_item",
        lambda received_client, path, include_content=False: (_ for _ in ()).throw(NotFoundError("missing")),
    )

    def fake_save(received_client, path, **kwargs):
        captured.update(path=path, kwargs=kwargs)
        return {"path": path, "type": "file", "format": "base64"}

    monkeypatch.setattr("jqcli.commands.research.save_research_item", fake_save)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["--non-interactive", "research", "upload", str(local), "--yes"],
    )

    assert result.exit_code == 0
    assert captured == {
        "path": "data.bin",
        "kwargs": {
            "content": base64.b64encode(raw).decode("ascii"),
            "item_type": "file",
            "content_format": "base64",
        },
    }
    assert client.closed is True


def test_research_upload_refuses_remote_overwrite_without_force(monkeypatch, tmp_path):
    client = FakeClient()
    local = tmp_path / "data.txt"
    local.write_text("new", encoding="utf-8")
    saved = []
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.get_research_item",
        lambda received_client, path, include_content=False: {"path": path, "type": "file"},
    )
    monkeypatch.setattr("jqcli.commands.research.save_research_item", lambda *args, **kwargs: saved.append((args, kwargs)))

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path)
        + ["--non-interactive", "research", "upload", str(local), "remote.txt", "--yes"],
    )

    assert result.exit_code == 6
    assert json.loads(result.stderr)["error"]["code"] == "file_error"
    assert "--force" in json.loads(result.stderr)["error"]["message"]
    assert saved == []
    assert client.closed is True


def test_research_upload_rejects_files_larger_than_25_mib_before_api(monkeypatch, tmp_path):
    local = tmp_path / "large.bin"
    local.write_bytes(b"small fixture")
    original_stat = Path.stat

    def fake_stat(path, *args, **kwargs):
        if path == local:
            return SimpleNamespace(st_size=25 * 1024 * 1024 + 1)
        return original_stat(path, *args, **kwargs)

    def unexpected_client(app):
        raise AssertionError("oversized upload must be rejected before API access")

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr("jqcli.commands.research.make_client", unexpected_client)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["--non-interactive", "research", "upload", str(local), "--yes"],
    )

    assert result.exit_code == 6
    payload = json.loads(result.stderr)
    assert payload["error"]["code"] == "file_error"
    assert "25 MiB" in payload["error"]["message"]


def test_research_mkdir_json_and_close(monkeypatch, tmp_path):
    client = FakeClient()
    captured = {}
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)

    def fake_mkdir(received_client, path):
        captured.update(client=received_client, path=path)
        return {"path": path, "type": "directory"}

    monkeypatch.setattr("jqcli.commands.research.create_research_directory", fake_mkdir)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["--non-interactive", "research", "mkdir", "folder", "--yes"],
    )

    assert result.exit_code == 0
    assert captured == {"client": client, "path": "folder"}
    assert json.loads(result.output) == {"path": "folder", "type": "directory"}
    assert client.closed is True


def test_research_mv_json_and_close(monkeypatch, tmp_path):
    client = FakeClient()
    captured = {}
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)

    def fake_move(received_client, source, destination):
        captured.update(client=received_client, source=source, destination=destination)
        return {"path": destination, "type": "file"}

    monkeypatch.setattr("jqcli.commands.research.move_research_item", fake_move)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path)
        + ["--non-interactive", "research", "mv", "old.txt", "folder/new.txt", "--yes"],
    )

    assert result.exit_code == 0
    assert captured == {"client": client, "source": "old.txt", "destination": "folder/new.txt"}
    assert json.loads(result.output)["path"] == "folder/new.txt"
    assert client.closed is True


def test_research_rm_wraps_empty_response(monkeypatch, tmp_path):
    client = FakeClient()
    captured = {}
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)

    def fake_delete(received_client, path):
        captured.update(client=received_client, path=path)
        return None

    monkeypatch.setattr("jqcli.commands.research.delete_research_item", fake_delete)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["--non-interactive", "research", "rm", "old.txt", "--yes"],
    )

    assert result.exit_code == 0
    assert captured == {"client": client, "path": "old.txt"}
    assert json.loads(result.output) == {"ok": True, "path": "old.txt"}
    assert client.closed is True


@pytest.mark.parametrize(
    "args",
    [
        ["mkdir", "/", "--yes"],
        ["mv", "/", "destination", "--yes"],
        ["mv", "source", "//", "--yes"],
        ["rm", ".", "--yes"],
    ],
)
def test_research_writes_reject_root_paths(monkeypatch, tmp_path, args):
    def unexpected_client(app):
        raise AssertionError("root path must be rejected before API access")

    monkeypatch.setattr("jqcli.commands.research.make_client", unexpected_client)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["--non-interactive", "research"] + args,
    )

    assert result.exit_code == 3
    assert json.loads(result.stderr)["error"]["code"] == "usage_error"


def test_research_write_rejects_unsafe_path_before_confirmation(monkeypatch, tmp_path):
    def unexpected_client(app):
        raise AssertionError("unsafe path must be rejected before API access")

    monkeypatch.setattr("jqcli.commands.research.make_client", unexpected_client)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path, json_output=False) + ["research", "rm", "folder/../item"],
    )

    assert result.exit_code == 3
    assert "确认删除" not in result.output
    assert "无效组件" in result.output


@pytest.mark.parametrize(
    ("command", "api_name", "payload"),
    [
        (
            "kernelspecs",
            "list_research_kernelspecs",
            {
                "default": "python3",
                "items": [{"name": "python3", "display_name": "Python 3", "language": "python"}],
                "total": 1,
            },
        ),
        (
            "kernels",
            "list_research_kernels",
            {"items": [{"id": "k1", "name": "python3", "execution_state": "idle"}], "total": 1},
        ),
        (
            "sessions",
            "list_research_sessions",
            {
                "items": [
                    {
                        "id": "s1",
                        "path": "note.ipynb",
                        "type": "notebook",
                        "kernel": {"name": "python3", "execution_state": "idle"},
                    }
                ],
                "total": 1,
            },
        ),
    ],
)
def test_research_runtime_lists_are_read_only_json_and_close(
    monkeypatch, tmp_path, command, api_name, payload
):
    client = FakeClient()
    received = []
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)

    def fake_list(received_client):
        received.append(received_client)
        return payload

    monkeypatch.setattr(f"jqcli.commands.research.{api_name}", fake_list)

    result = CliRunner().invoke(main, command_prefix(tmp_path) + ["research", command])

    assert result.exit_code == 0
    assert json.loads(result.output) == payload
    assert received == [client]
    assert client.closed is True


def test_research_kernelspecs_table_marks_default(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.list_research_kernelspecs",
        lambda received_client: {
            "default": "python3",
            "items": [
                {
                    "name": "python3",
                    "display_name": "Python 3",
                    "language": "python",
                    "interrupt_mode": "signal",
                }
            ],
            "total": 1,
        },
    )

    result = CliRunner().invoke(
        main, command_prefix(tmp_path, json_output=False) + ["research", "kernelspecs"]
    )

    assert result.exit_code == 0
    for expected in ("名称", "显示名称", "Python 3", "python", "signal", "是"):
        assert expected in result.output
    assert client.closed is True


@pytest.mark.parametrize(
    "args",
    [
        ["exec", "--yes"],
        ["exec", "--file", "code.py", "--code-stdin", "--yes"],
    ],
)
def test_research_exec_requires_exactly_one_code_source_before_api(monkeypatch, tmp_path, args):
    monkeypatch.setattr(
        "jqcli.commands.research.make_client",
        lambda app: (_ for _ in ()).throw(AssertionError("invalid source selection must not call API")),
    )

    result = CliRunner().invoke(main, command_prefix(tmp_path) + ["research"] + args)

    assert result.exit_code == 3
    assert json.loads(result.stderr)["error"]["code"] == "usage_error"


def test_research_exec_confirms_before_reading_file_or_calling_api(monkeypatch, tmp_path):
    missing = tmp_path / "missing.py"
    monkeypatch.setattr(
        "jqcli.commands.research.make_client",
        lambda app: (_ for _ in ()).throw(AssertionError("unconfirmed execution must not call API")),
    )

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["research", "exec", "--file", str(missing)],
    )

    assert result.exit_code == 7
    assert json.loads(result.stderr)["error"]["code"] == "confirmation_required"


def test_research_exec_table_mode_also_requires_explicit_yes_before_file_read(monkeypatch, tmp_path):
    missing = tmp_path / "missing.py"
    monkeypatch.setattr(
        "jqcli.commands.research.make_client",
        lambda app: (_ for _ in ()).throw(AssertionError("unconfirmed execution must not call API")),
    )

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path, json_output=False)
        + ["research", "exec", "--file", str(missing)],
    )

    assert result.exit_code == 7
    assert "必须显式传入 --yes" in result.output
    assert "无法读取执行代码" not in result.output


def test_research_exec_code_stdin_always_requires_explicit_yes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "jqcli.commands.research.make_client",
        lambda app: (_ for _ in ()).throw(AssertionError("unconfirmed stdin must not call API")),
    )

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path, json_output=False) + ["research", "exec", "--code-stdin"],
        input="print('not consumed')\n",
    )

    assert result.exit_code == 7
    assert "必须显式传入 --yes" in result.output


def test_research_exec_file_forwards_options_and_closes(monkeypatch, tmp_path):
    client = FakeClient()
    source = tmp_path / "hello.py"
    source.write_bytes("print('你好')\n".encode("utf-8"))
    captured = {}
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)

    def fake_execute(received_client, code, **kwargs):
        captured.update(client=received_client, code=code, kwargs=kwargs)
        return {"status": "ok", "execution_count": 1, "outputs": []}

    monkeypatch.setattr("jqcli.commands.research.execute_research_code", fake_execute)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path)
        + [
            "research",
            "exec",
            "--file",
            str(source),
            "--kernel",
            "python3",
            "--execution-timeout",
            "9.5",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert captured["client"] is client
    assert captured["code"] == "print('你好')\n"
    assert captured["kwargs"] == {"kernel_name": "python3", "execution_timeout": 9.5, "on_event": None}
    assert json.loads(result.output)["status"] == "ok"
    assert client.closed is True


@pytest.mark.parametrize("content", [b"", b"  \n\t", b"\xff\xfe"])
def test_research_exec_rejects_empty_or_non_utf8_file_before_api(monkeypatch, tmp_path, content):
    source = tmp_path / "bad.py"
    source.write_bytes(content)
    monkeypatch.setattr(
        "jqcli.commands.research.make_client",
        lambda app: (_ for _ in ()).throw(AssertionError("bad code must not call API")),
    )

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["research", "exec", "--file", str(source), "--yes"],
    )

    assert result.exit_code == 6
    assert json.loads(result.stderr)["error"]["code"] == "file_error"


@pytest.mark.parametrize("command", ["exec", "run"])
@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_research_execution_rejects_invalid_timeout_before_api(
    monkeypatch, tmp_path, command, timeout
):
    monkeypatch.setattr(
        "jqcli.commands.research.make_client",
        lambda app: (_ for _ in ()).throw(AssertionError("bad timeout must not call API")),
    )
    args = ["research", command]
    if command == "exec":
        args += ["--code-stdin"]
    else:
        args += ["note.ipynb"]
    args += ["--execution-timeout", timeout, "--yes"]

    result = CliRunner().invoke(main, command_prefix(tmp_path) + args, input="print(1)\n")

    assert result.exit_code == 3
    assert json.loads(result.stderr)["error"]["code"] == "usage_error"


def test_research_exec_stream_requires_json_before_reading_or_api(monkeypatch, tmp_path):
    source = tmp_path / "code.py"
    source.write_text("print(1)", encoding="utf-8")
    monkeypatch.setattr(
        "jqcli.commands.research.make_client",
        lambda app: (_ for _ in ()).throw(AssertionError("invalid stream mode must not call API")),
    )

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path, json_output=False)
        + ["research", "exec", "--file", str(source), "--stream", "--yes"],
    )

    assert result.exit_code == 3
    assert "--stream 仅支持 --format json" in result.output


def test_research_exec_stream_emits_events_and_final_done(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)

    def fake_execute(received_client, code, **kwargs):
        kwargs["on_event"]({"event": "status", "state": "busy"})
        kwargs["on_event"](
            {"event": "output", "output": {"output_type": "stream", "name": "stdout", "text": "ok\n"}}
        )
        return {
            "status": "ok",
            "execution_count": 1,
            "outputs": [{"output_type": "stream", "name": "stdout", "text": "ok\n"}],
        }

    monkeypatch.setattr("jqcli.commands.research.execute_research_code", fake_execute)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path)
        + ["research", "exec", "--code-stdin", "--stream", "--yes"],
        input="print('ok')\n",
    )

    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.output.splitlines()]
    assert [line.get("event") or line.get("type") for line in lines] == ["status", "output", "done"]
    assert lines[-1]["result"]["status"] == "ok"
    assert client.closed is True


def test_research_run_stream_emits_cell_events_and_final_done(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)

    def fake_run(received_client, path, **kwargs):
        kwargs["on_event"]({"cell_index": 2, "event": "status", "state": "busy"})
        return {
            "status": "ok",
            "path": path,
            "total_code_cells": 1,
            "selected_code_cells": 1,
            "executed_cells": 1,
            "cells": [],
            "saved": False,
        }

    monkeypatch.setattr("jqcli.commands.research.run_research_notebook", fake_run)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path)
        + ["research", "run", "note.ipynb", "--cell", "2", "--stream", "--yes"],
    )

    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.output.splitlines()]
    assert [line["event"] for line in lines] == ["status", "done"]
    assert lines[0]["cell_index"] == 2
    assert lines[-1]["result"]["saved"] is False
    assert client.closed is True


def test_research_exec_closes_client_when_api_fails(monkeypatch, tmp_path):
    client = FakeClient()
    source = tmp_path / "code.py"
    source.write_bytes(b"print(1)\n")
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.execute_research_code",
        lambda received_client, code, **kwargs: (_ for _ in ()).throw(UsageError("remote failed")),
    )

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["research", "exec", "--file", str(source), "--yes"],
    )

    assert result.exit_code == 3
    assert json.loads(result.stderr)["error"]["code"] == "usage_error"
    assert client.closed is True


def test_research_exec_table_only_renders_safe_plain_text_summary(monkeypatch, tmp_path):
    client = FakeClient()
    source = tmp_path / "code.py"
    source.write_text("1", encoding="utf-8")
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)
    monkeypatch.setattr(
        "jqcli.commands.research.execute_research_code",
        lambda received_client, code, **kwargs: {
            "status": "error",
            "execution_count": 3,
            "outputs": [
                {"output_type": "stream", "name": "stdout", "text": "safe\x1b[2J\n"},
                {
                    "output_type": "display_data",
                    "data": {
                        "text/plain": "plain value",
                        "text/html": "<script>html-secret</script>",
                        "application/javascript": "js-secret",
                    },
                    "metadata": {},
                },
                {
                    "output_type": "error",
                    "ename": "ValueError",
                    "evalue": "bad",
                    "traceback": ["traceback-secret"],
                },
            ],
        },
    )

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path, json_output=False)
        + ["research", "exec", "--file", str(source), "--yes"],
    )

    assert result.exit_code == 0
    for expected in ("状态: error", "safe", "plain value", "ValueError: bad"):
        assert expected in result.output
    for forbidden in ("\x1b", "html-secret", "js-secret", "traceback-secret"):
        assert forbidden not in result.output
    assert client.closed is True


def test_research_run_normalizes_path_forwards_cells_and_closes(monkeypatch, tmp_path):
    client = FakeClient()
    captured = {}
    monkeypatch.setattr("jqcli.commands.research.make_client", lambda app: client)

    def fake_run(received_client, path, **kwargs):
        captured.update(client=received_client, path=path, kwargs=kwargs)
        return {
            "status": "ok",
            "path": path,
            "total_code_cells": 2,
            "executed_cells": 2,
            "cells": [],
            "saved": False,
        }

    monkeypatch.setattr("jqcli.commands.research.run_research_notebook", fake_run)

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path)
        + [
            "research",
            "run",
            "/folder/note.ipynb",
            "--cell",
            "0",
            "--cell",
            "2",
            "--kernel",
            "python3",
            "--execution-timeout",
            "15",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "client": client,
        "path": "folder/note.ipynb",
        "kwargs": {
            "cell_indexes": [0, 2],
            "kernel_name": "python3",
            "execution_timeout": 15.0,
            "on_event": None,
        },
    }
    assert json.loads(result.output)["saved"] is False
    assert client.closed is True


@pytest.mark.parametrize("cells", [["--cell", "-1"], ["--cell", "1", "--cell", "1"]])
def test_research_run_rejects_invalid_cell_selection_before_api(monkeypatch, tmp_path, cells):
    monkeypatch.setattr(
        "jqcli.commands.research.make_client",
        lambda app: (_ for _ in ()).throw(AssertionError("bad cells must not call API")),
    )

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path) + ["research", "run", "note.ipynb"] + cells + ["--yes"],
    )

    assert result.exit_code == 3
    assert json.loads(result.stderr)["error"]["code"] == "usage_error"


def test_research_run_requires_confirmation_before_api(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "jqcli.commands.research.make_client",
        lambda app: (_ for _ in ()).throw(AssertionError("unconfirmed run must not call API")),
    )

    result = CliRunner().invoke(
        main, command_prefix(tmp_path) + ["research", "run", "note.ipynb"]
    )

    assert result.exit_code == 7
    assert json.loads(result.stderr)["error"]["code"] == "confirmation_required"


def test_research_run_table_mode_also_requires_explicit_yes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "jqcli.commands.research.make_client",
        lambda app: (_ for _ in ()).throw(AssertionError("unconfirmed run must not call API")),
    )

    result = CliRunner().invoke(
        main,
        command_prefix(tmp_path, json_output=False) + ["research", "run", "note.ipynb"],
    )

    assert result.exit_code == 7
    assert "必须显式传入 --yes" in result.output


def test_research_execution_kernel_help_matches_default_selection():
    runner = CliRunner()

    exec_help = runner.invoke(main, ["research", "exec", "--help"])
    run_help = runner.invoke(main, ["research", "run", "--help"])

    assert exec_help.exit_code == 0
    assert "默认使用平台默认值" in exec_help.output
    assert "Notebook 元数据" not in exec_help.output
    assert run_help.exit_code == 0
    assert "默认使用 Notebook 元数据或平台默认值" in run_help.output
