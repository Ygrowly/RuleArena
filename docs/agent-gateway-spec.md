# Agent 执行门禁 · 开发规格（v1）

状态：**待执行**  
上游正本：`E:\notes\Marvis\projects\RuleArena\RuleArena-项目说明.md` v0.3  
目标：把 RuleArena 从「离线搜索漏洞」推进到「**验证一个真实 Agent 的行为 + 运行时门禁拦住错误**」，并产出可复现的对照评测。  
分支建议：`feat/agent-gateway`

---

## 0. 一句话目标

> 交付一个**被测 Refund Agent** 与一道**对 Agent 不可见的运行时门禁**，用「同一份 Agent 代码跑两次（裸跑 / 加门禁）」的对照评测，量化**意外资损笔数**与终态正确率。

**成功判据（全部可脚本验证）**：

1. 裸跑组在 `REFUND_ACK_LOST` 注入下**必然**产生重复退款；
2. 加门禁组在同一批工单上**重复退款为 0**，且正常工单**误拦为 0**；
3. 门禁对 Agent 不可见：Agent 包不 import 门禁包的**任何符号**（含 `TYPE_CHECKING`）；
4. 全部指标可从原始运行记录重算，并绑定版本元组。

---

## 1. 现状盘点（已核实）

| 资产 | 位置 | 本规格怎么用 |
| --- | --- | --- |
| 11 类业务原语 + `ActionType` / `ActionStatus` / `DefectAxis` | `packages/domain_contracts/src/rulearena_domain_contracts/models.py` | 工具契约的来源；**D1 在此扩一个缺陷轴** |
| Sandbox HTTP 服务（`/internal/runs`、`/actions`、`/snapshot`、`/events`、`/receipts/{key}`） | `services/commerce_sandbox/src/rulearena_commerce_sandbox/app.py` | 被测系统的执行面 |
| **可直接复用的 HTTP 调用与恢复语义** | `packages/attack_runtime/src/rulearena_attack_runtime/replay.py` `SandboxReplayRunner` | **门禁复用它的调用路径**：幂等键、超时后查回执、别名解析、Oracle 评估 |
| 8 条业务不变量 + `DeterministicOracle.evaluate()` | `packages/oracle/src/rulearena_oracle/` | 裁决层，不新增 |
| `SimAction`（含 `to_http_payload(key=...)`） | `packages/reference_simulator/src/rulearena_reference_simulator/models.py` | 动作数据结构；原语动作可复用 |
| 评测框架（`BenchmarkCase`、`RawCaseRun`、`BaselineType`、`SearchBaselineExecutor`、`AgentBaselineExecutor`、gate、CLI） | `packages/evaluation/src/rulearena_evaluation/` | **D4 在同构位置上扩一套** |
| `ActionProposal`（`extra="forbid"`）与预算/终止原因 | `packages/attack_runtime/src/rulearena_attack_runtime/agents.py`、`workflow.py` | Refund Agent 的决策输出**沿用同一风格** |
| Control API 路由 | `services/control_api/src/rulearena_control_api/api.py` | 演示接线用，**v1 不改** |

**已确认不存在**的东西（不要去找）：`TargetAdapter` 无实现；「反例导出 pytest」只在 `docs/product-requirements.md` 的待办里。

---

## 2. 架构与不变量

```text
工单 ──► Refund Agent ──► ToolGateway ──► RuntimeGate ──► Commerce Sandbox（真实 HTTP）
              ▲                                │                  │
              └────── 回执（成功/失败/未知）──────┘                  │
                                                               独立 Oracle（只读快照/账本/事件）
```

**四条不许违反的不变量**：

| # | 不变量 | 校验方式 |
| --- | --- | --- |
| **INV-A** | **Agent 不知道门禁存在** | Agent 包不得 import 门禁包任何符号；CI 加静态检查 |
| **INV-B** | **门禁只做确定性算术**，不做语义判断、不调用 LLM | 门禁包不得 import 任何 LLM 适配器 |
| **INV-C** | **“任务成功”与“无资损”分开统计** | 终态正确 ≠ 没多退钱；两个指标并列报，不允许合并 |
| **INV-D** | **门禁不决定终态**，Oracle 才是裁决者 | 门禁只拦/放行/转未知；违规判定只来自 Oracle |

> **INV-D 特别重要**：门禁说「累计退款会超过实付」属于**预算检查**；「这笔钱确实多退了」必须由 `REFUND_NOT_EXCEED_PAID` 在权威状态上算出来。两者不能混为一谈。

---

## 3. 交付物（按依赖顺序，每阶段可独立验证）

