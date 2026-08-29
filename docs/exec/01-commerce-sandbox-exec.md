# 阶段 1 执行 Spec：Commerce Sandbox

状态：待执行  
前置条件：阶段 0 Review 通过  
阶段目标：实现一个通过真实 HTTP、事务与数据库执行三类电商规则的最小业务世界，为后续搜索与 Oracle 提供可重置、可隔离、可重放的被测系统。

## 1. 给 Codex 的任务

只完成 Commerce Sandbox。先阅读核心规格、阶段 0 结论和本 Spec，检查现有实现与用户改动。不要实现 Oracle、Simulator、LLM、Attack Worker 或产品 UI；测试中的断言可以检查业务状态，但不得提前复制正式 Oracle。

## 2. 业务范围

支持三套内置 Scenario：

1. `PROMOTION`：创建订单、使用优惠券、支付、取消、退款。
2. `REFUND_POINTS`：支付后发放积分，全部/部分退款后按规则扣回。
3. `MEMBERSHIP_ENTITLEMENT`：开通会员、领取/核销次数权益、取消或退款会员。

每类 Scenario 均提供 `vulnerable` 和 `fixed` 实现配置。漏洞注入必须封装在 Sandbox 内部版本/Profile 中，不得出现在 Action/State 契约、API 响应或 Agent 可见字段中。

## 3. 必须实现

- Sandbox 领域表、BusinessEvent、ActionReceipt、RunSpace 和 ScenarioVersion。
- `POST /internal/runs`、reset、actions、snapshot、events、receipt 查询端点。
- 每个 RunSpace 拥有独立数据命名空间；所有查询强制带 run_id。
- 业务动作的显式状态机、前置条件、事务和领域错误。
- 每个写动作使用唯一 `idempotency_key`；重复键返回相同 Receipt，不重复副作用。
- Action 与事件账本同事务提交；失败动作不留半成品状态。
- 规范化 Snapshot：稳定排序、Decimal 字符串、排除时间戳和内部主键等噪声。
- reset 使用冻结 Scenario 初始快照重建干净状态。
- 内部令牌认证、请求大小限制、结构化日志和关联 ID。

## 4. 业务正确性约束

- Money 永远使用 Decimal，并明确舍入规则。
- 订单、优惠券、积分账户、会员、权益均有显式生命周期，非法迁移返回稳定错误码。
- 部分退款累计不得超过实付；积分扣回、权益恢复由 Profile 规则决定。
- 并发写入必须使用唯一约束、条件更新或行锁保证一致性。
- Receipt 必须区分 `SUCCEEDED`、`REJECTED`；网络超时由调用方查询 Receipt，不在服务端猜测。
- Event append-only；业务表可变，事件不可原地改写。

## 5. Vulnerable Profile

实现至少每类两个可复现缺陷，共不少于六个，例如：

- 优惠券取消后错误恢复并可重复使用；部分退款按原价退款导致净支出为负。
- 重复退款请求积分只扣一次或完全不扣；并发部分退款累计超额。
- 会员退款后已领取权益仍可使用；次数权益并发核销出现负数/重复消费。

具体 Ground Truth 仅存 `benchmarks`/部署配置和 Reviewer 可读说明中；公共运行 API 不返回缺陷名称或期望动作序列。

## 6. 推荐实施顺序

1. 为每类 Scenario 写固定 Given/When/Then 集成测试。
2. 建表、迁移、RunSpace 与 reset。
3. 先实现 fixed Profile 的动作状态机。
4. 加幂等、并发和事务测试。
5. 以明确 Profile 策略引入 vulnerable 行为，避免散落布尔判断。
6. 实现 Snapshot/Event/Receipt API，并以真实 HTTP 完成 E2E。

## 7. 必测矩阵

- 三类 Scenario 的正常生命周期。
- 所有非法状态迁移均拒绝且不改变状态。
- 同一 idempotency_key 顺序和并发提交多次，只产生一次副作用和一份权威 Receipt。
- 两个 RunSpace 使用相同业务 ID 互不污染。
- 动作中途异常时业务状态、Receipt、Event 一致回滚。
- reset 后 snapshot 与初始 snapshot 的规范化 Hash 一致。
- vulnerable Case 可触发，fixed Profile 相同序列不触发。
- 未授权请求、超大请求和未知动作被拒绝。

建议命令：

```bash
uv run pytest -q tests/sandbox
uv run pytest -q tests/integration/test_sandbox_http.py
uv run pytest -q tests/concurrency
uv run ruff check services/commerce_sandbox packages/domain_contracts
uv run mypy services/commerce_sandbox
```

## 8. 阶段验收门

| 验收项 | 通过条件 |
| --- | --- |
| 真实执行 | 测试通过 HTTP + PostgreSQL，不是函数级 Mock |
| 业务覆盖 | 三类 Scenario 均有 fixed/vulnerable Profile |
| 可重放 | reset + 同序列得到相同规范化终态 |
| 幂等并发 | 重复与并发测试无双写、超额和负值 |
| 隔离 | RunSpace 与数据库角色均无越界 |
| 信息边界 | API/契约/日志不泄漏 Ground Truth |

## 9. 停止条件

若阶段 0 契约必须破坏性修改、某业务语义仍有多种解释、真实 PostgreSQL 并发无法验证，先停止并请求决策。不得以全局内存字典、固定响应或跳过并发测试冒充 Sandbox 完成。

## 10. 完成报告

除根 README 要求外，附：动作 × 状态转换矩阵、每个 Profile 的版本标识、幂等/并发测试证据、一次完整 HTTP 请求—事件—快照示例。不得在面向 Agent 的示例中暴露 Ground Truth。
