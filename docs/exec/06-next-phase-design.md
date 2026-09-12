# 下一阶段设计：度量完整性 → 搜索能力 → 收尾

前置：`docs/review/06-current-state-review.md`（现状与问题清单）。
本文只给设计，不含实现。每项按 问题 / 目标 / 方案 / 关键取舍 / 验收 / 依赖 / 成本 展开。

排序原则：**先修度量，再优化被度量的东西**。当前 9 个漏洞 case 无法区分任何策略
（置信区间全部重叠），在这样一个度量上优化搜索，得到的数字无法解释——这正是 R1 与
golden-v3 那一轮教给我们的：当时"LLM 没跑赢 BFS"的结论，根因是靶场剪掉了答案。

---

## Phase A：补齐验证（无新功能，成本近零）

### A1. 完整栈与 Live Run 端到端

**问题**：本次只单独起过 postgres/redis/sandbox，`docker compose up` 全栈与浏览器发起的
Live Run 从未验证；Live Run 的预算刚被改过（12k → 100k tokens），改动本身未经验证。

**方案**：一次脚本化冒烟，产出可复现的输出：
1. `docker compose up -d --build` → 等 `/readyz` 通过（不是 `/healthz`）。
2. `curl /api/templates` 返回 3 个模板。
3. 浏览器（Playwright，复用系统 Chrome）走一遍：选模板 → 编译 → 确认歧义 → 启动 →
   等到终态；断言页面显示的是**真实终态**（`CONFIRMED_VIOLATION` 或
   `NO_VIOLATION_WITHIN_BUDGET`），而不是"运行中"。
4. 记录一次真实运行的 `strategy_diagnostics`，确认预算不再截断（`BUDGET_TOKENS_OR_COST == 0`）。

**关键取舍**：Live Run 的 90 秒上界不改（它是公开演示的成本护栏）。因此每策略只有 30 秒，
**可能仍不足以提交候选**。验收时把这一点如实记录：若 90 秒内确实无法提交，就把
"公开 Live Run 只能演示机制、不能演示发现"写进 README，而不是偷偷放宽时间。

**验收**：上述 4 步的真实输出（含失败状态也要如实呈现）。
**成本**：约 15 分钟，无模型费用以外开销（一次 Live Run ≈ $0.01）。

### A2. hidden suite @ golden-v3 + `benchmark verify`

**问题**：Release Gate 目前没有当前证据——`verify` 需要一次匹配版本元组的 hidden
Multi 运行。

**方案**：用本地 `.env.hidden-suite.json`（部署侧资产）跑
`--suite hidden --baselines multi_strategy`，然后 `benchmark verify --latest`。

**关键取舍**：hidden 结果**公开报告只出聚合**（发现率/误报），不披露 per-case 答案——
现有 `public_metric_summary` 已保证这一点，报告沿用。

**验收**：`verify` 给出**通过或拒绝**（二者都是有效结论）；结果如实写进 README 的
评测区与门禁段。
**依赖**：无。**成本**：8 case × ~3 分钟 ≈ 30 分钟，约 $0.3。

---

## Phase B：度量完整性

### B1. 缺陷矩阵靶场（R4，修 P1-1）

**问题**：同一 scenario 下所有 case 共用同一环境（同一 `rule_spec` + 同一
`sandbox_version`），而 `SandboxProfile` 是**一个布尔**、7 个缺陷开关全绑在一起。后果是
case 标签不约束环境：`membership-01/03` 的路径违反了 `ENTITLEMENT_NON_NEGATIVE`，而它们
标注的是 `ENTITLEMENT_REFUND_CONSISTENCY`——2 条 Oracle 已确认的违规因此无法计入发现率。
hidden 与 dev 又共用同一批 RuleSpec，等于没有泛化测试。

**目标**：让每个 case 的环境**只能**表现出它标注的那个缺陷。

**方案**：

