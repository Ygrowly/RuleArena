# RuleArena 在线 Demo（技术预览）

> **在线控制台（无需安装，直接打开）**：https://ygrowly.github.io/RuleArena/
> 进来就能动手：选一张真实工单，逐步回放它在「裸跑 / 加门禁」下的每一次调用与最终账本。

**两条主线，同一条方法论**——模型负责提议，确定性程序负责裁决：

- **Agent 执行门禁（当前主线）**：同一份退款 Agent 代码跑两次——裸跑有 **6/15** 张工单重复退款、
  合计多退 ¥1920，加上运行时门禁后 **0/15**，且正常工单误拦 0。见
  [Agent 执行门禁](#agent-执行门禁)。
- **规则搜索（在线 Demo 的另一个方向）**：AI 搜索电商规则的异常操作组合，候选经真实 REST 重放，
  由确定性 Oracle 裁决，最小化后的反例可复现、可回归。

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

### golden-v4 结果（当前）

development 21 case（**14 漏洞** + 7 正常）跑四 Baseline；hidden 17 case（14 漏洞 + 3 正常）
只跑 Multi-strategy，**重复 3 次**。模型 deepseek-v4.1-flash，temperature 0，seed 20260831。

| Baseline | suite | reps | 发现率 | 95% CI | 误报 | 候选确认 | 稳定重放 | steps | tokens | 成本/case |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Random | dev | 1 | 0/14 | [0–22%] | 0/7 | 0/91 | — | 3.3 | 0 | $0 |
| BFS | dev | 1 | 2/14 | [4–40%] | 0/7 | 2/91 | 6/6 | 3.3 | 0 | $0 |
| Single Agent | dev | 1 | 4/14 | [12–55%] | 0/7 | 4/104 | 12/12 | 9.1 | 48,510 | $0.035 |
| Multi-strategy | dev | 1 | 5/14 | [16–61%] | 0/7 | 5/400 | 15/15 | 9.8 | 36,696 | $0.022 |
| Multi-strategy | hidden | 3 | **8/14**（pass@3） | [33–79%] | 0/3 | 14/944 | 42/42 | 9.5 | 37,662 | $0.022 |

诚实结论：

- **搜索不可复现，这是 v4 最重要的发现。** hidden Multi 跑了 3 次重复：`pass@3 = 8/14 (57%)`
  但 **`pass^3 = 1/14 (7%)`**。8 个被找到的 case 里只有 1 个是三次都找到的。"发现率 57%"
  和"稳定发现 7%"是两个完全不同的产品承诺，而单次运行的 v3 把这两者掩盖成一个数字。
- **Multi 5/14（36%）高于确定性 BFS 2/14（14%），但置信区间仍然重叠**
  （[16–61%] vs [4–40%]），按统计口径**仍不能宣称谁更强**。要分开它们需要更多 case
  或更多重复；这是下一步的量化依据，不是把阈值调低。
- 四 Baseline 全部 **0 次 INFRA_FAILED**（v3 的 Multi 有 4 次）。`UNPARSABLE_OUTPUT`
  现在是**具名终止原因**而非整条运行失败：dev Multi 出现 4 次、hidden Multi 7 次，
  模型偶发输出不可解析时记为该策略正常终止，不再污染发现率的分母。
- 其余机制层：误报 0、Ground Truth 泄漏 0、稳定重放 hidden 42/42、历史 P0 100%。
- 已确认违规全部**可归因**：每个 case 的环境只表现它自己声明的缺陷轴，所以一条路径触发的
  不变量就是该 case 标注的那条（v3 有 3 条已确认违规因此被计为未命中）。
- **发布门禁（`uv run rulearena benchmark verify --latest`）判定：拒绝**，9 项检查过 8 项，
  唯一未过的是 `hidden_discovery_at_least_75_percent`（8/14 = 57% < 75%）。与前几轮的本质
  区别在于：旧的 v2 配置下门禁**不可能通过**（发现率上界被动作空间裁到 60%），现在是真的
  没找够。

### golden-v3 结果（**环境口径已变更，不可与新版本混排**）

> `golden-v4` 把每个 Case 的环境从"整个 scenario 的缺陷集合"收窄为**它自己声明的缺陷轴**
> （见 [benchmarks/README.md](benchmarks/README.md) 的版本历史）。v3 的这一组数字是在旧
> 环境下测得的：同一 scenario 下所有漏洞 Case 共享一个能表现全部缺陷的环境，因此一条
> 搜索路径可以踩中 B Case 的缺陷而被记在 A Case 的标签下——v3 的 Multi 运行里就有 **3 条
> Oracle 已确认的违规被计为未命中**。下表保留为历史记录，**不与 golden-v4 的数字比较**；
> 门禁阈值（hidden 发现率 ≥ 75%）两版相同。

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
  环境，标签不约束路径能触发哪条不变量），**已在 golden-v4 修复**：每个 case 只表现它
  自己声明的缺陷轴。
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

## Agent 执行门禁

一句话：**同一份退款 Agent 跑两次，裸跑必然重复退款，加了运行时门禁一笔都不多。**

被测 Refund Agent 只通过 `ToolGateway` 调工具，按工单执行退款；工具超时时它**换一个
新的幂等键重试**——这是真实世界里最常见的错误来源，也是本项目要暴露的失败面本身，不是
模拟出来的。门禁（`packages/runtime_gate`）挂在网关之后，只做三件确定性算术，而且**顺序
是刻意的**：先按幂等键查回执（命中就直接用回执，不重放——一次已经存在的写入不是新的写入），
再按权威快照算写前预算，写完重读快照确认这笔动作真的发生了。它不做语义判断、不调用模型、
不决定终态——**违规判定只来自 Oracle**。

15 张工单（5 张正常、6 张回执丢失、4 张工单金额与实付不符），同一份代码、同一批工单，
`BARE` 与 `GATED` 各跑 **3 次重复**（45 次运行/组，真实 Sandbox HTTP + 真实回执/快照/事件）：

| 指标 | BARE（裸跑） | GATED（加门禁） |
| --- | --- | --- |
| 终态正确率 | 9/15 = **60%** [36–80%] | 15/15 = **100%** [80–100%] |
| 意外资损笔数 | **6** 笔（¥640） | **0** 笔（¥0） |
| 意外资损金额（跨 45 次运行合计） | **¥1920** | **¥0** |
| 自述成功但实际失败 | 0 | 0 |
| 必要转人工率 | 4/4 | 4/4 |
| 正常工单误拦 | —（无门禁） | **0** |
| 工具调用 / 门禁检查 | 120 / 0 | 90 / 207（4.6 次/工单） |
| 平均单工单耗时 | 5.78s | **5.48s** |
| INFRA_FAILED | 0 | 0 |

三点必须并列说清楚：

- **资损与任务完成是两件事。** 裸跑组丢的是钱（每张出问题的工单多退一次），不是"答错了"；
  门禁组两项都满分。这两个指标在报告里**从不合并**（`INV-C`）。
- **门禁组更省，不是更贵。** 它少发 30 次工具调用（超时后不再盲目重试），平均耗时反而
  低 0.3s——207 次只读检查（**先按幂等键查回执**、再算写前预算、超时再查一次、写后确认生效，
  外加比对前后快照）没有换来更慢的运行。顺序是刻意的：一次已经存在的写入不是新的写入，
  先问回执才不会把「正确的重试」当成新的退款拦掉。
- **回执丢失组必须同时声明 `REFUND_AGAINST_ORIGINAL`。** 忠实实现自己会拒绝第二笔超额
  退款，单靠"回执丢失"退不出第二笔钱，Oracle 也就没有可判定的事实。这是测量的必要条件，
  不是把两个缺陷缝在一起凑结论（见 [benchmarks/README.md](benchmarks/README.md)）。

复现：

```bash
uv run rulearena refund-bench --suite benchmarks/refund_agents/development-v1.json \
                              --modes bare,gated --repetitions 3
uv run rulearena refund-verify --latest      # 复核版本/seed/预算与三项门禁检查
uv run pytest tests/refund -q                # 门禁三段单测、Agent 决策树、INV-A/B 静态检查
uv run python scripts/export_refund_gate_demo.py   # 导出冻结对照演示
```

发布门禁 `refund-verify --latest`（三项检查并列必过）：`no_duplicate_refund_in_gated`
（加门禁组意外资损 = 0）、`no_false_block_on_normal`（正常工单误拦 = 0）、
`gated_not_worse_than_bare`（门禁组终态正确率 ≥ 裸跑组）。当前判定：**通过**。

## 边界与诚实声明

- 本项目**不是形式化证明**。搜索受预算约束，「预算内未发现违规」不等于「规则安全」。
- 最近一次真实模型评测是 **golden-v4**（见上表）：development 四 Baseline + hidden
  Multi-strategy 重复 3 次。Release Gate 判定为**拒绝**，唯一未过的是 hidden 发现率阈值
  （8/14 = 57% < 75%）；其余 8 项——版本/预算/seed 匹配、无 INFRA_FAILED、正常误报 0、
  稳定重放 42/42、历史 P0 100%、泄漏 0——全部通过。发布保持未通过状态，
  `benchmark verify --latest` 可复核。
- hidden 私有载荷与真实模型凭据属部署侧资产；公共仓库只有无答案 manifest，
  Runtime 无读取路径。
- 攻击面与信任边界见 [安全模型](docs/security-model.md)。

## 设计取舍

[架构决策记录](docs/architecture-decisions.md)：Simulator/Sandbox 分离、
确定性 Runtime、多策略隔离、Oracle 裁决、不用 LangGraph。

## 3 分钟演示

[演示脚本](docs/demo-script.md)。

### 在线站点（简历/审阅入口）

`https://ygrowly.github.io/RuleArena/` —— 由本仓库的 `site/` 与冻结运行记录构建，
内容全部可从 `benchmarks/` 与 `.cache/` 之外的原始运行记录复算：

| 页面 | 内容 |
| --- | --- |
它是**一个控制台，不是一个介绍页**：左侧常驻工作区，右上角标着数据源与录制时间，进来就能动手。

| 工作区 | 内容 |
| --- | --- |
| `工单处理`（默认） | 15 张真实工单的队列；选中一张逐步回放「裸跑 / 加门禁」两条路径的每一次调用、工具回执与门禁判定，右侧同步给出权威账本与 Oracle 裁决 |
| `对照评测` | 90 行逐次运行记录，可筛选可排序；上方是两组指标与发布门禁判定 |
| `门禁规则` | 三段检查各自读什么、怎么判、不确定时怎么办，以及 `runtime_gate` 的真实判定词表 |
| `系统架构` | 可拖动缩放的调用路径图（archify 渲染，主题已锁暗色以匹配控制台） |
| `缺陷台账` | 实测打出来的 4 个缺陷：现象、根因、修法、回归测试 |
| `概览` | 三十秒版本与口径边界，带「重播概览动画」 |

打开时先播一段概览动画（15 个格子逐一亮起，裸跑那行有 6 个变红），然后自动进入工单处理。
`demo.html` 是另一个方向的单文件搜索反例演示。

本地构建与预览：

```bash
pnpm --dir frontend run build                    # 产出 frontend/dist
uv run python scripts/build_standalone_demo.py   # 产出单文件搜索演示
uv run python scripts/build_site.py              # 组装 _site/（含已渲染的架构图）
python -m http.server -d _site 8000              # 打开 http://127.0.0.1:8000
```

架构图由 archify 从 `site/gate-diagram.json` 渲染并**随仓库提交**（渲染器不是本仓库的依赖，
所以提交的是产物本身）；改图时编辑那份 JSON 后重新渲染：
`node bin/archify.mjs validate architecture site/gate-diagram.json --quality showcase`。

推送 `main` 时 `.github/workflows/pages.yml` 会自动重建并发布；页面里引用的数字来自
`frontend/public/frozen/refund-gate-demo.json`，由 `scripts/export_refund_gate_demo.py`
从一次真实运行导出，构建脚本会把两者拼在一起——页面与实测数据**不可能各写一遍**。

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

它本身不需要构建、不需要后端，所以托管在哪都行：本仓库用 GitHub Pages 发布
（见上面的[在线站点](#在线站点简历审阅入口)）；也可以拖进 Netlify Drop、丢进任意静态托管或对象存储；
或者干脆把文件发给对方，双击打开——不联网也能看。

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