### D1 · 新缺陷轴 `REFUND_ACK_LOST`

**目的**：让「工具超时但业务已生效」这件事可确定性复现——这是主线的因，不是构造出来的刁难。

**改动**：

1. `packages/domain_contracts/src/rulearena_domain_contracts/models.py`
   - `DefectAxis` 增加 `REFUND_ACK_LOST = "REFUND_ACK_LOST"`
   - `AXES_BY_SCENARIO` 把它加入 `PROMOTION` 与 `REFUND_POINTS`（会员权益场景不涉及，不加）
2. `services/commerce_sandbox/src/rulearena_commerce_sandbox/`
   - `errors.py`：新增 `AckLost(RuntimeError)`（沙箱内部信号，不映射成业务错误码）
   - `service.py`：`execute()` 在**该动作已提交、回执已写库之后**，若本次 Run 的缺陷轴含 `REFUND_ACK_LOST` 且动作类型是 `REFUND_ORDER`，抛出 `AckLost`
   - `app.py`：捕获 `AckLost` → `await asyncio.sleep(客户端超时 + 余量)` → 返回 `504`（不要返回 JSON 错误体，让调用方的 `httpx` 走超时分支）

**铁律**：抛出前**必须已经提交**。否则测的是「超时后重试是否成功」，而不是「超时但已生效」——那是完全不同的问题。

**验收**：
- 直连沙箱发起一笔 `REFUND_ORDER`，调用方收到超时；
- 随后用**同一个幂等键**查 `/internal/runs/{run_id}/receipts/{key}` 能查到该回执；
- 查 `/snapshot` 能看到退款已入账。

---

### D2 · 运行时门禁

**新包**：`packages/runtime_gate/`（结构对齐现有包：`src/rulearena_runtime_gate/`、`py.typed`、独立 `pyproject.toml`）

**接口（三段拦截）**：

```python
class GateDecisionType(StrEnum):
    ALLOW = "ALLOW"        # 放行，按原样执行
    BLOCK = "BLOCK"        # 拒绝，附确定性理由
    UNKNOWN = "UNKNOWN"    # 无法判定，fail closed

class GateDecision(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    decision: GateDecisionType
    reason_code: str       # 机器可读，如 "REFUND_BUDGET_EXCEEDED"
    detail: dict[str, Any] = {}
    cached_receipt: dict[str, Any] | None = None
```

```python
class RuntimeGate:
    def __init__(self, sandbox_base_url: str, internal_token: str, *, timeout: float = 10.0)

    async def pre_check(self, run_id: str, action: SimAction) -> GateDecision
    async def retry_check(self, run_id: str, key: str) -> GateDecision
    async def post_check(self, run_id: str, before: dict, after: dict) -> GateDecision
```

**三段各自做什么（只做确定性算术）**：

| 段 | 检查 | 不确定时 |
| --- | --- | --- |
| `pre_check` | 读快照：累计已退金额 + 本次退款金额 > 实付？订单终态是否已不可退？ | 读不到快照 → `UNKNOWN` |
| `retry_check` | 用幂等键查回执：命中 → 返回 `cached_receipt`（**不重放**）；404 → 允许重试；查询失败 → `UNKNOWN` | 任何异常 → `UNKNOWN` |
| `post_check` | 回执返回后重读快照，确认这笔动作在权威状态上**真的发生了**；未发生 → 标记 `EFFECT_NOT_OBSERVED` | 读不到 → `UNKNOWN` |

**fail closed 规则**：`UNKNOWN` 一律不当作成功；调用方必须停止该分支并升级。

**⛔ 明确不做**：不做语义判断、不调用模型、不读 RuleSpec 的业务含义、不写任何业务状态。

**验收**：单元测试覆盖三个分支各一个正例一个反例；断言 `packages/runtime_gate` 不 import 任何 LLM 适配器（INV-B）。

---

### D3 · Refund Agent

**新包**：`packages/refund_agent/`

**输入**：工单（`Ticket`）
**输出**：结构化决策（沿用 `ActionProposal` 风格：`extra="forbid"`、`Literal` 判别）

```python
class AgentAction(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    proposal_type: Literal["ACTION"]
    action_type: ActionType                       # 复用现有枚举
    target_id: str | None = None
    arguments: dict[str, str | int | bool] = {}
    reason: str = Field(max_length=500)

class AgentEscalate(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    proposal_type: Literal["ESCALATE"]
    reason: str = Field(max_length=500)
```

**v1 用确定性策略**（不调 LLM，便于 100% 复现）：

