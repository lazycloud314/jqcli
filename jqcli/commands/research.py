from __future__ import annotations

import base64
import binascii
import json
import math
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable

import click
from rich.console import Console
from rich.table import Table

from jqcli.api.client import ApiClient
from jqcli.api.research import (
    create_research_directory,
    delete_research_item,
    get_research_item,
    list_research_items,
    move_research_item,
    normalize_research_path,
    save_research_item,
)
from jqcli.api.research_execution import (
    execute_research_code,
    list_research_kernels,
    list_research_kernelspecs,
    list_research_sessions,
    run_research_notebook,
)
from jqcli.authentication import ensure_authentication
from jqcli.errors import (
    ConfirmationRequiredError,
    FileError,
    NotFoundError,
    UsageError,
)
from jqcli.output import write_json, write_json_line


if TYPE_CHECKING:
    from jqcli.cli import AppContext


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
DEFAULT_EXECUTION_TIMEOUT = 120.0


@click.group(name="research")
def research_group() -> None:
    """研究平台文件与 Notebook 管理。"""


def make_client(app: AppContext) -> ApiClient:
    ensure_authentication(app, cookie_only=True)
    return ApiClient(app.api_base, cookie=app.cookie, timeout=app.timeout, debug=app.debug)


def close_client(client: object) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def render_research_table(items: list[dict[str, Any]]) -> None:
    table = Table()
    for name in ("名称", "类型", "大小", "最近修改", "可写"):
        table.add_column(name)
    for item in items:
        writable = item.get("writable")
        table.add_row(
            str(item.get("name", "")),
            str(item.get("type", "")),
            "" if item.get("size") is None else str(item["size"]),
            str(item.get("last_modified", "")),
            "" if writable is None else ("是" if bool(writable) else "否"),
        )
    Console().print(table)


def render_kernelspecs_table(payload: dict[str, Any]) -> None:
    default_name = str(payload.get("default") or "")
    table = Table()
    for name in ("名称", "显示名称", "语言", "中断模式", "默认"):
        table.add_column(name)
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        table.add_row(
            name,
            str(item.get("display_name") or ""),
            str(item.get("language") or ""),
            str(item.get("interrupt_mode") or ""),
            "是" if name == default_name else "",
        )
    Console().print(table)


def render_kernels_table(payload: dict[str, Any]) -> None:
    table = Table()
    for name in ("ID", "名称", "状态", "连接数", "最近活动"):
        table.add_column(name)
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        table.add_row(
            str(item.get("id") or ""),
            str(item.get("name") or ""),
            str(item.get("execution_state") or ""),
            "" if item.get("connections") is None else str(item["connections"]),
            str(item.get("last_activity") or ""),
        )
    Console().print(table)


def render_sessions_table(payload: dict[str, Any]) -> None:
    table = Table()
    for name in ("ID", "路径", "类型", "内核", "状态"):
        table.add_column(name)
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        kernel = item.get("kernel") if isinstance(item.get("kernel"), dict) else {}
        table.add_row(
            str(item.get("id") or ""),
            str(item.get("path") or ""),
            str(item.get("type") or ""),
            str(kernel.get("name") or ""),
            str(kernel.get("execution_state") or ""),
        )
    Console().print(table)


def render_research_item(payload: dict[str, Any], *, include_content: bool) -> None:
    for label, key in (
        ("路径", "path"),
        ("名称", "name"),
        ("类型", "type"),
        ("格式", "format"),
        ("MIME", "mimetype"),
        ("大小", "size"),
        ("最近修改", "last_modified"),
        ("可写", "writable"),
    ):
        value = payload.get(key)
        if value is not None:
            click.echo(f"{label}: {value}")
    if not include_content:
        return
    content = payload.get("content")
    click.echo("内容:")
    if payload.get("format") == "json" or not isinstance(content, str):
        click.echo(json.dumps(content, ensure_ascii=False, indent=2))
    else:
        click.echo(content)


