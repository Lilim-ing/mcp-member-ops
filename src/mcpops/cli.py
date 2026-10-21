"""CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .rbac import ROLES, Principal, principal_from_env
from .server import TOOLS, Server
from .store import Store

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

app = typer.Typer(add_completion=False, help="mcpops · 会员运营 MCP 服务")
console = Console()
# stdout is the JSON-RPC channel in serve mode, so diagnostics must go elsewhere.
err_console = Console(stderr=True)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "fixtures"
TASKS = ROOT / "artifacts" / "tasks.jsonl"
AUDIT = ROOT / "artifacts" / "audit.jsonl"


def _server(role: str = "") -> Server:
    principal = Principal(role=role) if role else principal_from_env()
    return Server(Store(DATA, TASKS), principal, AUDIT)


@app.command()
def serve(role: str = typer.Option("", help="覆盖环境变量 MCPOPS_ROLE")) -> None:
    """以 stdio 方式运行 MCP 服务。"""
    server = _server(role)
    err_console.print(f"[dim]mcpops ready · role={server.principal.role} · stdio[/dim]")
    server.serve_stdio()


@app.command("list")
def list_cmd() -> None:
    """列出可用原语。"""
    table = Table(show_header=True, header_style="bold", title="tools")
    for col in ("name", "description"):
        table.add_column(col, overflow="fold")
    for tool in TOOLS:
        table.add_row(tool["name"], tool["description"])
    console.print(table)
    console.print(f"\n角色：{' / '.join(ROLES)}（环境变量 MCPOPS_ROLE）")


@app.command()
def call(
    tool: str = typer.Argument(...),
    args: str = typer.Option("{}", help="JSON 参数"),
    role: str = typer.Option("", help="以哪个角色调用（留空则读 MCPOPS_ROLE，默认 viewer）"),
) -> None:
    """直接调用一个工具（调试用）。"""
    server = _server(role)
    response = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": json.loads(args)}}
    )
    if "error" in response:
        console.print(f"[red]{response['error']['code']}[/red] {response['error']['message']}")
        raise typer.Exit(code=2)
    text = response["result"]["content"][0]["text"]
    console.print_json(text)


if __name__ == "__main__":
    app()
