from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from jqcli.api.client import ApiClient
from jqcli.api.strategy import (
    create_strategy, create_strategy_folder, delete_strategy, get_strategy,
    list_strategies, list_strategy_folders, move_strategy, update_strategy,
)
from jqcli.cli import AppContext
from jqcli.errors import ConfirmationRequiredError, FileError, NotAuthenticatedError, UsageError
from jqcli.output import write_json


TEMPLATE = """def initialize(context):
    pass


def handle_data(context, data):
    pass
"""


@click.group(name="strategy")
def strategy_group() -> None:
    """策略管理。"""


def make_client(app: AppContext) -> ApiClient:
    if not (app.token or app.cookie):
        raise NotAuthenticatedError()
    return ApiClient(app.api_base, token=app.token, cookie=app.cookie, timeout=app.timeout)


def close_client(client: object) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def read_code(file_path: str | None, code_stdin: bool) -> str | None:
    if file_path and code_stdin:
        raise UsageError("--file 与 --code-stdin 不可同时使用")
    if file_path:
        path = Path(file_path)
        try:
            code = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise FileError(f"无法读取文件 {file_path}") from exc
        if not code.strip():
            raise FileError(f"文件 {file_path} 为空")
        return code
    if code_stdin:
        code = sys.stdin.read()
        if not code.strip():
            raise UsageError("stdin 中没有策略源码")
        return code
    return None


def render_strategy_table(items: list[dict[str, Any]]) -> None:
    table = Table()
    for name in ("ID", "名称", "类型", "创建时间", "最近修改"):
        table.add_column(name)
    for item in items:
        table.add_row(
            str(item.get("id", "")),
            str(item.get("name", "")),
            str(item.get("type", "")),
            str(item.get("created_at", "")),
            str(item.get("updated_at", "")),
        )
    Console().print(table)


@strategy_group.command("ls")
@click.option("--sort", "sort_by", type=click.Choice(["name", "created", "updated"]), default="updated")
@click.option("--limit", type=int, default=50)
@click.option("--all", "all_items", is_flag=True)
@click.pass_obj
def ls(app: AppContext, sort_by: str, limit: int, all_items: bool) -> None:
    client = make_client(app)
    try:
        payload = list_strategies(client, sort=sort_by, limit=limit, all_items=all_items)
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    else:
        render_strategy_table(list(payload.get("items", [])))


@strategy_group.command("show")
@click.argument("strategy_id")
@click.option("--code", "show_code", is_flag=True)
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=str))
@click.option("--force", is_flag=True)
@click.pass_obj
def show(app: AppContext, strategy_id: str, show_code: bool, output: str | None, force: bool) -> None:
    include_code = show_code or bool(output)
    client = make_client(app)
    try:
        payload = get_strategy(client, strategy_id, include_code=include_code)
    finally:
        close_client(client)
    if output:
        path = Path(output)
        if path.exists() and not force:
            raise FileError(f"输出文件已存在：{output}")
        code = payload.get("code")
        if code is None:
            raise FileError("远端响应未包含策略源码")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(code), encoding="utf-8")
        except OSError as exc:
            raise FileError(f"无法写入文件 {output}") from exc
    if app.json_output:
        write_json(payload)
    else:
        click.echo(f"ID: {payload.get('id', '')}")
        click.echo(f"名称: {payload.get('name', '')}")
        if include_code and payload.get("code") is not None and not output:
            click.echo(str(payload["code"]))


@strategy_group.command("folders")
@click.option("--parent", "parent_id", default="0", help="父文件夹 ID，0 表示根目录。")
@click.pass_obj
def folders(app: AppContext, parent_id: str) -> None:
    """列出策略文件夹。"""
    client = make_client(app)
    try:
        payload = list_strategy_folders(client, parent_id=parent_id)
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    else:
        table = Table("ID", "名称", "父文件夹")
        for item in payload["items"]:
            table.add_row(item["id"], item["name"], item["parent_id"])
        Console().print(table)


@strategy_group.command("mkdir")
@click.argument("name")
@click.option("--parent", "parent_id", default="0", help="父文件夹 ID，0 表示根目录。")
@click.pass_obj
def mkdir(app: AppContext, name: str, parent_id: str) -> None:
    """创建策略文件夹。"""
    client = make_client(app)
    try:
        payload = create_strategy_folder(client, name=name, parent_id=parent_id)
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    elif not app.quiet:
        click.echo(f"文件夹已创建：{payload['name']} ({payload['id']})")


@strategy_group.command("mv")
@click.argument("strategy_id")
@click.option("--folder", "folder_id", required=True, help="目标文件夹 ID，0 表示根目录。")
@click.pass_obj
def mv(app: AppContext, strategy_id: str, folder_id: str) -> None:
    """移动策略并核实目标文件夹成员，保留策略源码和回测。"""
    client = make_client(app)
    try:
        payload = move_strategy(client, strategy_id, folder_id=folder_id)
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    elif not app.quiet:
        click.echo(f"策略已移动：{payload['name']} → {folder_id}")


@strategy_group.command("new")
@click.argument("name")
@click.option("--file", "-f", "file_path", type=click.Path(dir_okay=False, path_type=str))
@click.option("--code-stdin", is_flag=True)
@click.option("--type", "strategy_type", type=click.Choice(["stock", "futures"]), default="stock")
@click.pass_obj
def new(app: AppContext, name: str, file_path: str | None, code_stdin: bool, strategy_type: str) -> None:
    code = read_code(file_path, code_stdin)
    if code is None:
        code = TEMPLATE
    client = make_client(app)
    try:
        payload = create_strategy(client, name=name, code=code, strategy_type=strategy_type)
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    elif not app.quiet:
        click.echo(f"策略已创建：{payload.get('id', '')}")


@strategy_group.command("edit")
@click.argument("strategy_id")
@click.option("--file", "-f", "file_path", type=click.Path(dir_okay=False, path_type=str))
@click.option("--code-stdin", is_flag=True)
@click.option("--name")
@click.pass_obj
def edit(app: AppContext, strategy_id: str, file_path: str | None, code_stdin: bool, name: str | None) -> None:
    code = read_code(file_path, code_stdin)
    if code is None and name is None:
        raise UsageError("请提供 --file、--code-stdin 或 --name")
    client = make_client(app)
    try:
        payload = update_strategy(client, strategy_id, name=name, code=code)
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    elif not app.quiet:
        click.echo(f"策略已更新：{payload.get('id', strategy_id)}")


@strategy_group.command("rm")
@click.argument("strategy_id")
@click.option("--yes", "-y", is_flag=True)
@click.pass_obj
def rm(app: AppContext, strategy_id: str, yes: bool) -> None:
    if app.non_interactive and not yes:
        raise ConfirmationRequiredError()
    if not app.non_interactive and not yes:
        click.confirm(f"确认删除策略 {strategy_id}？", abort=True)
    client = make_client(app)
    try:
        payload = delete_strategy(client, strategy_id) or {"ok": True, "id": strategy_id}
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    elif not app.quiet:
        click.echo(f"策略已删除：{strategy_id}")
