# 阶段 0–2 独立 Review 结果

审查日期：2026-08-30  
审查范围：阶段 0 Foundation、阶段 1 Commerce Sandbox、阶段 2 Simulator / Oracle /
Replay / Minimization，以及当前工作树和真实 PostgreSQL/HTTP 运行结果。  
最终结论：`PASS`，允许进入阶段 3。

## 修复的 Findings

| 级别 | 阶段 | Finding | 处理结果 |
| --- | --- | --- | --- |
| P1 | 0 | Control Alembic 未提供 Metadata，`alembic check` 无法运行 | 增加仅限 `control` schema 的空 Metadata 与过滤器；两套 drift check 均通过 |
| P1 | 0 | CI 未启动 Sandbox，真实 HTTP、并发和重放测试会被 skip | Compose 增加服务 readiness healthcheck；CI 启动 Sandbox 并显式运行真实测试 |
| P1 | 0 | RuleSpec 允许零积分除数、跨场景规则和重复 rule type | 增加确定性跨字段校验及负例 |
| P1 | 1 | 请求大小限制只信任 `Content-Length`，chunked 请求可绕过 | 同时限制实际读取字节数，并增加 chunked 负例 |
| P1 | 1 | Sandbox 未阻止跨 Scenario 动作 | Sandbox 与 Simulator 均增加场景动作白名单，拒绝且不改变状态 |
| P1 | 1 | vulnerable 会员故障错误影响“不退款取消”路径 | 将故障限定到退款；普通取消始终撤销剩余权益 |
| P1 | 1 | `REFUND_POINTS` 只有一个独立故障，不满足每类至少两个 | 增加可复现的积分超额核销故障；fixed 路径保持拒绝 |
| P1 | 2 | Replay 将 `INSUFFICIENT_EVIDENCE` 合并为 `MODEL_DIVERGENCE` | 增加独立分类，只有 Oracle `VIOLATED` 可确认 |
| P1 | 2 | Simulator 对非法金额/枚举等会抛异常，未显式拒绝 | 非法输入稳定返回 `REJECTED/INVALID_ARGUMENT` |
| P1 | 2 | Simulator 部分退款不按比例扣回积分，与规则和 fixed Sandbox 不一致 | 增加累计按比例扣回与 `points_revoked` 状态 |
| P1 | 2 | Simulator 忽略部分 target/owner/currency 约束，会员退款语义与 fixed Sandbox 不一致 | 对齐资产关联、币种、目标实体和 `UNUSED_ONLY` 拒绝语义 |
| P1 | 2 | state_hash 排除了会改变未来行为的幂等记忆 | 纳入规范化幂等状态，避免错误去重 |
| P1 | 2 | Oracle 会员事件按全局顺序判断，可能把另一权益消费误报为退款后消费 | 按 membership / entitlement ID 关联事件 |
| P1 | 2 | Oracle 对 float、NaN、非法整数字段可能接受或抛异常 | 严格解析；证据异常统一返回 `INSUFFICIENT_EVIDENCE` |
| P2 | 2 | Random Search 在零预算时不检查初始状态 | 预算消耗前检查初始 goal |

修复后未发现剩余 P0/P1。所有 intentional vulnerable 行为均封装在 Sandbox Profile，
未进入共享契约、Snapshot、Event 或 Receipt 响应。

## 验收矩阵

| 维度 | 结果 | 证据 |
| --- | --- | --- |
| Foundation build/config | Pass | Docker clean build、Compose config、前后端锁文件构建通过 |
| Contract strictness | Pass | unknown/float/negative/enum/code-like/zero-divisor/cross-scenario 负例通过 |
| Database isolation | Pass | 两个真实 runtime role 的跨 schema USAGE/SELECT 均被拒绝 |
| Health/readiness | Pass | PostgreSQL + Redis 实探测；Compose healthcheck 覆盖两个服务 |
| Sandbox isolation/atomicity | Pass | 跨 Run、回滚、Receipt/Event/State 同事务测试通过 |
| Idempotency/concurrency | Pass | 同 key 八并发仅一个 Receipt/Event；不同行为由 Run 行锁串行化 |
| Reset/replay | Pass | reset 恢复初始规范化 Hash；当前 epoch 的 Event/Receipt 隔离 |
| Fault injection | Pass | Promotion 2、Refund Points 2、Membership 3 个独立故障可复现；fixed 对照不误报 |
| Ground Truth boundary | Pass | Profile/Ground Truth 不出现在公共契约和运行响应；Oracle 无 Profile 分支 |
| Simulator independence | Pass | AST import gate + 全文审计；不 import Sandbox Service/ORM/判定逻辑 |
| Eight invariants | Pass | 8 个稳定 ID 均覆盖 violated、satisfied/not-applicable、insufficient evidence |
| Search | Pass | BFS/Random 有 depth/node 预算、seed 可复现、Hash 去重语义正确 |
| Confirmation | Pass | 三类 vulnerable Case 均真实 HTTP 连续 3/3；fixed 同序列不确认 |
| Minimization | Pass | 每次新 RunSpace、同 invariant、逐一删除验证 1-minimal |

## 最终执行证据

```text
uv run pytest -q --maxfail=1
64 passed in 88.45s

uv run ruff check .
All checks passed!

uv run mypy services packages scripts
Success: no issues found in 32 source files

pnpm --dir frontend run lint / typecheck / test / build
全部退出 0；Vite production build 成功

docker compose config --quiet
退出 0

uv run alembic -c alembic.ini check
No new upgrade operations detected.

uv run alembic -c alembic-sandbox.ini check
No new upgrade operations detected.

git diff --check
退出 0（仅有 Windows CRLF 提示，无 whitespace error）
```

真实测试使用本地 PostgreSQL 16、Redis 和当前工作树启动的独立 Sandbox HTTP 服务；
未使用内存数据库或函数级 Mock 冒充集成验证。

## 残余风险

- 前端当前是阶段 0 空壳，Vitest 没有测试文件；阶段 0–2 没有产品 UI 行为，因此不阻断。
- 本机已有另一套开发容器占用默认端口，本次使用现有数据库/Redis和独立 `18001` Sandbox
  完成验证；这是本机环境冲突，不是仓库逻辑失败。
- 本次没有执行 GitHub-hosted CI 本身，但已在本地逐项执行其新增的数据库、HTTP、并发、
  重放和迁移漂移命令。
