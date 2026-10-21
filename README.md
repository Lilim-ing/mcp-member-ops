# mcp-member-ops

**会员运营 MCP 服务** —— 把会员数据能力暴露给 AI 客户端，同时管住三件事：谁能调、能看到什么、调用过什么。

```bash
pip install -e ".[dev]"
MCPOPS_ROLE=analyst mcpops serve
```

标准 MCP JSON-RPC over stdio，手写实现（约 200 行），不依赖 SDK。

---

## 三类原语

**Tools（4 个）**

| 工具 | 作用 | 返回 |
|---|---|---|
| `segment_summary` | 按维度汇总某月人群规模与 GMV | 聚合值，无个人信息 |
| `list_campaigns` | 历史活动、渠道、优惠力度 | 公开配置 |
| `get_member_profile` | 单个会员画像与近 90 天消费 | **脱敏后**的明细 |
| `create_campaign_task` | 创建活动执行任务 | 写入任务日志 |

**Resources（2 个）**

- `mcpops://segments` —— 五个人群的标准口径（high_value / at_risk / new / dormant / reactivated）
- `mcpops://schema` —— 可用表与字段，明确标注 phone 与 id_card 只返回脱敏值

**Prompts（1 个）**

- `churn_review` —— 流失复盘的提问模板，强制"数字必须来自工具返回"

---

## RBAC：按能力授权，不按接口

| 能力 | viewer | analyst | admin |
|---|---|---|---|
| `segment_summary` | ✅ | ✅ | ✅ |
| `list_campaigns` | ✅ | ✅ | ✅ |
| `get_member_profile` | ❌ | ✅ | ✅ |
| `create_campaign_task` | ❌ | ❌ | ✅ |

关键在于 `get_member_profile` 和 `segment_summary` 都是"读"，但风险差一个量级：一个返回聚合，一个返回行级明细。所以权限挂在**能力**上，而不是"读/写"上。

授权检查只有一处——`Server._call_tool` 里的 `principal.require(name)`。新增传输方式也绕不过去。

---

## 脱敏在返回层

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

`masked_columns` 是显式字段：客户端能知道"这里被处理过"，而不是以为拿到的是原值。

---

## 审计

每次调用一条 JSONL，成功和拒绝都记：

```json
{"principal": "test-viewer", "role": "viewer", "method": "tools/call",
 "params": {"name": "get_member_profile", ...}, "ok": false,
 "detail": "role 'viewer' 无权调用 get_member_profile"}
```

拒绝记录和成功记录同样重要——只看成功日志，等于不知道谁在试探边界。

---

## 实测

```bash
python -m pytest    # 18 passed
```

| 用例 | 结果 |
|---|---|
| `initialize` | 返回 protocolVersion 与三类 capabilities |
| viewer 调 `segment_summary` | ✅ 返回聚合 |
| viewer 调 `get_member_profile` | ❌ `-32001 role 'viewer' 无权调用` |
| analyst 调 `get_member_profile` | ✅ 脱敏值 |
| analyst 调 `create_campaign_task` | ❌ `-32001` |
| admin 调 `create_campaign_task` | ✅ 写入任务日志 |
| 调 `drop_table` | ❌ `-32602 unknown tool` |
| 调不存在的方法 | ❌ `-32601 method not found` |
| 读 `mcpops://secrets` | ❌ `-32602 unknown resource` |
| 发 `notifications/initialized` | 无响应（JSON-RPC 通知规范） |
| `get_member_profile` 缺 `member_id` | ❌ `-32602 invalid params` |
| `create_campaign_task` 的 `budget_cny` 传字符串 | ❌ `-32602 invalid params` |

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

**为什么手写 JSON-RPC 而不装 MCP SDK？**
SDK 会更省事，但也会把授权点推进框架的调用链里。这个项目要演示的是"权限必须只有一个闸门"，所以在 `dispatch` 里显式写一遍更清楚。协议本身只有 6 个方法，没有复杂到必须用库。

**为什么角色从环境变量读，不做 OAuth？**
OAuth 是部署问题，不是设计问题。演示需要的是"权限模型清楚"，而不是"登录流程完整"。真实部署应该由网关签发 token，服务端只信任网关传入的角色。

**为什么任务写入单独文件，不写数据库？**
服务对仓库应当只读。写操作落在独立的 append-only 日志里，既留下痕迹，又不给服务一个能改主数据的连接。

---

## 边界

- 角色由环境变量指定，无认证流程；生产环境需网关签发身份
- 脱敏基于列名白名单，改列名需同步维护
- 不做限流与配额
- 审计日志为本地文件，无防篡改
- 数据为合成数据，`phone` / `id_card` 均为伪造

## License

MIT