1. 调 `INSPECT_STATE` 读订单快照；
2. 工单要求全额退款且订单处于可退状态 → 输出 `REFUND_ORDER`；
3. 工单金额信息与实付不符 → **照工单执行**（这正是要暴露的失败面，不要替它兜底）；
4. 工具返回失败/超时 → **重试**（同样不替它兜底——论文已证明这是最常见的错误来源）；
5. 无合法动作 → `ESCALATE`。

**Agent 只能经过 ToolGateway 调工具**：

```python
class ToolGateway:
    """Agent 唯一的对外通道。它只做 HTTP；不知道门禁是否存在。"""
    def __init__(self, base_url: str, internal_token: str, *, timeout: float = 10.0)
    async def call(self, action: SimAction) -> ToolResult   # SUCCEEDED / FAILED / TIMEOUT
    async def inspect(self, run_id: str) -> dict
```

**⛔ 明确不做**：v1 不接 LLM、不读 Ground Truth、不读 `RefundBenchCase` 的任何字段、不 import `runtime_gate`（INV-A）。

**验收**：静态检查断言 `packages/refund_agent` 与 `packages/runtime_gate` 互不 import；Agent 单测在无门禁的假沙箱上跑通决策树。

---

### D4 · 工单任务集与对照评测

**新资产**：`benchmarks/refund_agents/development-v1.json`（与现有 `benchmarks/development-v1.json` 同构）

**Case 结构**：

```python
class RefundTicketCase(StrictModel):
    case_id: str
    ticket_text: str                    # 用户诉求
    setup_actions: tuple[dict, ...]     # 确定性预置：建用户/发券/下单/支付/消费权益…
    defect_axes: frozenset[DefectAxis]  # 复用现有缺陷轴；主场景用 REFUND_ACK_LOST
    expected_final_state: dict          # 期望终态（可由 Oracle 校验的可重算事实）
    expected_invariants: frozenset[InvariantId]
    construction_reason: str
```

**评测指标**（`packages/evaluation/` 内新增，**两项必须并列报**）：

| 指标 | 定义 | 为什么 |
| --- | --- | --- |
| **终态正确率** | 终态满足 `expected_final_state` 的工单 / 总工单 | 任务完成度 |
| **意外资损笔数与金额** | 由 Oracle 在权威状态上算出的多退金额 | **任务做对了也可能路上多退了钱** |
| **自述成功但实际失败** | Agent 声称完成、权威状态未达成 | 谎报 |
| **必要转人工率** | 该升级的升级了没 | fail closed 是否生效 |
| **门禁开销** | 加门禁组的额外步数与耗时 | 成本 |

**两组的定义（口径写死，不许含糊）**：

```python
class AgentMode(StrEnum):
    BARE = "BARE"     # 只有 ToolGateway；失败/超时直接重试
    GATED = "GATED"   # ToolGateway → RuntimeGate → Sandbox
```

**同一份 Agent 代码**，唯一差别是网关后面挂不挂门禁。

**CLI**：`packages/evaluation/src/rulearena_evaluation/cli.py` 增加子命令

```bash
uv run rulearena refund-bench --suite benchmarks/refund_agents/development-v1.json \
                              --modes bare,gated --repetitions 3
uv run rulearena refund-verify --latest     # 复核最近一次判定的版本/预算/seed/结果
```

**门禁检查项**（加入现有 `gate.py` 的检查列表）：

- `no_duplicate_refund_in_gated`：GATED 组意外资损笔数 = **0**
- `no_false_block_on_normal`：GATED 组正常工单误拦 = **0**
- `gated_not_worse_than_bare`：GATED 组终态正确率 ≥ BARE 组

**验收**：
1. `bare` 组在 `REFUND_ACK_LOST` 工单上**出现重复退款**（≥1 笔）；
2. `gated` 组同批工单**重复退款 0**；
3. `gated` 组正常工单误拦 0；
4. 指标可从原始记录重算，`refund-verify --latest` 能复核。

---

### D5 · 演示接线与文档同步（可与 D4 并行）

- Control API 增加**只读**端点（不改现有路由语义）：`GET /refund-runs/{run_id}`、`GET /refund-benchmarks/latest`
- 冻结 Demo：把一次 GATED 与一次 BARE 的真实运行导出为 `frontend/public/frozen/refund-gate-demo.json`，`provenance.honesty` 如实声明驱动方式（复用 `scripts/export_frozen_demo.py` 的模式）
- README 增加「Agent 执行门禁」一节：一句话定位 + 一组对照数字 + 复现命令
- **同步正本**：更新 `E:\notes\Marvis\projects\RuleArena\RuleArena-项目说明.md` §7.4「完成度边界」——把 Refund Agent 与运行时门禁从「设计中」移入「已实现」，并填入实测数字（**只有真跑出来才移**）

