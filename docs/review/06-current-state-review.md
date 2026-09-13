# 现状审查（2026-09-12）

范围：`feat/discovery-optimization` → `feat/golden-v3-budget` 两个分支上本次会话的全部改动，
以及这些改动之后的项目真实状态。
口径：只写实际执行过的命令与结果；推断与设计目标分别标注。按 P0/P1/P2 分级。

---

## 0. 一句话结论

**机制层的瓶颈已经清空，评测层的瓶颈已经定位，产品层（演示）已经可用。**

"清空"的最硬证据是：golden-v3 下两个 agent 基线的
`BUDGET_TIME` / `BUDGET_TOKENS_OR_COST` / `BUDGET_STEPS` **全部为 0**——没有任何预算维度在
绑定，策略是被 `CANDIDATE_FOUND`（Single 11、Multi 34）停下的；而修复前 100% 的策略都死于
token 预算耗尽、从未提交过候选。

LLM 策略的历史最高发现率是 0/9，现在是 Single **3/8**、Multi **2/9**，与确定性 BFS（2/9）
持平；但样本量仍不足以支持任何"谁更强"的结论——这本身是本次审查修正的六个错误陈述之一。

---

## 1. 已完成并已验证（MEASURED）

### 1.1 质量门禁（本次会话结束时的实测）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 后端单测 | `uv run pytest -q` | **116 passed / 32 skipped / 0 failed** |
| Lint | `uv run ruff check .` | All checks passed |
| 类型 | `uv run mypy .` | Success，98 个源文件 |
| 需数据库的测试 | `pytest -m postgres` | **5 passed / 1 skipped** |
| 前端单测 | `pnpm --dir frontend test` | 3 文件 / **10 passed** |
| 前端 lint / 类型 / 构建 | `pnpm --dir frontend run lint/typecheck/build` | 全部通过 |
| 迁移 | `alembic upgrade head` + `alembic check` | 0005、0006 已应用，**漂移检测无新增操作** |

### 1.2 搜索层：动作空间表达力（R1）

**问题**：Agent 的动作空间等于 `ReferenceSimulator.legal_actions()`，而后者按模拟器自身的
适用性模型裁剪，并把参数封在"规则当前允许"的范围内。实测后果：**9 个开发漏洞中有 6 个的
ground truth 根本无法被提出**，任何策略的发现率上界只有 5/9（hidden 3/5），而门禁要求 75%
——在旧配置下**结构上不可能通过**。

**修复**：合法性改由动作契约定义（场景支持该动作 + 引用实体存在），模拟器只**预测**后果；
参数化动作额外携带越过规则一单位的边界探针；防重守卫只拦"未生效"的重复（重复下单与
重复退款本身就是要找的缺陷）。

**证据**：`tests/evaluation/test_case_reachability.py` 在真实 Sandbox 上验证——每个
ground-truth 反例都可被提出，且在自身 profile 上确认、在相反 profile 上不确认。**上界 100%**。

### 1.3 预算：从自相矛盾到自洽（golden-v3）

**问题**：配置声明 12 步与 12000 tokens，而实测每步 p95 消耗 **2735 tokens**
（input+output），即 token 额度只够 `12000 / 2267 ≈ 5.3` 步。**这两个数字互相矛盾**，
任何策略都走不到声明的步数。v1 曾把原因归为"90 秒时间结构性截断"，但时间预算从未被耗尽
（`BUDGET_TIME = 0`）——**v1 的归因是错的，v2 那次实验本身证伪了它**。

**修复**：只改一个变量 `max_tokens` 12000 → 100000，按 `3 策略 × 12 步 × p95(2735)` 取整。

**证据**：v3 下 `BUDGET_TOKENS_OR_COST` 从 14/14 降到 **0**，步数从最多 5 升到 **9–10**。

### 1.4 诊断可观测（Phase 0）

每个策略的终止原因、用量、被拒提案分类、候选提交数现在**落库**（`RawRun.strategy_diagnostics`
+ `benchmark_case_run.strategy_diagnostics`，迁移 0005），并可聚合为
`strategy_termination_reasons` / `rejected_proposal_kinds`。

**证据**：这条改动直接产出了 1.3 的结论（`BUDGET_STEPS = 0`、`BUDGET_TOKENS_OR_COST` 占满）。

### 1.5 候选确认：由 Oracle 决定，而非模型猜的标签（F3）

**问题**：重放已算出全部不变量结论，但运行时只用**模型自己报的那一个**判定确认。

**证据（修复前）**：golden-v3 的 Multi 运行里 3 条路径被 Oracle 真正确认违规
（`candidate_confirmation_rate` 3/37、`replay_stability_rate` 9/9），却因为报的不是标注的
不变量，发现率被判为 **0/9**。

