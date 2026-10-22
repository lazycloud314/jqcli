from __future__ import annotations

from typing import Any

import click
from rich.console import Console
from rich.table import Table

from jqcli.api.client import ApiClient
from jqcli.api.community import check_strategy_clone, clone_strategy, get_post_detail, iter_latest_posts, list_latest_posts
from jqcli.cli import AppContext
from jqcli.errors import NotAuthenticatedError
from jqcli.output import write_json, write_json_line


@click.group(name="community")
def community_group() -> None:
    """社区文章。"""


def make_client(app: AppContext) -> ApiClient:
    return ApiClient(app.api_base, token=app.token, cookie=app.cookie, timeout=app.timeout, debug=app.debug)


def make_authenticated_client(app: AppContext) -> ApiClient:
    if not (app.token or app.cookie):
        raise NotAuthenticatedError()
    return make_client(app)


def close_client(client: object) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def render_post_table(items: list[dict[str, Any]]) -> None:
    table = Table()
    for name in ("ID", "标题", "作者", "发布时间", "回复", "浏览", "置顶"):
        table.add_column(name)
    for item in items:
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        table.add_row(
            str(item.get("id", "")),
            str(item.get("title", "")),
            str(author.get("name", "")),
            str(item.get("published_at", "")),
            str(item.get("reply_count", "")),
            str(item.get("view_count", "")),
            "是" if item.get("is_top") else "",
        )
    Console().print(table)


@community_group.command("latest")
@click.option("--page-size", type=int, default=50, show_default=True, help="每页条数")
@click.option("--max-pages", type=int, default=None, help="最多读取页数；不传且未设置 --until 时默认 1 页")
@click.option("--until", help="读取到该发布时间为止，支持 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
@click.option("--list-type", type=int, default=1, show_default=True, help="社区 listType，1 为文章")
@click.option("--tags", default="", help="标签 ID，多个用逗号分隔")
@click.option("--since-id", default="", help="遇到该文章 ID 后停止，用于增量拉取")
@click.option("--stream", "stream_output", is_flag=True, help="逐条输出 NDJSON，适合长时间拉取")
@click.pass_obj
def latest(
    app: AppContext,
    page_size: int,
    max_pages: int | None,
    until: str | None,
    list_type: int,
    tags: str,
    since_id: str,
    stream_output: bool,
) -> None:
    client = make_client(app)
    try:
        if stream_output:
            for event in iter_latest_posts(
                client,
                page_size=page_size,
                max_pages=max_pages,
                until=until,
                list_type=list_type,
                tags=tags,
                since_id=since_id or None,
            ):
                write_json_line(event)
            return
        else:
            payload = list_latest_posts(
                client,
                page_size=page_size,
                max_pages=max_pages,
                until=until,
                list_type=list_type,
                tags=tags,
                since_id=since_id or None,
            )
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    else:
        render_post_table(list(payload.get("items", [])))


@community_group.command("detail")
@click.argument("post_id")
@click.option("--reply-page", type=int, default=1, show_default=True, help="讨论区起始页")
@click.option("--reply-pages", type=int, default=1, show_default=True, help="读取讨论区页数")
@click.option("--all-replies", is_flag=True, help="读取全部讨论区页")
@click.option("--with-backtest-stats", is_flag=True, help="同时读取文章策略回测的收益/风险指标")
@click.pass_obj
def detail(
    app: AppContext,
    post_id: str,
    reply_page: int,
    reply_pages: int,
    all_replies: bool,
    with_backtest_stats: bool,
) -> None:
    client = make_client(app)
    try:
        payload = get_post_detail(
            client,
            post_id,
            reply_page=reply_page,
            reply_pages=reply_pages,
            all_replies=all_replies,
            with_backtest_stats=with_backtest_stats,
        )
    finally:
        close_client(client)
    if app.json_output:
        write_json(payload)
    else:
        post = payload.get("post") if isinstance(payload.get("post"), dict) else {}
        discussion = payload.get("discussion") if isinstance(payload.get("discussion"), dict) else {}
        click.echo(f"标题: {post.get('title', '')}")
        click.echo(f"作者: {(post.get('author') or {}).get('name', '')}")
        click.echo(f"发布时间: {post.get('published_at', '')}")
        click.echo(f"回测: {(post.get('backtest') or {}).get('name', '')} {(post.get('backtest') or {}).get('id', '')}")
        backtest = (payload.get("strategy") or {}).get("backtest") if isinstance(payload.get("strategy"), dict) else {}
        stats = backtest.get("stats") if isinstance(backtest, dict) and isinstance(backtest.get("stats"), dict) else {}
        if stats:
            click.echo(f"年化收益: {stats.get('annual_algo_return', '')}")
            click.echo(f"最大回撤: {stats.get('max_drawdown', '')}")
        click.echo(f"讨论: {len(discussion.get('items', []))}/{discussion.get('total_count', '')}")


@community_group.command("clone-strategy")
@click.argument("post_id")
@click.option("--backtest-id", help="文章内回测 ID；不传时自动读取文章详情中的 backtest.id")
@click.option("--reply-id", help="回复中附带回测的回复 ID")
@click.option("--yes", is_flag=True, help="确认执行克隆；不传时只调用检查接口")
@click.pass_obj
def clone_strategy_command(app: AppContext, post_id: str, backtest_id: str | None, reply_id: str | None, yes: bool) -> None:
    client = make_authenticated_client(app)
    try:
        if yes:
            payload = clone_strategy(client, post_id, backtest_id=backtest_id, reply_id=reply_id)
        else:
            payload = check_strategy_clone(client, post_id, backtest_id=backtest_id, reply_id=reply_id)
            payload["execute"] = False
            payload["hint"] = "传 --yes 才会执行克隆并可能扣除积分"
    finally:
        close_client(client)

    if app.json_output:
        write_json(payload)
    elif yes:
        click.echo(f"克隆策略完成: {payload.get('url') or payload.get('redirect') or payload.get('backtest_id', '')}")
    else:
        click.echo(f"可克隆: {'是' if payload.get('can_clone') else '否'}")
        click.echo(f"原因: {payload.get('reason', '')}")
        click.echo(f"所需积分: {payload.get('reduce', '')}")
        click.echo(f"当前积分: {payload.get('amount', '')}")
        click.echo("执行克隆需追加 --yes")
