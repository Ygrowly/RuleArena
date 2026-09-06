# RuleArena 在线 Demo（技术预览）

> 一句话：**AI 搜索电商规则的异常操作组合，真实 API 重放，确定性 Oracle 裁决。**

RuleArena 不是“智能审查产品方案”。它在给定规则下让三个隔离的策略 Agent
（价值流 / 生命周期 / 边界）在 Reference Simulator 中搜索可疑操作组合，
每个候选必须经过真实 Commerce Sandbox HTTP 重放，并由确定性 Oracle 对
快照/回执/事件裁决；只有 `CONFIRMED_VIOLATION` 才会成为反例。

## 30 秒看懂

1. 选一个业务模板（优惠券 / 退款与积分 / 会员权益），用中文改规则。
2. 确认歧义后冻结 RuleSpec（content hash 绑定，不可变）。
3. 三个策略独立搜索；候选经真实 Sandbox 重放 + Oracle 裁决。
4. 反例自动最小化（Delta Debugging），展示每步真实状态 Diff、回执与事件。
5. 切到 Fixed v2 重放：旧反例不再成立，正常路径仍通过。

## 为什么不是 Prompt

- Agent 只能输出结构化 `ActionProposal/StopProposal`（`extra=forbid`），
  没有工具通道，不能自封结论、不能写 outcome。
- 状态机、预算、取消、恢复全部由确定性代码控制；模型输出不可信。
- 结论唯一来源是重放后的 Oracle。详见
  [架构决策记录](docs/architecture-decisions.md)。

## 架构

```
浏览器 (React/Vite, nginx:8080)
   │  /api（Idempotency-Key、SSE、限流）
   ▼
Control API (FastAPI) ── PostgreSQL（权威状态）── Redis（ARQ 队列）
   │                                        │
   ▼                                        ▼
Reference Simulator ◄── Attack Worker（确定性状态机 + 三策略 Agent）
                                            │
                                            ▼
                          Commerce Sandbox（独立服务/私网）→ Oracle
```

## 快速启动（Docker 一条命令）

```bash
cp .env.example .env   # 修改全部示例密码与内部令牌
docker compose up -d --build
open http://127.0.0.1:8080
```

迁移由 `control-migrate` / `sandbox-migrate` 在服务启动前执行，失败阻断发布。
`/healthz` 只表示进程存活；`/readyz` 实际查询 PostgreSQL 与 Redis。

- 冻结黄金案例：无需模型即可浏览。数据来自真实持久化 Run
  （`frontend/public/frozen/golden-run.json`，由 `scripts/export_frozen_demo.py`
  通过真实 Sandbox HTTP 重放 + 确定性 Oracle 导出，动议由确定性脚本驱动并在
  `provenance.honesty` 中如实声明）。
- 实时运行：限额 Live Run（默认 12 步 / 12k tokens / $1.5 / 90s，IP 限流
  10 次 / 5 分钟）。LLM/Worker 不可用时显示真实失败状态；冻结案例始终可浏览。

## 评测

| Baseline | 漏洞发现率 | 正常误报 | 备注 |
| --- | --- | --- | --- |
| Random | 0/9 | 0/7 | development，seed 20260831，实测 |
| BFS | 2/9 | 0/7 | 同上，实测 |
| Single Agent | N/A (dev) · 0/5 (hidden) | 0/3 (hidden) | hidden 为 deepseek-v3.2 实测；dev 未测 |
| Multi-strategy | N/A (dev) · 0/5 (hidden) | 0/3 (hidden) | 同上 |

hidden suite Release Gate 判定：**拒绝**（hidden 发现率 0/5 < 75%，无已确认反例可谈 3/3）；
其余检查项（版本/预算/seed 匹配、正常误报 0、历史 P0 100%、泄漏 0、无 INFRA_FAILED）全部通过。
真实模型 Agent 在 90 秒预算内未提交任何候选即预算耗尽——这是当前主要的模型侧短板，详见
`docs/exec/04-eval-observability-report.md` 与本次审查报告。

复现：`uv run rulearena benchmark --suite development --baselines random,bfs`。
无数据的格子标 N/A，不填估计值。完整口径见 `docs/exec/04-eval-observability-report.md`。

## 边界与诚实声明

- 本项目**不是形式化证明**。搜索受预算约束，「预算内未发现违规」不等于「规则安全」。
- 真实模型（deepseek-v3.2）已完成 hidden suite 四 Baseline 实测：
  Release Gate 如实拒绝（发现率 0/5 < 75%）。发布保持未通过状态，
  `benchmark verify --latest` 可复核。
- hidden 私有载荷与真实模型凭据属部署侧资产；公共仓库只有无答案 manifest，
  Runtime 无读取路径。
- 攻击面与信任边界见 [安全模型](docs/security-model.md)。

## 设计取舍

[架构决策记录](docs/architecture-decisions.md)：Simulator/Sandbox 分离、
确定性 Runtime、多策略隔离、Oracle 裁决、不用 LangGraph。

## 3 分钟演示

[演示脚本](docs/demo-script.md)。

## 本地开发与质量检查

```bash
uv sync --all-groups
uv run ruff check .
uv run mypy .
uv run pytest -q          # 真实服务验收需 SANDBOX_HTTP_URL / TEST_*_DATABASE_URL / TEST_REDIS_URL
pnpm --dir frontend install
pnpm --dir frontend test                # vitest
pnpm --dir frontend test:e2e            # Playwright（复用系统 Chrome，E2E_BASE_URL 默认 8080）
pnpm --dir frontend run lint && pnpm --dir frontend run typecheck && pnpm --dir frontend run build
docker compose config
```

导出冻结黄金案例（需本地 Sandbox 运行）：

```bash
SANDBOX_HTTP_URL=http://127.0.0.1:8001 INTERNAL_SERVICE_TOKEN=<token>   uv run python scripts/export_frozen_demo.py
```

## 部署（Railway）

公开 Web/Control，Sandbox 仅私网并验证内部令牌；迁移失败阻断发布。
当前仓库未包含云资源授权，Railway 部署步骤与拓扑见
`docs/security-model.md` 的部署章节；未经明确授权不创建云资源。

## 数据库隔离验证

```bash
TEST_CONTROL_DATABASE_URL='postgresql+asyncpg://rulearena_control:<pwd>@localhost:15432/rulearena' TEST_SANDBOX_DATABASE_URL='postgresql+asyncpg://rulearena_sandbox:<pwd>@localhost:15432/rulearena'   uv run pytest -q tests/test_database_isolation.py
```

测试检查自身 Schema 的 `USAGE` 权限与对方 Schema 的拒绝访问；不能以 SQLite 或 mock 替代。

## 依赖边界

`control_api` 与 `commerce_sandbox` 只依赖共享包；共享包不反向导入服务。
`policy_schema` 定义规则与值对象，`domain_contracts` 定义动作、回执、事件、
快照和 API 错误，`observability` 提供统一配置与 JSON 日志，`evaluation`
只属于评测侧进程，`attack_runtime` 不依赖 `evaluation`。

## 环境要求

- Python 3.12、uv 0.11+
- Node.js 20+、pnpm 10.15+
- Docker Engine 与 Docker Compose
