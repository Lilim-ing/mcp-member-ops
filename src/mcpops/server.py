"""MCP server: JSON-RPC 2.0 over stdio, three primitives.

Implemented by hand rather than via an SDK so the permission check sits in
exactly one place -- `dispatch` -- and cannot be bypassed by a new transport.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .rbac import Principal
from .store import Store

PROTOCOL_VERSION = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "segment_summary",
        "description": "按维度汇总某月的人群规模与 GMV（仅聚合，不含个人信息）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "enum": ["member_level", "city_tier", "age_band", "register_channel"],
                },
                "month": {"type": "string", "description": "YYYY-MM"},
            },
            "required": ["dimension", "month"],
        },
    },
    {
        "name": "list_campaigns",
        "description": "列出历史活动及其渠道与优惠力度",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_member_profile",
        "description": "查询单个会员的画像与近 90 天消费（返回前自动脱敏）",
        "inputSchema": {
            "type": "object",
            "properties": {"member_id": {"type": "string"}},
            "required": ["member_id"],
        },
    },
    {
        "name": "create_campaign_task",
        "description": "创建一个活动执行任务（写操作，仅 admin）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "segment": {"type": "string"},
                "action": {"type": "string"},
                "budget_cny": {"type": "number"},
            },
            "required": ["segment", "action"],
        },
    },
]

RESOURCES = [
    {
        "uri": "mcpops://segments",
        "name": "人群定义",
        "description": "五个标准人群的口径说明",
        "mimeType": "application/json",
    },
    {
        "uri": "mcpops://schema",
        "name": "数据表结构",
        "description": "可用表与字段（不含敏感字段）",
        "mimeType": "application/json",
    },
]

PROMPTS = [
    {
        "name": "churn_review",
        "description": "流失复盘的提问模板",
        "arguments": [{"name": "month", "required": True}],
    }
]

SEGMENTS = {
    "high_value": "近 12 个月累计 GMV 位于前 20%，且近 90 天有购买",
    "at_risk": "近 90 天无购买，但历史购买 ≥3 单",
    "new": "注册 30 天内，尚未产生第 2 单",
    "dormant": "180 天以上无购买",
    "reactivated": "180 天无购买后重新产生订单",
}

SCHEMA = {
    "members": ["member_id", "member_level", "city_tier", "age_band", "register_channel", "register_date"],
    "orders": ["order_id", "member_id", "order_time", "channel", "paid_amount", "status"],
    "order_items": ["order_id", "sku_id", "category", "qty", "is_returned"],
    "campaigns": ["campaign_id", "name", "start_date", "end_date", "channel", "coupon_value"],
    "note": "phone 与 id_card 存在于 members 表，但服务端只返回脱敏值",
}


class MCPError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Server:
    def __init__(self, store: Store, principal: Principal, audit_path: str | Path) -> None:
        self.store = store
        self.principal = principal
        self.audit_path = Path(audit_path)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    # -- audit -----------------------------------------------------------
    def _audit(self, method: str, params: dict, ok: bool, detail: str = "") -> None:
        record = {
            "principal": self.principal.name,
            "role": self.principal.role,
            "method": method,
            "params": params,
            "ok": ok,
            "detail": detail,
        }
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # -- JSON-RPC --------------------------------------------------------
    def handle(self, request: dict) -> dict | None:
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params") or {}

        # JSON-RPC notifications carry no id and must not receive a response.
        # MCP clients send notifications/initialized right after the handshake;
        # answering it with -32601 would make a compliant client look broken.
        if "id" not in request:
            try:
                self.dispatch(method, params)
            except MCPError:
                pass
            return None

        try:
            result = self.dispatch(method, params)
            self._audit(method, params, True)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except MCPError as exc:
            self._audit(method, params, False, exc.message)
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": exc.code, "message": exc.message}}
        except PermissionError as exc:
            self._audit(method, params, False, str(exc))
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32001, "message": str(exc)}}
        except Exception as exc:  # noqa: BLE001
            self._audit(method, params, False, f"{type(exc).__name__}: {exc}")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
            }

    def dispatch(self, method: str, params: dict) -> Any:
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return {}
        if method == "initialize":
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "mcpops", "version": "0.1.0"},
            }
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self._call_tool(params)
        if method == "resources/list":
            return {"resources": RESOURCES}
        if method == "resources/read":
            return self._read_resource(params)
        if method == "prompts/list":
            return {"prompts": PROMPTS}
        if method == "prompts/get":
            return self._get_prompt(params)
        if method == "ping":
            return {}
        raise MCPError(-32601, f"method not found: {method}")

    # -- primitives ------------------------------------------------------
    def _validate_args(self, name: str, args: Any) -> dict:
        """Enforce the tool's declared inputSchema before touching the store."""
        schema = next(t for t in TOOLS if t["name"] == name)["inputSchema"]
        if not isinstance(args, dict):
            raise MCPError(-32602, "invalid params: arguments must be an object")

        for key in schema.get("required", []):
            if key not in args or args[key] is None:
                raise MCPError(-32602, f"invalid params: missing required argument {key!r}")

        properties = schema.get("properties", {})
        for key, value in args.items():
            if key not in properties:
                raise MCPError(-32602, f"invalid params: unexpected argument {key!r}")
            expected = properties[key].get("type")
            if expected == "string" and not isinstance(value, str):
                raise MCPError(-32602, f"invalid params: {key!r} must be a string")
            if expected == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise MCPError(-32602, f"invalid params: {key!r} must be a number")
            allowed = properties[key].get("enum")
            if allowed and value not in allowed:
                raise MCPError(-32602, f"invalid params: {key!r} must be one of {allowed}")
        return args

    def _call_tool(self, params: dict) -> dict:
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            raise MCPError(-32602, f"unknown tool: {name}")

        # The single authorisation gate for every tool.
        self.principal.require(name)
        args = self._validate_args(name, args)

        if name == "segment_summary":
            rows = self.store.segment_summary(args.get("dimension", ""), args.get("month", ""))
        elif name == "list_campaigns":
            rows = self.store.campaigns()
        elif name == "get_member_profile":
            profile = self.store.member_profile(args.get("member_id", ""))
            if profile is None:
                raise MCPError(-32602, f"member not found: {args.get('member_id')}")
            rows = [profile]
        elif name == "create_campaign_task":
            rows = [
                self.store.append_task(
                    {
                        "segment": args.get("segment"),
                        "action": args.get("action"),
                        "budget_cny": args.get("budget_cny"),
                        "created_by": self.principal.name,
                    }
                )
            ]
        else:  # pragma: no cover - guarded by the set check above
            raise MCPError(-32602, f"unhandled tool: {name}")

        return {"content": [{"type": "text", "text": json.dumps(rows, ensure_ascii=False)}], "isError": False}

    def _read_resource(self, params: dict) -> dict:
        uri = params.get("uri", "")
        if uri == "mcpops://segments":
            payload = SEGMENTS
        elif uri == "mcpops://schema":
            payload = SCHEMA
        else:
            raise MCPError(-32602, f"unknown resource: {uri}")
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, ensure_ascii=False)}]}

    def _get_prompt(self, params: dict) -> dict:
        if params.get("name") != "churn_review":
            raise MCPError(-32602, f"unknown prompt: {params.get('name')}")
        month = (params.get("arguments") or {}).get("month", "本月")
        return {
            "description": "流失复盘模板",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            f"请复盘 {month} 的会员流失情况：\n"
                            "1) 用 segment_summary 按会员等级看规模变化\n"
                            "2) 找出流失集中的 2 个群体\n"
                            "3) 给出一个可执行动作（人群/渠道/权益/指标）\n"
                            "所有数字必须来自工具返回，不要估算。"
                        ),
                    },
                }
            ],
        }

    # -- transport -------------------------------------------------------
    def serve_stdio(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}), flush=True)
                continue

            if isinstance(request, list):
                # Batch: notifications inside the batch produce no response.
                responses = [
                    r for r in (self.handle(item) for item in request if isinstance(item, dict)) if r is not None
                ]
                if responses:
                    print(json.dumps(responses, ensure_ascii=False), flush=True)
                continue

            if not isinstance(request, dict):
                print(
                    json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}),
                    flush=True,
                )
                continue

            response = self.handle(request)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