1. **把 profile 从一个布尔改为一组缺陷轴。**
   ```python
   class DefectAxis(StrEnum):
       COUPON_RESTORED_ON_REFUND = "COUPON_RESTORED_ON_REFUND"
       REFUND_AGAINST_ORIGINAL = "REFUND_AGAINST_ORIGINAL"
       POINTS_GRANTED_AGAIN_ON_REFUND = "POINTS_GRANTED_AGAIN_ON_REFUND"
       POINTS_OVERREDEMPTION = "POINTS_OVERREDEMPTION"
       FULL_REFUND_AFTER_CONSUMPTION = "FULL_REFUND_AFTER_CONSUMPTION"
       ENTITLEMENT_LEFT_AFTER_REFUND = "ENTITLEMENT_LEFT_AFTER_REFUND"
       ENTITLEMENT_OVERCONSUMPTION = "ENTITLEMENT_OVERCONSUMPTION"

   @dataclass(frozen=True)
   class SandboxProfile:
       axes: frozenset[DefectAxis] = frozenset()

       @property
       def restores_coupon_after_full_refund(self) -> bool:
           return DefectAxis.COUPON_RESTORED_ON_REFUND in self.axes
       # …其余 6 个属性同构
   ```
   兼容层：`SandboxVersion.VULNERABLE` → 全部轴，`FIXED` → 空集，使既有调用不变。

2. **运行请求携带轴集合**：`POST /internal/runs` 增加可选 `defect_axes: [str]`；给出时
   以它为准，否则回落到 `sandbox_version` 语义。

3. **case 元数据携带轴集合**，并加一条**用例契约**：
   > 一个 case 的环境 = 固定 profile + 它声明的轴集合；**不得**包含未声明的轴。

4. **不变量与缺陷轴的绑定表**（写进 Evaluation 侧，不进 Runtime）：每个轴对应它可能
   触发的那个不变量。这条表让"发现了哪一类缺陷"成为可归因的指标，而不是一个总数。

**关键取舍**：
- **不改 Oracle**。Oracle 依然只读快照/回执/事件，不看 profile——否则就变成自己验证自己
  （原则二、四）。缺陷轴只影响**被测量的实现**，不影响裁决标准。
- 不加"每个 case 一个容器"这类重方案：轴集合已经足够让环境互斥，且不必重做部署。

**验收**（三条，都可用现有探针脚本自动化）：
1. **互斥性**：对每个 case，穷举其环境上的全部合法路径并逐一重放，**只有该 case 声明的轴
   对应的不变量**可能被违反；任何其他不变量恒为 SATISFIED 或 NOT_APPLICABLE。
2. **可发现性**：每个 case 的 ground truth 在其环境中仍然确认 3/3（即缺陷没有被轴化改坏）。
3. **对照**：同一路径在"固定 profile"（空轴集）上重放必须**不**确认——这是 case 具备
   区分力的证明，也是现有 `test_case_reachability.py` 已经建立的模式，扩到全部 case。

**依赖**：无（在 R2 之前做）。
**成本**：中等——`profiles.py` 与 sandbox 的 run 创建改动小，难点在把 7 个轴与
现有 7 个属性的调用点逐一核对，以及给每个 case 标注轴集合。

### B2. 用例生成器与统计效力（R4 续 + R5，修 P1-3）

**问题**：9 个漏洞 case 无法判定任何差异。要让 `0/n` 的上界低于 22% 需要 **n ≥ 14**
（Wilson：`3.84 / (n + 3.84) < 0.22`）。

**目标**：漏洞 case ≥ 14，且每个 case 的**缺陷类别可归因**。

**方案**：由 `scenario × 缺陷轴（单轴 / 双轴组合）× 深度` 生成用例，而不是手写。
生成器输出 case 元数据（含轴集合与期望不变量），评测侧仍负责 ground truth 的 3/3 确认。
hidden 必须使用**与 dev 不同的 RuleSpec 参数**（阈值、`restore_on_full_refund`、
`revoke_on_refund`、`maximum_refunds_per_order`），否则不构成 holdout。