**修复后**：发现率 **2/9**（Multi）、**3/8**（Single）。

### 1.6 长跑不再丢数据

`BenchmarkRun` 此前只在整条基线跑完时落库，被中止就全丢（一次 3 小时运行丢了 9 个 case 的
诊断）。现在运行行先以 `RUNNING` 建好、每个 case 完成即落库，`--resume` 跳过已完成。
append-only 触发器只放行**一次**离开 `RUNNING` 的迁移，身份与配置列全程冻结。

**证据（真实端到端）**：强杀一次运行 → 库里留下 RUNNING 行 + 4 条 case；`--resume` 只执行
剩余 12 个，最终 16 条 case、发现率 2/9，与不中断的运行一致。

### 1.7 公开演示可点击（产品层）

`frontend/dist-standalone/rulearena-demo.html`（**263 KB**，双击即开，无 Docker/后端/模型）。

演示的冻结运行是**真实模型提出的路径**：4 步（创建用户 → 下单 200 → 支付 → 全额退款），
真实 HTTP 重放后的状态 Diff 显示**退款后积分从 200 变成 400**，Oracle 确认
`POINTS_VALUE_CONSERVATION`，独立重放 3/3，Fixed v2 上同一路径积分 200 → 0 不再违规。
页面包含 Oracle 裁决原文、Oracle 检查的全部 8 条不变量、每步真实状态 Diff、回执与事件、
两个 profile 并排对比、以及 8 次真实模型调用的 tokens/延迟/成本表。

前端展示层本次重做：结论前置、违规步红框标注、Oracle 全量清单、修复并排对比、技术 Trace 表。
手机端（390px）无横向溢出。

### 1.8 顺带修掉的三个真实缺陷

| 缺陷 | 性质 | 证据 |
| --- | --- | --- |
| `POST /api/runs` 的预算完全由客户端决定（文档却声称"Worker 强制硬上限"） | **公开部署的安全洞**：可烧掉模型额度 | 已加服务端钳制 + 测试；越界 422 |
| 测试用 `store.save()` 把合成运行永久写进基准库（append-only 删不掉） | 污染公开 API 的"最新基准" | 改为事务回滚；污染计数不再增长 |
| 演示导出被预算均分悄悄弄坏（8 步被三等分后每策略 2 步，装不下 5 步路径） | 无测试覆盖的连带伤害 | 演示预算按每策略步数给足 |

---

## 2. 当前 golden-v3 实测（development，真实模型 deepseek-v4.1-flash）

| Baseline | 发现率 | 95% CI | 误报 | 候选确认 | 稳定重放 | steps | tokens | 成本 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Random | 0/9 | [0–30%] | 0/7 | 0/70 | — | 3.4 | 0 | $0 |
| BFS | 2/9 | [6–55%] | 0/7 | 3/70 | 9/9 | 3.4 | 0 | $0 |
| Single Agent | 3/8 | [14–69%] | 0/7 | 3/11 | 9/9 | 9.2 | 45,632 | $0.46 |
| Multi-strategy | 2/9 | [6–55%] | 0/7 | 4/34 | 12/12 | 8.6 | 35,486 | $0.33 |

**正确读法**：LLM 已追上 BFS 的点估计，但三个区间重叠 → **当前样本量无法区分**。
要让 `0/n` 的上界低于 22% 需要 n ≥ 14 个漏洞 case（当前 9 个）。

---

## 3. 本次审查修正的错误陈述

这些是项目此前对外（README / 项目说明 / 简历口径）写过的结论，复核后被证据推翻：

| # | 原陈述 | 实际情况 |
| --- | --- | --- |
| 1 | "90 秒时间预算结构性截断 Agent" | 时间预算从未耗尽（`BUDGET_TIME = 0`）；真因是 token 记账 |
| 2 | "LLM 发现率 0–20% 没跑赢 BFS 20–22%" | 统计上不可区分（区间重叠）；且修复后 LLM 点估计已不低于 BFS |
| 3 | "Release Gate 如实拒绝" | v2 配置下门禁**不可能通过**（上界被动作空间裁到 60%）——不是"如实拒绝"，是靶场把答案剪掉了 |
| 4 | "Live Run 预算由 Worker 强制，非 UI 约束" | 服务端当时没有任何钳制，预算完全由请求体决定 |
| 5 | "Reference Simulator 与 Sandbox 共享同一份状态模型，用于快速搜索" | 模拟器是 Sandbox 语义的**严格子集**，曾把 6/9 的答案裁剪掉 |
| 6 | （审查过程中我自己的判断）"F3 不适用，无需记录全部不变量" | 该判断对当时探针的两条路径成立，但对 membership 三条不成立——它正是挡住 3 个确认反例的原因 |

