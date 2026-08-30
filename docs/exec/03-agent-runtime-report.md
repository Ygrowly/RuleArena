# 阶段 3 完成报告：Rule Compiler 与多策略 Agent Runtime

状态：已实现并通过确定性、真实 PostgreSQL 与真实 Sandbox HTTP 验证  
日期：2026-08-30

## 1. 实现摘要

- Rule Compiler 只接受内置三类模板，所有模型调用统一经过 `LLMAdapter`；候选经严格
  `RuleSpec` 与确定性校验，歧义不会生成可运行版本。
- `RuleVersion` 使用规范 JSON 的 SHA-256 content hash；PostgreSQL 以唯一键去重，并由
  trigger 阻止 UPDATE/DELETE。编译草稿、结构化问题与 LLM 调用审计均持久化。
- Control API 提供编译、确认、Run 创建/查询/取消、Counterexample 查询和可恢复 SSE；
  创建 Run 使用 `Idempotency-Key`，ARQ 使用稳定 `attack:{run_id}` Job ID。
- Worker 用显式代码状态机调度三个独立 `StrategyAgent`。Agent 只能提出结构化动作或停止/
  候选，不得写 outcome；候选只有经真实 Sandbox 重放和 Oracle `VIOLATED` 才会持久化为
  confirmed Counterexample。
- AttackRun、StrategyRun、预算、Checkpoint、事件和 Counterexample 的生产存储为
  PostgreSQL；状态、Checkpoint 和唯一对象分别使用 CAS、版本 CAS 与唯一约束。
- Sandbox 写超时后先按稳定幂等键查询 Receipt；404 转为 `ACTION_UNKNOWN`，不会盲重试。

## 2. Runtime 状态转换表

| 当前状态 | 允许后继 | 约束 |
| --- | --- | --- |
| `DRAFT` | `NEEDS_CONFIRMATION`, `READY` | 有歧义不能直接运行 |
| `NEEDS_CONFIRMATION` | `READY` | 仅显式确认后的冻结版本可进入 |
| `READY` | `SEARCHING`, `CANCEL_REQUESTED`, `FAILED` | Worker 以 CAS 抢占 |
| `SEARCHING` | `REPLAYING`, `COMPLETED`, `RECOVERING`, `CANCEL_REQUESTED`, `FAILED` | Agent 不能触发非法跳转 |
| `REPLAYING` | `SEARCHING`, `COMPLETED`, `RECOVERING`, `CANCEL_REQUESTED`, `FAILED` | confirmed 仅来自 Replay Oracle |
| `RECOVERING` | `SEARCHING`, `REPLAYING`, `CANCEL_REQUESTED`, `FAILED` | 从持久化 Checkpoint/候选恢复 |
| `CANCEL_REQUESTED` | `CANCELLED` | Worker 启动时及搜索安全点均确认取消 |
| `FAILED` | `RECOVERING` | 恢复清除旧的终态 outcome |
| `COMPLETED`, `CANCELLED` | 无 | 旧 Worker 的 CAS 无法覆盖终态 |

`status` 与 `outcome` 分列保存。只有 `COMPLETED`、`CANCELLED`、`FAILED` 能同时写入固定
Outcome；`COMPLETED` 本身不代表“安全”。

## 3. Agent 工具权限矩阵

| 能力 | VALUE_FLOW | LIFECYCLE | BOUNDARY | 边界实现 |
| --- | ---: | ---: | ---: | --- |
| 查询规范化模拟状态 | 允许 | 允许 | 允许 | 只读 `AgentContext.normalized_state` |
| 列出当前合法动作 Schema | 允许 | 允许 | 允许 | Runtime 从 Reference Simulator 生成 |
| 在 Simulator 执行动作 | 允许提议 | 允许提议 | 允许提议 | Runtime 校验动作、参数、范围、重复与预算后执行 |
| 提交候选 | 允许 | 允许 | 允许 | 结构化 `StopProposal.candidate_invariant`，只能进入重放 |
| 写 outcome / 自封 confirmed | 禁止 | 禁止 | 禁止 | Proposal `extra=forbid`，outcome 仅由 Workflow 写入 |
| Sandbox 直连 | 禁止 | 禁止 | 禁止 | 只有 `SandboxReplayRunner` 持有 HTTP/token |
| 数据库、Redis、文件、Shell、网络 | 禁止 | 禁止 | 禁止 | 不存在对应 Agent 工具或句柄 |
| Sandbox Profile / Ground Truth / Benchmark 答案 | 禁止 | 禁止 | 禁止 | Context key denylist + 架构数据路径隔离 |

