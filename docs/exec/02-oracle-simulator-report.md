# 阶段 2 完成报告

完成日期：2026-08-29  
结论：实现完成，允许进入独立 Review。

## 实现范围

- `reference_simulator`：三类场景的纯不可变状态、显式转换结果、稳定 Hash、
  seeded Random 和有界 BFS。
- `oracle`：只消费冻结 `RuleSpec`、Snapshot、Receipt、Event 的确定性判定器。
- `attack_runtime`：全新 RunSpace HTTP 重放、语义 ID 到运行时 ID 映射、
  `CONFIRMED_VIOLATION` / `MODEL_DIVERGENCE` 分类和删除式最小化。
- 未实现 LLM、Rule Compiler、多 Agent、Benchmark UI 或成品 Web。

## 八个 invariant 的形式化口径

| Invariant | 输入 | `VIOLATED` 条件 | 缺证据处理 |
| --- | --- | --- | --- |
| `NET_PAID_NON_NEGATIVE` | 最终订单 | `paid - refunded < 0` | 金额或订单集合缺失时 `INSUFFICIENT_EVIDENCE` |
| `REFUND_NOT_EXCEED_PAID` | 最终订单 | `refunded > paid` | 金额或订单集合缺失时 `INSUFFICIENT_EVIDENCE` |
| `COUPON_SINGLE_CONSUMPTION` | 优惠券、事件 | `usage_count > 1` 或重复 `COUPON_USED` | 优惠券集合缺失时 `INSUFFICIENT_EVIDENCE` |
| `POINTS_VALUE_CONSERVATION` | 积分规则、订单、用户、事件 | 发放或按退款比例扣回不符合规则，或余额为负 | 规则所需字段缺失时 `INSUFFICIENT_EVIDENCE` |
| `ORDER_TERMINAL_MONOTONICITY` | 连续快照 | `CANCELLED` / `REFUNDED` 后回到其他状态 | 少于两个快照时 `INSUFFICIENT_EVIDENCE` |
| `ENTITLEMENT_NON_NEGATIVE` | 最终权益 | `granted - consumed - revoked < 0` | 权益集合缺失时 `INSUFFICIENT_EVIDENCE` |
| `ENTITLEMENT_REFUND_CONSISTENCY` | 会员规则、会员、权益、事件 | 退款策略、终止后余额或退款后消费不一致 | 关联会员/权益缺失时 `INSUFFICIENT_EVIDENCE` |
| `IDEMPOTENT_EFFECT` | Receipt、Event | 同 action/key 结果不一致或产生重复业务事件 | 无幂等回执时 `INSUFFICIENT_EVIDENCE` |

没有相关业务对象时返回 `NOT_APPLICABLE`；只有 `VIOLATED` 可进入确认分支。

## 独立性审计

依赖方向如下：

```text
policy_schema + domain_contracts
        ├── reference_simulator
        └── oracle

reference_simulator + oracle + httpx
        └── attack_runtime ──HTTP──> Commerce Sandbox
```

AST 架构测试阻止 `reference_simulator` 和 `oracle` import
`rulearena_commerce_sandbox` 或 `services.commerce_sandbox`。Oracle 源码同时禁止
`sandbox_profile`、`sandbox_version` 和 `vulnerable` 判定分支。Simulator 不访问
数据库、Redis 或网络。

## 确定性搜索结果

- 固定 RuleSpec 和动作序列产生相同规范化状态与 SHA-256 Hash。
- BFS 使用 Hash 去重，并记录 `max_depth`、`max_nodes`、展开节点数和唯一状态数。
- seeded Random 使用固定种子 `20260829`；相同预算下轨迹和终态 Hash 完全一致。
- `EXHAUSTED` / `BUDGET_EXHAUSTED` 只描述本次搜索结果，不表示系统安全。

## 候选到最小反例证据链

本地服务：`http://127.0.0.1:8001`，通过 pnpm/uv 本地开发方式验证；Docker 仅提供
PostgreSQL 和 Redis 基础设施。

1. Promotion 候选通过 HTTP 在 vulnerable RunSpace 执行：创建用户、发券、创建订单、
   用券、支付、退款 60、退款 100。
2. Oracle 从最终真实快照得到 `refunded=160.00 > paid=100.00`，输出
   `REFUND_NOT_EXCEED_PAID / VIOLATED`。
3. `attack_runtime` 仅据该真实 Oracle Finding 输出 `CONFIRMED_VIOLATION`。
4. 删除式最小化每次创建全新 RunSpace，将 7 步缩减为 5 步：创建用户、创建订单、
   支付、退款 60、退款 100。
5. 对剩余 5 个动作逐一删除并重新 HTTP 重放，均变为 `MODEL_DIVERGENCE`；因此结果是
   当前删除空间内的 1-minimal，不宣称全局最短。

稳定性实验：Promotion 超额退款、Refund Points 价值不守恒、Membership 退款后权益
仍可消费三个 vulnerable Case 均连续重放 3/3 确认；相同序列在 fixed Profile 全部为
`MODEL_DIVERGENCE`。每组重复运行的 invariant、规范化终态 Hash 和证据等价。

## 验证结果

```text
uv run --offline ruff check .
All checks passed!

uv run --offline mypy services packages scripts tests
Success: no issues found in 48 source files

uv run --offline pytest -q
32 passed, 19 skipped

SANDBOX_HTTP_URL=http://127.0.0.1:8001 uv run --offline pytest tests/replay -q
4 passed (包含 3×3 漏洞重放、fixed 防误报和真实 1-minimal)
```
