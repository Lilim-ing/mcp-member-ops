# mcp-member-ops

会员运营 MCP 服务。把会员数据能力暴露给 AI 客户端，同时限制三件事：谁能调、能看到什么、调用过什么。

```bash
pip install -e ".[dev]"
MCPOPS_ROLE=analyst mcpops serve
```

标准 MCP JSON-RPC over stdio，手写实现（约 200 行），不依赖 SDK。

---

## 原语

Tools（4 个）

| 工具 | 作用 | 返回 |
|---|---|---|
| `segment_summary` | 按维度汇总某月人群规模与 GMV | 聚合值，无个人信息 |
| `list_campaigns` | 历史活动、渠道、优惠力度 | 公开配置 |
| `get_member_profile` | 单个会员画像与近 90 天消费 | 脱敏后的明细 |
| `create_campaign_task` | 创建活动执行任务 | 写入任务日志 |

Resources（2 个）

- `mcpops://segments`：五个人群的标准口径（high_value / at_risk / new / dormant / reactivated）
- `mcpops://schema`：可用表与字段，标注 phone 与 id_card 只返回脱敏值

Prompts（1 个）

- `churn_review`：流失复盘的提问模板，要求数字必须来自工具返回

---

## 权限

| 能力 | viewer | analyst | admin |
|---|---|---|---|
| `segment_summary` | 是 | 是 | 是 |
| `list_campaigns` | 是 | 是 | 是 |
| `get_member_profile` | 否 | 是 | 是 |
| `create_campaign_task` | 否 | 否 | 是 |

`get_member_profile` 和 `segment_summary` 都是读，但一个返回行级明细，一个只返回聚合，风险不在一个量级。所以权限按具体能力授予，读/写这个维度不够用。

授权检查只有一处：`Server._call_tool` 里的 `principal.require(name)`。以后加新的传输方式也得走这里。

---

## 脱敏

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_member_profile","arguments":{"member_id":"M000001"}}}' \
  | MCPOPS_ROLE=analyst mcpops serve
```

```json
{
  "member_id": "M000001",
  "member_level": "金卡",
  "phone": "179****7525",
  "id_card": "1101**********0731",
  "orders_90d": 2,
  "masked_columns": ["phone", "id_card"]
}
```

`masked_columns` 是显式字段。客户端能看出这些列被处理过，不会误以为是原值。

---

## 审计

每次调用记一条 JSONL，成功和拒绝都写：

```json
{"principal": "test-viewer", "role": "viewer", "method": "tools/call",
 "params": {"name": "get_member_profile", ...}, "ok": false,
 "detail": "role 'viewer' 无权调用 get_member_profile"}
```

拒绝也要记。只留成功日志的话，越权尝试就查不到了。

---

## 实测

```bash
python -m pytest    # 18 passed
```

| 用例 | 结果 |
|---|---|
| `initialize` | 返回 protocolVersion 与三类 capabilities |
| viewer 调 `segment_summary` | 通过，返回聚合 |
| viewer 调 `get_member_profile` | 拒绝，`-32001 role 'viewer' 无权调用` |
| analyst 调 `get_member_profile` | 通过，脱敏值 |
| analyst 调 `create_campaign_task` | 拒绝，`-32001` |
| admin 调 `create_campaign_task` | 通过，写入任务日志 |
| 调 `drop_table` | 拒绝，`-32602 unknown tool` |
| 调不存在的方法 | 拒绝，`-32601 method not found` |
| 读 `mcpops://secrets` | 拒绝，`-32602 unknown resource` |
| 发 `notifications/initialized` | 无响应（JSON-RPC 通知规范） |
| `get_member_profile` 缺 `member_id` | 拒绝，`-32602 invalid params` |
| `create_campaign_task` 的 `budget_cny` 传字符串 | 拒绝，`-32602 invalid params` |

---

## 快速开始

```bash
pip install -e ".[dev]"

mcpops list                                    # 看原语
mcpops call segment_summary --args '{"dimension":"member_level","month":"2026-09"}'
mcpops call get_member_profile --args '{"member_id":"M000001"}' --role analyst
MCPOPS_ROLE=viewer mcpops serve                # 只读角色跑服务

python -m pytest
```

接入 AI 客户端时，把 `mcpops serve` 配成 stdio server 即可。

---

## 设计取舍

手写 JSON-RPC 而不用 MCP SDK，是因为 SDK 会把授权点埋进框架的调用链里。这里想说明权限只能有一个入口，所以在 `dispatch` 里显式写一遍更直观。协议本身只有 6 个方法，用库的必要性不大。

角色从环境变量读，不做 OAuth，是因为 OAuth 属于部署环节。这里想讲清楚权限模型，登录流程不是重点。真实部署应该由网关签发 token，服务端只信任网关传入的角色。

任务写入放在单独文件里，是因为服务对仓库应当只读。写操作落到独立的 append-only 日志，既留下痕迹，也不用给服务一个能改主数据的连接。

---

## 边界

- 角色由环境变量指定，无认证流程；生产环境需网关签发身份
- 脱敏基于列名白名单，改列名需同步维护
- 不做限流与配额
- 审计日志为本地文件，无防篡改
- 数据为合成数据，`phone` / `id_card` 均为伪造

## License

MIT
