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
  通过真实 Sandbox HTTP 重放 + 确定性 Oracle 导出；动作序列由真实模型提出、是否
  构成违规由 Oracle 判定，`provenance.honesty` 如实声明这一区别）。
- 实时运行：限额 Live Run（默认 12 步 / 100k tokens / $1.5 / 90s，IP 限流
  10 次 / 5 分钟）。预算由服务端钳制（越界 422），90 秒是单次成本的实际边界。
  LLM/Worker 不可用时显示真实失败状态；冻结案例始终可浏览。

## 评测

四 Baseline 实测（**golden-v3**，deepseek-v4.1-flash，development 16 = 9 漏洞 + 7 正常）：

| Baseline | dev 发现率 | 95% CI | dev 误报 | 候选确认 | 稳定重放 | steps | tokens | 成本 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Random | 0/9 | [0–30%] | 0/7 | 0/70 | — | 3.4 | 0 | $0 |
| BFS | 2/9 | [6–55%] | 0/7 | 3/70 | 9/9 | 3.4 | 0 | $0 |
| Single Agent | 3/8 | [14–69%] | 0/7 | 3/11 | 9/9 | 9.2 | 45,632 | $0.46 |
| Multi-strategy | 2/9 | [6–55%] | 0/7 | 4/34 | 12/12 | 8.6 | 35,486 | $0.33 |

诚实结论：

- **LLM 策略已追上确定性 BFS**（Single 3/8、Multi 2/9 对 BFS 2/9），但三者置信区间
  重叠，**按统计口径仍不能宣称谁更强**。要让 `0/n` 的上界低于 22% 需要 n ≥ 14 个漏洞
  case（当前 9 个）——这是下一步扩用例数量的依据，而不是把阈值调低。
- **Single Agent 的分母是 8 而不是 9**：`dev-membership-02` 被记为 `INFRA_FAILED`，
  原因是 `RawCaseRun` 的账目一致性校验（已确认数 ≤ 已重放数）在"一条路径可确认多个
  不变量"时失败——这个校验本身是对的，抓到的是计数口径不一致（agent 侧按路径计、
  搜索基线按路径 × 不变量计），已修，但该次运行的数字无法回填，故如实标注。
- 候选确认列：确定性基线按「路径 × 不变量」计；上表 agent 基线的运行早于口径统一，
  该列对它们按路径计，下次运行将一致。
- 机制层：稳定重放 9/9 与 12/12、误报 0/7、Ground Truth 泄漏 0、无未解释的失败。
- 一处如实降级：另有 2 条 Oracle 已确认的违规**未计入**发现率——它们的路径违反的不是
  该 case 标注的不变量。这不是运行时的缺陷，而是靶场设计问题（多个 case 共用同一缺陷
  环境，标签不约束路径能触发哪条不变量），已记为下一步要修。
- **hidden suite 已在 v3 下运行**（Multi-strategy，8 case = 5 漏洞 + 3 正常）：发现率
  **2/5（40%）**，95% CI **[11.8%, 76.9%]**；误报 0/3；候选确认 3/144；稳定重放 9/9；
  零 INFRA_FAILED；成本 mean $0.021/case。命中 `hidden-04`、`hidden-05`。
- **发布门禁（`uv run rulearena benchmark verify --latest`）判定：拒绝**，9 项检查过 8 项，
  唯一未过的是 `hidden_discovery_at_least_75_percent`。与前一轮的本质区别在于：旧配置下
  门禁**不可能通过**（发现率上界被动作空间裁到 60%），现在是搜索确实只找到 2/5。同时
  n=5 的置信区间宽到**包含 75%**——按统计口径，它既不能证明达标，也不能排除达标。

复现：`uv run rulearena benchmark --suite development --baselines random,bfs,single_agent,multi_strategy`。
无数据的格子标 N/A，不填估计值。

### golden-v2 结果（**已作废**，仅作历史记录）

> 复核实测发现：v2 的 `max_tokens=12000` 只够走 `12000 / 2267 ≈ 5.3` 步，而同一份配置
> 声明的是 12 步——**两个数字本身互相矛盾**，任何策略都走不到声明的步数。`golden-v3`
> 只改这一个变量（12000 → 100000，按实测每步 p95 2735 tokens 校准），Case 内容、
> 期望答案与门禁阈值不变。同时修正了动作空间表达力：原先 9 个开发漏洞里有 6 个的
> ground truth 在 Agent 的动作模型里**根本无法被提出**，发现率上界只有 5/9（hidden
> 3/5）。依据见 `benchmarks/README.md` 版本历史。v2 数字不可与 v3 混排。