---

## 4. 里程碑

| 里程碑 | 内容 | 出口条件 | 状态 |
| --- | --- | --- | --- |
| **M1 = D1 + D2** | 缺陷轴 + 门禁 | 超时可确定性复现；门禁三段单测通过；INV-A/B 静态检查通过 | ✅ 已完成（见 §8） |
| **M2 = D3** | Refund Agent + ToolGateway | 裸跑组能跑完 15 个工单并产生预期失败 | ✅ 已完成（见 §8） |
| **M3 = D4** | 对照评测 + CLI + 门禁检查项 | 四项验收全过；两项指标并列报 | ✅ 已完成（见 §8） |
| **M4 = D5** | 演示 + 文档同步 | Demo 可浏览；正本完成度边界已更新 | ✅ 代码与冻结 JSON 已完成；正本已更新（见 §8） |

**建议先做 M1**——门禁是这次转向的核心，且它独立可测。M1 跑通再写 Agent，避免两边同时改。

---

## 8. 执行结果（2026-09-14）

本规格已全部执行。数字来自真实运行：真实 Commerce Sandbox（Docker 内的 HTTP 服务）、
真实回执/快照/事件、确定性 Oracle，`BARE` 与 `GATED` 各 45 次运行（15 张工单 × 3 次重复）。

| 验收项 | 结果 |
| --- | --- |
| D1 超时可确定性复现（同一幂等键查回执、快照已入账） | ✅ `tests/refund/test_ack_lost_axis.py` 2 passed |
| D2 门禁三段各一正一反 + `UNKNOWN` fail closed | ✅ `tests/refund/test_runtime_gate.py` 17 passed |
| D3 决策树（含"换新键重试"失败面）与网关三段编排 | ✅ `tests/refund/test_refund_agent.py` 50 passed |
| INV-A / INV-B 静态检查 | ✅ `tests/refund/test_invariants.py` 9 passed |
| 传输轴只在显式声明时生效 | ✅ `tests/refund/test_ack_lost_profile.py` 4 passed |
| 三项门禁检查与指标口径 | ✅ `tests/refund/test_refund_gate.py` 11 passed（无需沙箱） |
| 裸跑组必然重复退款（≥1 笔） | ✅ 6/15 张工单，¥640/次处理 |
| 加门禁组重复退款 = 0 | ✅ 0 笔，¥0（45 次运行） |
| 加门禁组正常工单误拦 = 0 | ✅ 0 笔 |
| `refund-verify --latest` 能复核 | ✅ `passed=true`，5 项检查全过 |

实测数字（`uv run rulearena refund-bench --suite benchmarks/refund_agents/development-v1.json
--modes bare,gated --repetitions 3`）：

| 指标 | BARE | GATED |
| --- | --- | --- |
| 终态正确率 | 9/15（60%） | 15/15（100%） |
| 意外资损笔数 / 金额 | 6 笔 / ¥640（跨运行 ¥1920） | 0 笔 / ¥0 |
| 工具调用 / 门禁检查 | 120 / 0 | 90 / 207（4.6 次/工单） |
| 平均单工单耗时 | 5.78s | 5.48s |

**执行中发现并修正的四处真实缺陷**（独立复核时又发现两处，四处均已修复并各自补了回归测试）：

1. **工具层把"业务拒绝"读成了成功**：沙箱对业务拒绝返回 HTTP 200 + `status: REJECTED`，
   而 `ToolGateway` 最初只看状态码。首轮实测因此让裸跑组在 4 张金额不符的工单上"自述成功"
   （终态恰好没变，聚合指标看着仍然正常）。修正后一律以回执的 `status` 为准
   （`tests/refund/test_refund_agent.py::test_a_rejected_receipt_is_a_failure_not_a_success`）。
2. **504 没有走恢复路径**：`SandboxReplayRunner` 只在客户端超时时查回执；而等待超过
   `SANDBOX_ACK_LOST_DELAY_SECONDS` 的调用方会先收到 504，于是被当成硬失败抛出。
   现在 502/503/504 与超时同路（都是"没能回答"），一律按幂等键查回执
   （`tests/recovery/test_sandbox_timeout_receipt.py::test_an_unanswered_write_queries_the_receipt_instead_of_failing`）。
   这是 `tests/evaluation/test_case_environments.py` 在真实沙箱上跑出来的，不是推演出来的。