**第 6 条值得单独说**：我在同一轮审查里先否定、后被数据推翻。记录在此，因为它说明"用一两个
样本证伪一个机制"是危险的，也说明保留可复核的原始数据比一次结论更重要。

---

## 4. 存在问题

### P0（阻断）

**无。** 本次会话开头存在的 P0（HEAD 因缺 import 完全跑不起来、动作空间裁剪、预算自相矛盾、
公开 API 无预算钳制）均已修复并有测试或实测证据。

### P1（应修复，影响结论可信度）

1. **靶场设计：case 标签不约束环境（R4）**
   多个 case 共用同一个缺陷环境（同一 `rule_spec` + 同一 `sandbox_version`，而 profile 是
   **一个布尔**、7 个缺陷开关全绑在一起）。后果：一条路径可能触发"别的" case 的缺陷。
   **证据**：另有 2 条 Oracle 已确认的违规未计入发现率（`membership-01/03` 的路径只违反
   `ENTITLEMENT_NON_NEGATIVE`，不含其标注的 `ENTITLEMENT_REFUND_CONSISTENCY`）。
   修法是让每个 case 拥有独立缺陷环境。

2. **hidden suite 未在 v3 下运行**
   Release Gate 目前**没有当前证据**：`verify` 需要一次匹配版本元组的 hidden Multi 运行。
   README 已如实标注，但这是"发布门禁"这条产品主张的空洞。

3. **统计效力不足**
   9 个漏洞 case 无法支撑任何策略间比较。这是 P1-1 的一部分（R4 同时扩用例数量到 ≥14）。

### P2（可延后）

1. **`max_time_seconds` 暂不需要校准（更正）**
   审查过程中一度以为时间成为新约束，依据是 F3 **之前**的一次运行出现过 `BUDGET_TIME = 1`
   与 p95 310s。但 F3 之后的实测显示：两个 agent 基线的
   **`BUDGET_TIME`、`BUDGET_TOKENS_OR_COST`、`BUDGET_STEPS` 全部为 0**，elapsed p95 分别是
   Single 254s、Multi 202s，都在 300s 上界之内。**没有任何预算维度在绑定**——策略是被
   `CANDIDATE_FOUND`（Single 11、Multi 34）停下的。因此时间校准降级为"监控项"而非待修项。
2. **Live Run 预算仍偏紧**：12 步三等分后每策略 4 步，90 秒三等分后每策略 30 秒。
   公开演示能否稳定走到提交候选，**未验证**。
3. **15 条合成测试运行永久留在基准历史里**（append-only 无法删除）。已被后续真实运行超越，
   当前不影响 `latest_completed()`，但无法清除。
4. **`docs/exec/*` 阶段性报告仍是 v2 数字**：它们是历史记录，未改写；README 与
   `benchmarks/README.md` 已承载当前口径。
5. **前端测试覆盖薄**：10 个单测；演示页的关键断言（结论文案、违规步标注、并排对比）
   没有测试覆盖。
6. **`rejected_proposal_kinds` 里 `DUPLICATE_ACTION` 曾出现 21 次**（Single，v3 前）：
   步数变长后模型在无效动作上打转。这是 R2（差分观测）要解决的直接证据，当前未修。

---

## 5. 未完成（TARGET / NOT VERIFIED）

### 5.1 已完成但**未在本次验证**

| 项 | 状态 |
| --- | --- |
| 完整栈（`docker compose up`：web + control-api + worker + sandbox） | **已验证**（2026-09-13）：6 个容器健康、nginx 前门 200、`/api/templates` 返回 3 个模板 |
| Live Run 端到端（浏览器发起一次真实运行） | **已验证**：compile 200 → confirm 200 → run 201 → 终态 `CONFIRMED_VIOLATION`，2 步 / 7.2k tokens，在 90 秒预算内 |
| hidden suite @ golden-v3 | **NOT VERIFIED**（未运行） |
| `benchmark verify --latest` @ v3 | **NOT VERIFIED**（依赖上一条） |
| 带账目修复的 agent 基线重跑 | **已完成**，但 MULTI 那次撞上提供方瞬时中断（连续 4 次 `ConnectError`），其分母被削到 7，因此 MULTI 仍引用 F3 那次的 2/9；SINGLE 的数字（3/8）来自这次运行 |

### 5.1a 补上验证时发现的问题（均已修复）