def _safe_basename(payload: dict[str, Any], requested_path: str) -> str:
    for raw_value in (payload.get("name"), payload.get("path"), requested_path):
        value = str(raw_value or "").replace("\\", "/").rstrip("/")
        if not value:
            continue
        name = PurePosixPath(value).name
        if name not in {"", ".", ".."} and "\x00" not in name:
            return name
    raise FileError("无法从研究路径确定安全的本地文件名；请传入 --output")


def _download_bytes(payload: dict[str, Any]) -> bytes:
    if payload.get("type") == "directory":
        raise UsageError("研究目录不能下载；请指定文件或 Notebook")
    content = payload.get("content")
    if content is None:
        raise FileError("研究文件响应未包含内容")
    content_format = str(payload.get("format") or ("json" if payload.get("type") == "notebook" else ""))
    if content_format == "base64":
        if not isinstance(content, str):
            raise FileError("研究文件响应中的 base64 内容不是字符串")
        try:
            return base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise FileError("研究文件响应包含无效 base64 内容") from exc
    if content_format == "json":
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError as exc:
                raise FileError("研究文件响应包含无效 JSON 内容") from exc
        try:
            return (json.dumps(content, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise FileError("研究文件响应中的 JSON 内容无法序列化") from exc
    if content_format == "text":
        if not isinstance(content, str):
            raise FileError("研究文件响应中的文本内容不是字符串")
        return content.encode("utf-8")
    raise FileError(f"不支持下载研究文件格式：{content_format or 'unknown'}")


def _confirm_write(app: AppContext, yes: bool, prompt: str) -> None:
    if (app.non_interactive or app.json_output) and not yes:
        raise ConfirmationRequiredError()
    if not yes:
        click.confirm(prompt, abort=True)


def _confirm_execution(yes: bool) -> None:
    if not yes:
        raise ConfirmationRequiredError(
            "远端执行代码可能产生账户侧副作用；必须显式传入 --yes"
        )


def _validate_execution_timeout(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise UsageError("--execution-timeout 必须大于 0")
    return value


def _validate_cells(cells: tuple[int, ...]) -> list[int] | None:
    if any(cell < 0 for cell in cells):
        raise UsageError("--cell 必须是非负整数")
    if len(set(cells)) != len(cells):
        raise UsageError("--cell 不能重复")
    return list(cells) if cells else None


def _read_execution_code(local_file: str | None, code_stdin: bool) -> str:
    if bool(local_file) == bool(code_stdin):
        raise UsageError("必须且只能指定 --file 或 --code-stdin 之一")
    try:
        if local_file is not None:
            raw = Path(local_file).read_bytes()
        else:
            raw = click.get_binary_stream("stdin").read()
    except OSError as exc:
        source = local_file or "标准输入"
        raise FileError(f"无法读取执行代码：{source}") from exc
    try:
        code = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        source = local_file or "标准输入"
        raise FileError(f"执行代码必须是 UTF-8 文本：{source}") from exc
    if not code.strip():
        raise FileError("执行代码不能为空")
    return code


def _safe_terminal_text(value: object) -> str:
    if isinstance(value, list):
        value = "".join(str(part) for part in value)
    text = str(value or "")
    return "".join(
        character
        for character in text
        if character in "\n\r\t" or (ord(character) >= 32 and not 127 <= ord(character) <= 159)
    )


def _execution_outputs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = payload.get("outputs")
    if isinstance(outputs, list):
        return [output for output in outputs if isinstance(output, dict)]
    return []


def _render_output_summary(output: dict[str, Any]) -> None:
    output_type = str(output.get("type") or output.get("output_type") or "")
    if output_type == "stream":
        name = _safe_terminal_text(output.get("name") or "stream")
        text = _safe_terminal_text(output.get("text"))
        click.echo(f"[{name}] {text}", nl=not text.endswith("\n"))
        return
    if output_type in {"display_data", "execute_result"}:
        data = output.get("data") if isinstance(output.get("data"), dict) else {}
        if "text/plain" in data:
            click.echo(_safe_terminal_text(data["text/plain"]))
        return
    if output_type == "error":
        name = _safe_terminal_text(output.get("ename") or "Error")
        value = _safe_terminal_text(output.get("evalue"))
        click.echo(f"错误: {name}{(': ' + value) if value else ''}")


def render_execution_result(payload: dict[str, Any]) -> None:
    status = _safe_terminal_text(payload.get("status") or "unknown")
    click.echo(f"状态: {status}")
    if payload.get("execution_count") is not None:
        click.echo(f"执行序号: {payload['execution_count']}")
    for output in _execution_outputs(payload):
        _render_output_summary(output)

    cells = payload.get("cells")
    if not isinstance(cells, list):
        return
    for ordinal, cell in enumerate(cells):
        if not isinstance(cell, dict):
            continue
        index = cell.get("cell_index", cell.get("index", ordinal))
        click.echo(f"单元格 {index}:")
        for output in _execution_outputs(cell):
            _render_output_summary(output)


def _stream_callback() -> Callable[[dict[str, Any]], None]:
    def emit(event: dict[str, Any]) -> None:
        if event.get("event") != "done" and event.get("type") != "done":
            write_json_line(event)

    return emit


def _non_root_path(path: str, *, label: str = "研究路径") -> str:
    normalized = normalize_research_path(path)
    if not normalized:
        raise UsageError(f"{label}不能是研究根目录")
    return normalized


def _read_upload(path: Path) -> tuple[Any, str, str]:
    try:
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise FileError(f"本地文件超过 25 MiB 上传限制：{path}")
        raw = path.read_bytes()
        if path.stat().st_size > MAX_UPLOAD_BYTES or len(raw) > MAX_UPLOAD_BYTES:
            raise FileError(f"本地文件超过 25 MiB 上传限制：{path}")
    except OSError as exc:
        raise FileError(f"无法读取本地文件 {path}") from exc

    if path.suffix.lower() == ".ipynb":
        try:
            notebook = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise FileError(f"Notebook 不是有效的 UTF-8 JSON：{path}") from exc
        if not isinstance(notebook, dict):
            raise FileError(f"Notebook 顶层必须是 JSON 对象：{path}")
        return notebook, "notebook", "json"

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return base64.b64encode(raw).decode("ascii"), "file", "base64"
    if "\x00" in text:
        return base64.b64encode(raw).decode("ascii"), "file", "base64"
    return text, "file", "text"


def _write_result(app: AppContext, payload: dict[str, Any], message: str) -> None:
    if app.json_output:
        write_json(payload)
    elif not app.quiet:
        click.echo(message)


@research_group.command("ls")
@click.argument("path", required=False, default="")
@click.pass_obj
def ls(app: AppContext, path: str) -> None:
    client = make_client(app)
    try:
        payload = list_research_items(client, path=path)
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    else:
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        render_research_table(items)


@research_group.command("kernelspecs")
@click.pass_obj
def kernelspecs(app: AppContext) -> None:
    """列出研究平台可用的内核规格。"""
    client = make_client(app)
    try:
        payload = list_research_kernelspecs(client)
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    else:
        render_kernelspecs_table(payload)


@research_group.command("kernels")
@click.pass_obj
def kernels(app: AppContext) -> None:
    """只读列出当前研究内核；不会连接或修改内核。"""
    client = make_client(app)
    try:
        payload = list_research_kernels(client)
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    else:
        render_kernels_table(payload)


@research_group.command("sessions")
@click.pass_obj
def sessions(app: AppContext) -> None:
    """只读列出当前研究会话；不会连接或修改会话。"""
    client = make_client(app)
    try:
        payload = list_research_sessions(client)
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    else:
        render_sessions_table(payload)


@research_group.command("show")
@click.argument("path")
@click.option("--content", "include_content", is_flag=True, help="同时读取并显示内容")
@click.pass_obj
def show(app: AppContext, path: str, include_content: bool) -> None:
    client = make_client(app)
    try:
        payload = get_research_item(client, path, include_content=include_content)
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    else:
        render_research_item(payload, include_content=include_content)


@research_group.command("download")
@click.argument("path")
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=str), help="本地输出文件")
@click.option("--force", is_flag=True, help="覆盖已存在的本地文件")
@click.pass_obj
def download(app: AppContext, path: str, output: str | None, force: bool) -> None:
    client = make_client(app)
    try:
        payload = get_research_item(client, path, include_content=True)
    finally:
        close_client(client)

    data = _download_bytes(payload)
    output_path = Path(output) if output else Path(_safe_basename(payload, path))
    if output_path.exists() and not force:
        raise FileError(f"输出文件已存在：{output_path}")
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb" if force else "xb") as output_file:
            output_file.write(data)
    except FileExistsError:
        raise FileError(f"输出文件已存在：{output_path}") from None
    except OSError as exc:
        raise FileError(f"无法写入文件 {output_path}") from exc

    receipt = {
        "path": str(payload.get("path") or path),
        "output": str(output_path),
        "bytes": len(data),
    }
    if app.json_output:
        write_json(receipt)
    elif not app.quiet:
        click.echo(f"已下载 {receipt['path']} -> {receipt['output']}（{receipt['bytes']} bytes）")


@research_group.command("exec")
@click.option("--file", "local_file", type=click.Path(dir_okay=False, path_type=str), help="读取 UTF-8 Python 文件")
@click.option("--code-stdin", is_flag=True, help="从标准输入读取 UTF-8 代码；必须同时传 --yes")
@click.option("--kernel", "kernel_name", help="临时内核规格名称；默认使用平台默认值")
@click.option(
    "--execution-timeout",
    type=float,
    default=DEFAULT_EXECUTION_TIMEOUT,
    show_default=True,
    help="远端执行总超时秒数",
)
@click.option("--stream", "stream_output", is_flag=True, help="以 JSONL 逐条输出交互事件；仅支持 JSON 格式")
@click.option("--yes", "yes", is_flag=True, help="确认在临时远端会话和内核中执行代码")
@click.pass_obj
def exec_code(
    app: AppContext,
    local_file: str | None,
    code_stdin: bool,
    kernel_name: str | None,
    execution_timeout: float,
    stream_output: bool,
    yes: bool,
) -> None:
    """在自动清理的临时研究会话和内核中执行代码。"""
    if bool(local_file) == bool(code_stdin):
        raise UsageError("必须且只能指定 --file 或 --code-stdin 之一")
    if stream_output and not app.json_output:
        raise UsageError("--stream 仅支持 --format json")
    execution_timeout = _validate_execution_timeout(execution_timeout)
    _confirm_execution(yes)
    code = _read_execution_code(local_file, code_stdin)
    callback = _stream_callback() if stream_output else None

    client = make_client(app)
    try:
        payload = execute_research_code(
            client,
            code,
            kernel_name=kernel_name,
            execution_timeout=execution_timeout,
            on_event=callback,
        )
    finally:
        close_client(client)

    if stream_output:
        write_json_line({"event": "done", "result": payload})
    elif app.json_output:
        write_json(payload)
    elif not app.quiet:
        render_execution_result(payload)


@research_group.command("run")
@click.argument("path")
@click.option("--cell", "cells", type=int, multiple=True, help="仅运行指定的 Notebook 原始零基单元格索引；可重复")
@click.option("--kernel", "kernel_name", help="临时内核规格名称；默认使用 Notebook 元数据或平台默认值")
@click.option(
    "--execution-timeout",
    type=float,
    default=DEFAULT_EXECUTION_TIMEOUT,
    show_default=True,
    help="Notebook 远端执行总超时秒数",
)
@click.option("--stream", "stream_output", is_flag=True, help="以 JSONL 逐条输出交互事件；仅支持 JSON 格式")
@click.option("--yes", "yes", is_flag=True, help="确认在临时远端会话中运行 Notebook")
@click.pass_obj
def run_notebook(
    app: AppContext,
    path: str,
    cells: tuple[int, ...],
    kernel_name: str | None,
    execution_timeout: float,
    stream_output: bool,
    yes: bool,
) -> None:
    """在自动清理的临时研究会话中运行远端 Notebook；不保存输出。"""
    path = _non_root_path(path, label="Notebook 路径")
    selected_cells = _validate_cells(cells)
    if stream_output and not app.json_output:
        raise UsageError("--stream 仅支持 --format json")
    execution_timeout = _validate_execution_timeout(execution_timeout)
    _confirm_execution(yes)
    callback = _stream_callback() if stream_output else None

    client = make_client(app)
    try:
        payload = run_research_notebook(
            client,
            path,
            cell_indexes=selected_cells,
            kernel_name=kernel_name,
            execution_timeout=execution_timeout,
            on_event=callback,
        )
    finally:
        close_client(client)

    if stream_output:
        write_json_line({"event": "done", "result": payload})
    elif app.json_output:
        write_json(payload)
    elif not app.quiet:
        render_execution_result(payload)


@research_group.command("upload")
@click.argument("local_path", type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.argument("remote_path", required=False)
@click.option("--force", is_flag=True, help="覆盖已存在的远端项目")
@click.option("--yes", "-y", is_flag=True, help="确认写入研究平台")
@click.pass_obj
def upload(app: AppContext, local_path: str, remote_path: str | None, force: bool, yes: bool) -> None:
    local = Path(local_path)
    destination = _non_root_path(remote_path or local.name, label="远端路径")
    _confirm_write(app, yes, f"确认上传 {local} 到研究平台 {destination}？")
    content, item_type, content_format = _read_upload(local)

    client = make_client(app)
    try:
        try:
            existing = get_research_item(client, destination, include_content=False)
        except NotFoundError:
            existing = None
        if existing is not None:
            if existing.get("type") == "directory":
                raise FileError(f"远端目标是目录，不能覆盖：{destination}")
            if not force:
                raise FileError(f"远端项目已存在：{destination}；如需覆盖请传入 --force")
        payload = save_research_item(
            client,
            destination,
            content=content,
            item_type=item_type,
            content_format=content_format,
        )
    finally:
        close_client(client)
    _write_result(app, payload, f"已上传到研究平台：{payload.get('path', destination)}")


@research_group.command("mkdir")
@click.argument("path")
@click.option("--yes", "-y", is_flag=True, help="确认创建远端目录")
@click.pass_obj
def mkdir(app: AppContext, path: str, yes: bool) -> None:
    path = _non_root_path(path)
    _confirm_write(app, yes, f"确认在研究平台创建目录 {path}？")
    client = make_client(app)
    try:
        payload = create_research_directory(client, path)
    finally:
        close_client(client)
    _write_result(app, payload, f"研究目录已创建：{payload.get('path', path)}")


@research_group.command("mv")
@click.argument("source")
@click.argument("destination")
@click.option("--yes", "-y", is_flag=True, help="确认移动或重命名远端项目")
@click.pass_obj
def mv(app: AppContext, source: str, destination: str, yes: bool) -> None:
    source = _non_root_path(source, label="源路径")
    destination = _non_root_path(destination, label="目标路径")
    _confirm_write(app, yes, f"确认在研究平台移动 {source} 到 {destination}？")
    client = make_client(app)
    try:
        payload = move_research_item(client, source, destination)
    finally:
        close_client(client)
    _write_result(app, payload, f"研究项目已移动：{payload.get('path', destination)}")


@research_group.command("rm")
@click.argument("path")
@click.option("--yes", "-y", is_flag=True, help="确认删除远端项目")
@click.pass_obj
def rm(app: AppContext, path: str, yes: bool) -> None:
    path = _non_root_path(path)
    _confirm_write(app, yes, f"确认删除研究平台项目 {path}？")
    client = make_client(app)
    try:
        payload = delete_research_item(client, path) or {"ok": True, "path": path}
    finally:
        close_client(client)
    _write_result(app, payload, f"研究项目已删除：{payload.get('path', path)}")