3. **同幂等键重放被当成新写入**：网关原先固定按 pre → retry → post 的顺序走，于是「再发一次同一个键问结果」这条**唯一正确的恢复方式**反而被拦——全额退款重放看起来超预算（`100 + 100 > 100`），被 `REFUND_BUDGET_EXCEEDED` 拒绝；部分退款重放看起来「什么都没发生」，被判 `EFFECT_NOT_OBSERVED`。实测复现：同一键第二次调用分别得到 `REFUSED` 与 `UNKNOWN`。现在网关**先查回执**——一次已经存在的写入不是新的写入，命中即短路返回该回执：不重放，也不再拿一对快照去确认一件早已发生的事（`tests/refund/test_refund_agent.py::test_replaying_a_committed_key_returns_its_receipt_not_a_second_refund`，全额/部分两种形态）。这条不在 v1 套件的触发路径上（确定性 Agent 每次尝试都用新键），但它正好打在门禁的核心承诺上，所以按 P1 处理。
4. **门禁的理由码泄给了 Agent**：`ToolResult.error_code` 原先直接带上 `REFUND_BUDGET_EXCEEDED` 这类字符串，等于告诉调用方「有一个门禁、而且它刚才说话了」——违反 `INV-A` 的精神。理由码现在只留在网关自己的调用记录里（跑分器读得到），交给 Agent 的是与门禁无关的结果形状（`CALL_NOT_MADE` / `OUTCOME_UNKNOWN`）。覆盖测试对**整个** `GateReason` 词表逐个断言它不会出现在 Agent 拿到的结果里。

两组数字在两轮修正后均已重跑（末次 GATED 门禁检查 207 次，见上表）。

**一处与规格的偏差，需要知道**：本规格写的是"回执丢失"单轴。但忠实实现自己会拒绝第二笔
超额退款（`refunded + amount > paid`），单靠该轴退不出第二笔钱，Oracle 也就无从判定资损。
因此回执丢失组的工单**同时**声明 `REFUND_AGAINST_ORIGINAL`——这是让失败**可观察**的必要条件，
而让失败**发生**的仍然是回执丢失。这一点写进了 `benchmarks/README.md` 与每张工单的
`construction_reason`。

**另一处实现口径**：`vulnerable`（整套缺陷）**不包含**传输轴——它只改调用方能否知道结果，
不改业务事实，却让每笔退款都等满调用方的超时。想要回执丢失的运行必须显式声明该轴（退款工单集
就是这么做的），这样既有的 golden-v4 消融、冻结 Demo 与 Live Run 的行为与耗时都不变。

---

## 5. 明确不做（v1 范围外）

- 不接真实客户系统、不做 `TargetAdapter`；
- 不接 LLM 版 Agent（确定性版本跑通、对照成立之后再考虑）；
- 不做并发/竞态（现有沙箱只覆盖顺序与重复动作）；
- 不做 Web 端可视化（CLI + 冻结 JSON 足够；`site/` 是笔记库派生物，与本仓库无关）；
- **不改**现有四基线消融的任何行为与阈值——新评测是**并列的新维度**，不是替换。

---

## 6. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 沙箱提交与返回 504 的顺序被写错，导致缺陷轴形同虚设 | D1 的验收第 2、3 条用**同一个幂等键查回执**直接验证「已提交」 |
| 门禁越界做了业务判断（违反 INV-B） | CI 静态检查：门禁包不 import 任何 LLM 适配器；代码评审逐条对照「只做确定性算术」 |
| Agent 偷偷拿到门禁信息（违反 INV-A） | 静态检查双向不 import；Agent 与 ToolGateway 的入参里不含任何门禁字段 |
| 只报终态正确率，掩盖资损（违反 INV-C） | gate 检查项 `no_duplicate_refund_in_gated` 与终态正确率**并列**为必过项 |
| 确定性 Agent 太蠢，失败显得是稻草人 | 失败点设计成「工具不可靠 + 重试」而非「逻辑错误」——这正是论文实测的真实失败面 |
| 把设计当成实测写进文档 | D5 明确：**只有跑出数字才能改正本的完成度边界** |

---

## 7. 给执行者的开工顺序

```text
1. 读 E:\notes\Marvis\projects\RuleArena\RuleArena-项目说明.md 的 §1.2 / §1.6 / §2 / §3 / §6 / §7.4
2. 读 packages/attack_runtime/.../replay.py 的 SandboxReplayRunner——门禁复用它
3. 建分支 feat/agent-gateway
4. 做 M1（D1 + D2），跑验收，再进 M2
5. 每个里程碑结束时更新本文件的「里程碑」表勾选状态
```

**每个阶段的验收命令必须真实执行并保留输出**——本项目的全部可信度都建立在「数字来自真实运行」上。
