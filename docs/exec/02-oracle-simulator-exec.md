# 阶段 2 执行 Spec：参考模拟器、Oracle、BFS 与重放

状态：已完成（2026-08-29）
前置条件：阶段 1 Review 通过  
阶段目标：建立与 Sandbox 实现独立的可执行规则模型、确定性判定器和候选确认链路，证明 RuleArena 不是“LLM 写风险报告”。

## 1. 给 Codex 的任务

只实现 `reference_simulator`、`oracle`、确定性搜索/重放/最小化服务。先阅读全部核心 Spec 与前两阶段 Review。不得实现 LLM、Rule Compiler、多 Agent、Benchmark 大盘或成品 Web。

## 2. 独立性硬约束

- Simulator 与 Sandbox 只共享 `policy_schema`、值对象和 Action/Receipt/Event/Snapshot 契约。
- 不得 import Sandbox 的 Repository、Service、ORM Model、状态转换函数或 Profile 分支。
- Oracle 不调用 Sandbox 的业务判定函数，也不读取 vulnerable/fixed 标记。
- Simulator 使用纯不可变/复制式 Python State；不访问数据库、网络或 Redis。
- Oracle 只依据冻结 RuleVersion、规范化状态、ActionReceipt 和 BusinessEvent 判定。

违反任一项视为 common-mode failure，阶段不通过。

## 3. Reference Simulator

实现三类 Scenario 的：

- 初始状态构造。
- `legal_actions(state)`。
- `transition(state, action) -> TransitionResult`。
- 规范化、稳定序列化和 `state_hash`。
- 无效动作显式返回，不静默修正 Agent 输入。
- RuleSpec 驱动的差异，禁止复制 Sandbox Profile 漏洞。

Simulator 的目的不是完整复刻电商系统，而是建立可搜索的参考业务世界。规则之外的未定义行为应返回 `UNSUPPORTED` 或要求确认，不能自行脑补。

## 4. Oracle 不变量

至少实现并以稳定 ID 标识以下八类：

1. `NET_PAID_NON_NEGATIVE`：用户净支付不得为负。
2. `REFUND_NOT_EXCEED_PAID`：累计退款不得超过允许基数。
3. `COUPON_SINGLE_CONSUMPTION`：同一优惠权益不得被重复消费。
4. `POINTS_VALUE_CONSERVATION`：积分发放与退款扣回符合冻结规则。
5. `ORDER_TERMINAL_MONOTONICITY`：终态订单不得非法回退。
6. `ENTITLEMENT_NON_NEGATIVE`：权益余额不得为负。
7. `ENTITLEMENT_REFUND_CONSISTENCY`：会员退款与权益有效性一致。
8. `IDEMPOTENT_EFFECT`：相同幂等键不得产生多次业务副作用。

Oracle 输出结构化 `OracleFinding`：invariant_id、status、evidence、before/after hash、相关 action/event ID、说明。状态只允许 `SATISFIED`、`VIOLATED`、`NOT_APPLICABLE`、`INSUFFICIENT_EVIDENCE`。

## 5. 确定性搜索与确认

实现两个 Baseline：

- seeded Random：给定种子与预算完全可重现。
- BFS：使用 `state_hash` 去重、深度/节点预算、非法动作剪枝。

候选确认流程：

1. 在 Simulator 找到违反不变量的动作路径。
2. 创建全新 Sandbox RunSpace。
3. 通过 HTTP 顺序执行动作，每步记录 Receipt/Event/Snapshot。
4. 由 Oracle 对真实结果重新判定。
5. 只有 API 重放仍违反同一 invariant 才生成 `CONFIRMED_VIOLATION`。
6. Simulator 违反但 Sandbox 不违反，标记 `MODEL_DIVERGENCE`，不得当作漏洞。

## 6. 反例最小化

使用删除式 Delta Debugging：每次从干净 RunSpace 重放，只有仍违反同一 invariant 才接受删除。输出当前删除空间中的 1-minimal 序列及最小化前后动作数，不宣称全局最短。

## 7. 测试要求

- 每个不变量至少有满足、违反、证据不足三类测试（不适用时说明）。
- Simulator 同输入、规则、种子得到同状态 Hash 和轨迹。
- BFS 能在有界状态空间内找到已知开发 Case，fixed Case 不误报。
- HTTP 重放 3 次得到同一 invariant、相同规范化终态和等价证据。
- 人为制造 Simulator/Sandbox 差异时，系统正确产出 `MODEL_DIVERGENCE`。
- 最小化后逐一删除任一剩余动作均不能保持同一违规。
- 架构测试阻止 Simulator/Oracle import Sandbox 实现模块。

建议命令：

```bash
uv run pytest -q tests/simulator tests/oracle
uv run pytest -q tests/replay tests/minimization
uv run pytest -q tests/architecture
uv run ruff check packages/reference_simulator packages/oracle
uv run mypy packages/reference_simulator packages/oracle
```

## 8. 阶段验收门

| 验收项 | 通过条件 |
| --- | --- |
| 独立模型 | 架构测试与人工检查均无 Sandbox 逻辑复用 |
| 确定性 | 固定版本/种子重复运行结果一致 |
| 判定性 | 漏洞由 Oracle 结构化结果而非文本决定 |
| 黑盒确认 | confirmed Case 全部通过真实 HTTP 重放 |
| 稳定重放 | 每个开发反例连续重放 3/3 成功 |
| 最小反例 | 输出经验证的 1-minimal 序列 |

## 9. 停止条件

如果不变量无法由现有 RuleSpec 无歧义计算、Sandbox Snapshot 缺少裁决字段、或为了复用不得不 import Sandbox 业务逻辑，停止并报告契约缺口。不得用 LLM Judge、字符串匹配或测试专用后门替代 Oracle。

## 10. 完成报告

附：八个 invariant 的形式化输入/输出表、Simulator/Sandbox 依赖审计、Random/BFS 固定种子结果、至少一条“候选 → HTTP 重放 → Oracle → 最小反例”完整证据链。