**R5 同批做**：`repetitions >= 3`，报告 `pass@k` 与 **`pass^k`**（代码已支持，从未跑过
k>1），所有比率带 Wilson 区间。

**验收**：漏洞 case ≥ 14；hidden 的 rule_specs 与 dev 的 diff 非空；每个新增 case 自动过
B1 的三条验收；README 的评测表每格是 `x/n [CI]` 且能给出 `pass^3`。
**依赖**：B1（轴是生成器的输入）。
**成本**：最大的一项——生成器 + 14 个 case 的 ground truth + 一次四基线重跑（约 $3）。

---

## Phase C：搜索能力（R2 + R3 合并设计）

> 这两项必须合在一起做：R2 引入的探针要有账可记，而 R3 正是把账本换成探针数。

### C0. 前置：规范化状态投影（**必须先做，否则 R2 不可用**）

**问题（实测发现）**：模拟器的 `normalized()` 与 Sandbox 快照的 `state` 字段几乎一致但
**不完全一致**：

| 字段 | Sandbox | Simulator |
| --- | --- | --- |
| 用户标识 | `id` | `user_id` |
| 会员状态 | `"INACTIVE"` | `"NONE"` |

朴素逐字段差分会在**每一步为每个实体**报出这类假分歧，真正的实现偏离会被词汇噪声淹没。

**方案**：定义一份**共享规范化投影** `canonical_state(raw) -> dict`，两侧都经它转换后再比较；
字段名、枚举取值、金额表示（`Decimal` 字符串）统一。投影放在 `domain_contracts`（共享包），
Simulator 与 Sandbox 快照各自实现。

**验收（这条同时是保真度的可证伪定义）**：
> 在**固定 profile** 上，对全部合法路径，投影后的差分**恒为空**。

现有文档里"Simulator 与 Sandbox 的保真度通过抽样对照检验"是一句无法证伪的话；这条把它变成
一个能跑的红/绿测试。**不满足就先修投影，不进入 C1。**

### C1. 差分观测闭环（R2，修 P2-6）

**问题**：Agent 在**没有缺陷的模拟器**里搜索，看不到靶场的真实行为。证据：Single 在 v3 下
`DUPLICATE_ACTION` 被拒 21 次——模型反复提交"没有生效"的动作，因为它看不到没生效。

**方案**：为每个策略开一个 **scratch RunSpace**，逐步执行；每步后取靶场权威快照，与模拟器
预测状态做**投影后差分**，把差异（**仅字段级差异，不含不变量名、不含缺陷标记**）放进
`AgentContext.target_divergence`：

```python
class StateDelta(BaseModel):
    entity: str          # "users" / "orders" / ...
    key: str             # 实体标识
    field: str           # "points_balance"
    predicted: str
    observed: str

# AgentContext 新增：
target_divergence: tuple[StateDelta, ...] = ()   # 最多 N 条，按字段名稳定排序
```

提示词补一句：`target_divergence` 是**真实系统的可观测行为与你的预测之差**，不是结论；是否
构成违规仍由重放后的 Oracle 判定。

**关键取舍**：
1. **确认仍在干净 RunSpace 重放**（原则二不受影响）：scratch 只服务搜索，不进证据链。
2. **不泄漏 Ground Truth**：差分只暴露"被测系统的可观测行为"，这正是黑盒模糊测试合法获得的
   信息；缺陷位置与不变量标签仍要 Agent 自己推导。
3. **探针失败要留痕**：靶场不可用时降级为"无差分"，但**运行事实里记录探测是否生效**
   （`probing: active | unavailable`），否则一次探测失效的运行会与正常运行混在同一张表里。

**验收**：
1. 固定 profile 上任意路径的差分为空（C0 的验收，此处复用）；
2. vulnerable profile 上，触发缺陷的那一步**必然**出现非空差分；
3. 等探针预算下，带观测的基线发现率高于不带观测的（**这是 R2 存在的唯一理由**，必须消融）。

### C2. 探针归一化预算 + 确定性上界基线（R3）