## 4. 三策略输入隔离证据

1. 每个策略必须使用不同 `StrategyAgent` 实例；构造 Worker 时共享实例会被拒绝。
2. `AgentContext` 仅含冻结 RuleSpec、当前规范化状态、合法动作、自己的最近 12 条历史、
   剩余预算和已确认 Counterexample ID。
3. `(attack_run_id, strategy_type)` 数据库唯一；每个策略有独立 Checkpoint 主键、frontier、
   usage、model config hash 与 prompt version。
4. 内存参考 Store 对 Context/Checkpoint 深拷贝；隔离测试得到三个不同 StrategyRun ID、
   三份互不别名的私有 Checkpoint。Agent 之间没有消息通道，也不共享候选路径或 CoT。

## 5. 崩溃恢复与幂等 Trace

| 故障点 | 首次持久化 | 重启行为 | 验证结果 |
| --- | --- | --- | --- |
| Simulator action 后、Checkpoint 前 | Strategy usage 已记录，动作路径未提交 | 从上个 Checkpoint/初态重算 | 无 Sandbox 副作用 |
| Checkpoint 后 | `version=1`, `actions=1` | 恢复同一路径，不重复追加动作 | 最终 Checkpoint 仍为 1 个动作 |
| Sandbox 响应丢失 | 稳定 action idempotency key | 先 GET Receipt | POST 仅 1 次、Receipt 查询 1 次 |
| Receipt 不存在 | 无权威成功证据 | 记录 `ACTION_UNKNOWN` 并中止 | 不盲重试 |
| Oracle 后、Counterexample 前 | 候选 invariant 已写 Checkpoint | 重放同一候选 | replay 2 次、Counterexample 1 条 |
| Counterexample 后、状态完成前 | Counterexample 唯一键已提交 | 检测已有结果并 CAS 完成 | replay 1 次、Counterexample 1 条 |

数据库实测还覆盖：同 Job 重投返回相同 AttackRun、同策略重投返回相同 StrategyRun、旧状态
CAS 返回 false、旧 Checkpoint version 被拒绝、RuleVersion UPDATE 被 trigger 拒绝。

## 6. 中文规则到 confirmed 最小反例

真实验证使用 FakeLLM（可重复）和真实 PostgreSQL-backed Commerce Sandbox HTTP：

1. 输入模板 `refund-points` 和中文规则“每消费 1 元获得 1 积分，退款时按退款金额撤销积分”。
2. Compiler 输出严格 `REFUND_POINTS` RuleSpec；用户确认后冻结 RuleVersion 和 content hash。
3. `VALUE_FLOW` 在 Reference Simulator 中生成：创建用户 500 元 → 创建订单 100 元 → 支付 →
   退款 50 元，并以 `POINTS_VALUE_CONSERVATION` 提交候选。
4. Runtime 切换 `SEARCHING → REPLAYING`，从干净的 `vulnerable` Sandbox RunSpace 通过 HTTP
   执行四个稳定幂等动作。
5. Oracle 从真实 Snapshot/Receipt/Event 判定积分价值不守恒，Replay 分类为
   `CONFIRMED_VIOLATION`；Agent 的候选文本本身没有确认权限。
6. Delta Debugging 每次创建干净 RunSpace；该 4 步路径保持为当前删除空间的 1-minimal，
   Counterexample 唯一持久化，Run 最终为 `COMPLETED / CONFIRMED_VIOLATION`。

## 7. 验证结果

```text
阶段三建议测试：20 passed / 1 skipped + 7 passed + 7 passed
Ruff（attack_worker + attack_runtime）：All checks passed!
Mypy（attack_worker + attack_runtime）：Success
真实 PostgreSQL migration upgrade + drift check：No new upgrade operations detected.
PostgreSQL CAS/唯一键/不可变触发器测试：1 passed
真实 Redis ARQ 重复入队测试：1 passed
真实中文规则 → confirmed Counterexample：1 passed
全量回归（含真实 PostgreSQL、Redis、数据库隔离与 Sandbox HTTP）：100 passed
全仓 Ruff：All checks passed!
全仓 Mypy：Success: no issues found in 44 source files
Docker Compose 配置校验：passed
```

阶段建议命令中的唯一 skip 是需要显式 `TEST_REDIS_URL` 的 ARQ 队列测试；在最终全量回归中
补齐该变量后已执行通过，因此最终全量结果没有 skip。

唯一未执行项是真实模型烟雾：当前环境没有 `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`。Worker 在缺少
这些配置时显式启动失败，不会降级为假成功；FakeLLM 与所有确定性安全门不受影响。