| 问题 | 影响 | 证据 |
| --- | --- | --- |
| `docker-compose.yml` 未传递模型网关必需配置 | **公开部署无法编译任何规则**（`model provider unavailable`） | 修复后同一模板从 provider 错误变成真实产出 4 条歧义问题 |
| 歧义确认协议不完整：UI 发空 body，服务端要求已解决的 RuleSpec | **公开 Live Run 有歧义时必 409**，且每次真实编译都会产生歧义 | 修复后在浏览器里走通 confirm → 200 |
| 编译干净时 UI 不冻结、拿不到 `version_id` | **启动按钮静默失效**（点下去什么都不发生） | 修复后日志出现 `POST /api/runs → 201` |
| nginx 启动时钉死后端 IP | 重建 `control-api` 后前门持续 502，直到 nginx 也重启 | 修复后重启后端仍返回 200 |
| `docker compose up --build` 在提示无 0006 | 镜像与仓库不同步时栈起不来（fail-closed 按设计工作，部署必须 `--build`） | 记为部署事实 |
| `uv sync` 构建期需联网 | 首次构建因瞬时网络失败，重试成功 | 记为部署事实 |

### 5.2 未开始（按价值排序）

| 项 | 内容 | 价值 |
| --- | --- | --- |
| **R2** | 差分观测闭环：每步把模拟器预测状态与靶场权威状态的 delta 回给策略（只给字段差异，不给不变量名） | 直接针对"模型看不到自己动作没生效、在无效动作上打转"（P2-5），是目前最大的搜索层提升点 |
| **R4** | 缺陷矩阵（每个 case 独立缺陷轴）+ 用例生成器（漏洞 case 从 9 扩到 ≥14） | 同时修 P1-1 与 P1-3；也是让发现率可判定的唯一途径 |
| **R3** | 探针归一化预算 + `ExhaustiveBounded` 确定性上界基线 | 让"LLM 搜索 vs 穷举"第一次可比；当前"BFS"其实只是一条 canonical 路径 |
| **R5** | `repetitions ≥ 3` + `pass^k`（代码支持，未跑过 k>1） | 与 τ-bench 的 pass^k 叙事对齐，目前没有任何一次运行有 k>1 |
| **R7** | 口径迁移收尾（`docs/exec/*`、项目说明、简历口径同步 v3） | 避免对外材料继续引用已作废数字 |

### 5.3 需要用户决定/授权

| 项 | 说明 |
| --- | --- |
| 托管成公开链接 | 单文件可直接上传 Netlify Drop / GitHub Pages；需要用户账号，我未代做 |
| 清除 15 条合成运行 | 需要临时停用 append-only 触发器，会削弱"已完成运行不可改写"的保证；不建议 |
| 平台目标：`max_time_seconds` 是否校准、hidden 是否重跑 | 各需一次预算与时间 |

---

## 6. 本次会话的改动清单（17 个提交）

```
2a1692a feat: make the search space expressible, persist diagnostics, add intervals
001d2cf feat: calibrate the token budget to the declared step budget (golden-v3)
edcb00c feat: persist each benchmark case as it finishes and allow resuming
3deeecb fix: let the Oracle decide which invariant broke, not the model's guess
4a96f70 test: stop writing permanent synthetic runs into the benchmark database
6020e77 feat: state the demo's conclusion up front and quote the Oracle's verdict
8e73411 feat: freeze a demo run whose path the model actually proposed
5e30271 feat: build the demo as one self-contained file
ff70d74 docs: document how to open the demo, and correct the previous commit's claim
454ec13 fix: enforce the public run budget cap on the server
d70f4f9 feat: turn the technical drill-down into a table a reader can scan
3c71501 docs: how to turn the single-file demo into a shareable link
b38e817 feat: show what the Oracle checked, not only what it found
74d8770 feat: show the fix instead of asserting it
52b951a fix: count replay attempts per invariant, so a confirmed run cannot look incoherent
684762b docs: replace the voided v2 table with the consistent golden-v3 results
```

---

## 7. 建议的下一步顺序

1. **等 agent 基线重跑完** → 用一致数字替换表里两处星号（SINGLE 分母 8 → 9、候选确认列口径统一）。
2. **补齐 NOT VERIFIED 项**：完整栈起一次、Live Run 跑一次、hidden @ v3 跑一次 → 门禁才有当前证据。
3. **R2**（差分观测）：直接针对 P2-5 与搜索层天花板，且不需要改靶场。
4. **R4**（缺陷矩阵 + 扩用例）：同时解决 P1-1 与 P1-3，让发现率第一次可判定。
5. **R5**（repetitions ≥ 3）与 **R7**（口径收尾）。