**问题**：预算单位是 steps/tokens/time，含义不透明且会让"重发上下文"这种记账噪声主导结论
（v3 之前 100% 的策略死于 token 预算）。而且当前"BFS"不是搜索——它只交回**一条** canonical
浅路径，其 22% 来自那条路径恰好命中，不是搜索能力的产物。

**方案**：
1. **预算单位 = 探针动作数**（对靶场发出的 HTTP 写动作）。token/成本降为**报告指标**，
   保留一个成本上限作安全网。`BudgetUsage` 增加 `probes`。
2. **`ExhaustiveBounded` 基线**：在契约动作空间内按深度枚举全部路径，逐条对**全部**不变量
   重放，直到用尽探针预算；报告实际达到的深度。这是给定预算下的**确定性上界**。
3. **重命名**：`BFS` → `CanonicalPath`（它本来就是），README 与代码同步。

**关键取舍**：枚举受深度上界约束（契约允许无限次 CREATE_ORDER），因此 `ExhaustiveBounded`
给出的是"该深度内的完备"，报告时必须写出实际深度，不能笼统称"穷举"。

**验收**：
1. 五个基线（CanonicalPath / ExhaustiveBounded / Random / Single / Multi）在同一探针预算下
   产出一张表；
2. `ExhaustiveBounded` 达到 B1 验收 1 所测的上界——**自洽校验**：两个独立途径得到同一个上界；
3. 新指标 `probes_to_first_confirmed`（中位数/p95）与 `confirmations_per_100_probes`。

**依赖**：C0（差分要可比）→ C1（产生探针）→ C2（给探针记账）。
**成本**：大——`BudgetUsage`/`Budget` 是核心类型，改动会波及 Worker、Evaluation、前端预算显示。

---

## Phase D：收尾

### D1. 口径迁移（R7）

`docs/exec/*` 五份阶段报告仍是 v2 数字。它们是历史记录，**不改写**，但在每份开头加一行：
> 本报告的数字属于已作废的 golden-v2 口径；当前口径见 README 评测区与
> `benchmarks/README.md` 版本历史。

同时把项目说明（`E:\notes\...\RuleArena-项目说明.md`）与简历口径同步到 v3，并保留第 3 节
列出的六处更正——它们本身是"我能发现并纠正自己的错误结论"的证据。

### D2. 前端测试覆盖

演示页的四个主张目前无测试：结论句、违规步标注、两个 profile 并排对比的数值、Oracle 全量
清单的计数。方案：`FrozenDemo.test.tsx` 用**已提交的冻结 JSON** 断言这四项（而不是造 fixture），
这样冻结数据被误改也会被测试抓到。

### D3. 15 条合成运行

**不动**。它们是 append-only 保护的代价之一，且已被后续真实运行超越。若要清除，需临时停用
触发器并留下审计记录——收益（清掉 15 条无影响的行）远小于削弱"已完成运行不可改写"这一保证。

### D4. Live Run 报价

不做预测性放宽。等 A1 的实测：若 90 秒内确实提交不了候选，就把结论写进 README；若提交得了，
把实测的步数/探针数写进 README 作为公开演示的能力边界。

---

## 依赖与顺序

```
A1 全栈冒烟 ──┐
A2 hidden@v3 ─┴─→ (门禁有当前证据)
B1 缺陷矩阵 ──→ B2 用例生成器 + R5 统计效力
               │
C0 规范化投影 ─┴─→ C1 差分观测 ──→ C2 探针预算 + ExhaustiveBounded
                                     │
D1/D2 收尾 ←────────────────────────┘
```

**如果只有周末两天**：做 A1、A2、B1、C0。前三个补上"未验证"与最大的度量缺陷，C0 是把
"保真度"从一句无法证伪的话变成红/绿测试——四项都不大，但都会改变项目的可信度基线。

**如果有一个月**：再加 B2（扩用例到 ≥14，让发现率第一次可判定）与 C1+C2（搜索层真正提升）。