| Baseline | dev 发现率 | hidden 发现率 | dev 误报 | hidden 误报 | 候选确认 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| Random | 0/9 | 0/5 | 0/7 | 0/3 | 0/70 (dev) | 确定性 |
| BFS | 2/9 | 1/5 | 0/7 | 0/3 | 3/70 (dev) | 确定性，稳定 9/9 |
| Single Agent | 0/9 | 0/5 | 0/7 | 0/3 | 0 候选 | 预算内未提交 |
| Multi-strategy | 0/9 | 1/5 | 0/7 | 0/3 | 0/1 (dev) · 1/1 (hidden) | hidden 稳定 3/3 |

**统计显著性更正（对 v2 结论的修正）**：v2 的表曾被用来支持"LLM 发现率没跑赢 BFS"，
但以 9 个 case 的样本量，这两个数字在统计上**不可区分**——dev 上 LLM 0/9 的 95% Wilson
区间是 **[0%, 29.9%]**，BFS 2/9 是 **[6.3%, 54.7%]**，区间完全重叠。正确表述是"当前
样本量无法区分两者"，而不是"BFS 更强"；按后者做的 Multi-strategy 降级说明，依据是
站不住的。比率指标现在都自带 Wilson 区间。

v2 的 Release Gate 判定为**拒绝**（hidden 发现率 0/5 < 75%），且在 v2 配置下**不可能
通过**——发现率上界被动作空间裁剪到 60%。

## 边界与诚实声明

- 本项目**不是形式化证明**。搜索受预算约束，「预算内未发现违规」不等于「规则安全」。
- 最近一次真实模型评测是 **golden-v3 的 development 四 Baseline 与 hidden Multi-strategy**
  （见上表）。Release Gate 判定为**拒绝**，唯一未过的是 hidden 发现率阈值（2/5 < 75%）；
  其余 8 项——版本/预算/seed 匹配、无 INFRA_FAILED、正常误报 0、稳定重放 3/3、历史 P0 100%、
  泄漏 0——全部通过。发布保持未通过状态，`benchmark verify --latest` 可复核。
- hidden 私有载荷与真实模型凭据属部署侧资产；公共仓库只有无答案 manifest，
  Runtime 无读取路径。
- 攻击面与信任边界见 [安全模型](docs/security-model.md)。

## 设计取舍

[架构决策记录](docs/architecture-decisions.md)：Simulator/Sandbox 分离、
确定性 Runtime、多策略隔离、Oracle 裁决、不用 LangGraph。

## 3 分钟演示

[演示脚本](docs/demo-script.md)。

### 直接看：单文件演示（无需任何依赖）

`frontend/dist-standalone/rulearena-demo.html` —— **双击即可在浏览器打开，不需要
Docker、不需要后端、不需要模型**。

它是一份真实完成运行的快照：动作序列由真实模型提出，经真实 Commerce Sandbox HTTP
重放，快照/回执/领域事件全部来自真实服务，是否构成违规由确定性 Oracle 判定，反例
经 Delta Debugging 压到 4 步，并在独立干净数据空间中重放 3/3 稳定；同一路径切到
Fixed v2 后不再成立。页面顶部 `provenance.honesty` 如实写明路径由谁提出、裁决由谁做出。

该文件由脚本生成并**随仓库提交**（它是交付物，不是普通构建产物）：

```bash
pnpm --dir frontend run build && uv run python scripts/build_standalone_demo.py
```

前者构建前端，后者把构建产物与冻结运行快照内联成单文件（同时输出一个不含文档外壳的
fragment 版本，供自带 `<body>` 的托管方使用）。

**怎么把它变成一条可分享的链接**（单文件无需构建、无需后端，所以任选其一）：

- **Netlify Drop**：打开 `app.netlify.com/drop`，把 `rulearena-demo.html` 拖进去，立刻得到 URL。
- **GitHub Pages**：把文件放进仓库，Settings → Pages 指向该目录（或改名为 `index.html`）。
- **任意静态托管 / 对象存储**：直接上传该 HTML，公开读即可。
- **零托管**：把文件本身发给对方，双击打开——不联网也能看。

### 完整栈（含限额 Live Run）

`docker compose up -d --build` → `http://127.0.0.1:8080`。除冻结案例外还提供限额
实时运行（需要模型配置）。

## 本地开发与质量检查

```bash
uv sync --all-groups
uv run ruff check .
uv run mypy .
uv run pytest -q          # 真实服务验收需 SANDBOX_HTTP_URL / TEST_*_DATABASE_URL / TEST_REDIS_URL
pnpm --dir frontend install
pnpm --dir frontend test                # vitest
pnpm --dir frontend test:e2e            # Playwright（复用系统 Chrome，E2E_BASE_URL 默认 8080）
                                        # 加 E2E_LIVE_MODEL=1 可跑「真实后端 + 真实模型」的实时运行端到端
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
