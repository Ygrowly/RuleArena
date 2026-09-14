# RuleArena Benchmark assets

- `development-v1.json` contains the 16 public development cases and their replay evidence.
- `hidden-manifest.json` contains only non-answer metadata for the 8 hidden cases.
- `refund_agents/development-v1.json` contains the 15 public refund tickets for the
  agent-gateway comparison (see below).
- The full hidden suite is not stored in the public repository. Deployment mounts it and sets
  `RULEARENA_PROCESS_ROLE=evaluation` plus `RULEARENA_HIDDEN_SUITE_PATH` for the evaluation job.

The Attack Runtime has no dependency on `rulearena-evaluation`, no hidden-case loader, and no file
tool. This prevents accidental prompt, trace, SSE, or public API exposure. A repository maintainer can
still alter source code to exfiltrate a mounted file; process/container permissions are therefore the
final deployment boundary, not Python object privacy.

## 版本历史

- `golden-v1`：初始 24 Case（16 development + 8 hidden），预算 12 步 / 12k tokens /
  $1.5 / **90s**。真实模型实测（deepseek-v3.2）显示 LLM 每步延迟 5–10s，90s
  结构性不足：Agent baseline 平均 75.8s / 13.75 步即被时间预算截断，从未提交
  候选；非 LLM 的 Random/BFS 不受影响，对比不公平。
- `golden-v2`：仅将 `max_time_seconds` 90 → **300**（依据实测延迟 p95
  校准），Case 内容、期望答案、其他预算与门禁阈值不变。变更原因与依据见
  2026-09-06 真实模型审查报告；旧 golden-v1 运行结果不可复用于 v2 门禁。
- `golden-v4`（当前）：**首个 Case 环境具备归因能力的版本**。此前每个 Case 的环境由
  一个布尔量 `sandbox_version` 决定，同一 scenario 下所有漏洞 Case 共享一个**能表现该
  scenario 全部缺陷**的环境。后果是实测中一条搜索路径可以踩中 B Case 的缺陷，却被记在
  A Case 的标签下——golden-v3 的 Multi-strategy 运行里因此有 **3 条 Oracle 已确认的违规
  被计为未命中**，Single 的分母也因一条用例的记账不一致退化为 8。
  本版本把布尔量换成**缺陷轴**（`DefectAxis`，7 个），每个漏洞 Case 显式声明它要测的
  那一个轴；Sandbox 只让声明的轴生效，并**拒绝**本 scenario 触达不到的轴（422，而不是
  静默失效）。同时 Sandbox 不再对"动作是否越界"做参数钳制，未解析输出从
  `INFRA_FAILED` 改为独立的 `UNPARSABLE_OUTPUT` 终止原因。
  改动的是 Case 的**运行环境**，因此 golden-v3 的发现率/误报数字**与新版本不可比**，
  门禁阈值不变（hidden 发现率 ≥ 75%）。v3 结果继续保留在 README 中作为历史记录。
  实测（deepseek-v4.1-flash，development 21 / hidden 17，hidden Multi 重复 3 次）：
  dev 四基线 0/2/4/5（共 14 个漏洞 case），hidden Multi `pass@3 = 8/14`、**`pass^3 = 1/14`**
  ——搜索的**不可复现性**从此可见，这是单次运行掩盖不了的。
- `golden-v3`：仅将 `max_tokens` 12000 → **100000**，Case 内容、期望答案、
  门禁阈值不变。依据是实测校准：真实模型每步消耗 input+output 的 p95 为 **2735
  tokens**，因此声明的 12 步预算需要 `12 × 2735 = 32820` tokens，而原配置只够
  `12000 / 2267 ≈ 5.3` 步——**"12 步"与"12k tokens"两个数字本身互相矛盾**，任何
  策略都不可能走到声明的步数。新值按 `同时搜索的策略数(3) × 12 步 × p95(2735)
  = 98460` 取整，使 SINGLE 与 MULTI 的每个策略都能走满 12 步。旧 golden-v1/v2
  运行结果不可复用于 v3 门禁，且**跨版本数字不可混排**。

## 缺陷轴与 Case 环境

每个漏洞 Case 声明它测量的缺陷轴；正常 Case 不声明（`defect_axes: []`，环境忠实）。

| 缺陷轴 | Scenario | 可观察量（不为忠实实现所动时才出现） |
| --- | --- | --- |
| `COUPON_RESTORED_ON_REFUND` | PROMOTION | 全额退款后优惠券回到 `AVAILABLE` |
| `REFUND_AGAINST_ORIGINAL` | PROMOTION | 两次部分退款各自以**原实付额**为上限（而非剩余额） |
| `POINTS_GRANTED_AGAIN_ON_REFUND` | REFUND_POINTS | 退款后又发一遍积分 |
| `POINTS_OVERREDEMPTION` | REFUND_POINTS | 余额不足仍可兑换 |
| `FULL_REFUND_AFTER_CONSUMPTION` | MEMBERSHIP_ENTITLEMENT | 已消费权益的会员仍可全额退款 |
| `ENTITLEMENT_LEFT_AFTER_REFUND` | MEMBERSHIP_ENTITLEMENT | 会员退款后权益仍然可用 |
| `ENTITLEMENT_OVERCONSUMPTION` | MEMBERSHIP_ENTITLEMENT | 消费数量可超过授予数量 |

轴到 scenario 的映射是**实现事实**：Sandbox 对每个轴的唯一引用点都在 scenario 分支内。
因此"声明一个本 scenario 触达不到的轴"会被加载期（Case）与请求期（Sandbox）双双拒绝。

验收测试：`tests/sandbox/test_defect_axes.py` 证明单轴环境的单位矩阵与轴组合；
`tests/evaluation/test_case_environments.py` 证明**每个 Case 的 Ground Truth 只在它自己
声明的轴下被确认**——在忠实环境下不确认，在同 scenario 的兄弟轴下也不确认。

### 传输缺陷轴（不在上表内）

`REFUND_ACK_LOST`（PROMOTION / REFUND_POINTS）与上表的区别是：它**不改动任何业务事实**，
只让回执在已提交之后丢失，调用方看到超时。因此没有任何搜索路径能在它之下"确认"一条不变量
——它由运行结果（调用方最终有没有多退钱）而不是由 Oracle finding 来度量。它在
`rulearena_domain_contracts` 里被单独列为 `TRANSPORT_DEFECT_AXES`，并从"每个业务轴都有
Case 支撑"的检查中排除；那条性质由下面的退款工单集来承担。

## 退款工单集（`refund_agents/development-v1.json`）

15 张工单，测的是**一个 Agent 处理工单的过程**，而不是一条搜索路径。同一份 Agent 代码跑两次
（`bare` / `gated`），唯一差别是网关后面挂不挂运行时门禁。结构与上面的 case 集同形：
`rule_specs` + `cases`，每张工单声明 `defect_axes` / `expected_final_state` /
`expected_invariants` / `expects_escalation` / `construction_reason`。

| 组 | 数量 | 环境 | 期望 |
| --- | --- | --- | --- |
| 正常全额/部分退款 | 5 | 忠实 | 退款落地，门禁不得误拦 |
| 回执丢失 | 6 | `REFUND_ACK_LOST` + `REFUND_AGAINST_ORIGINAL` | 只退一次；裸跑会退两次 |
| 工单金额与实付不符 | 4 | 忠实 | 转人工；不产生资损 |

回执丢失组必须**同时**声明 `REFUND_AGAINST_ORIGINAL`：忠实实现自己会拒绝第二笔超额退款
（`refunded + amount > paid`），单靠 `REFUND_ACK_LOST` 退不出第二笔钱，Oracle 也就没有
可判定的事实。这是这两个轴必须同时出现的**测量原因**，不是随手叠加。

生成脚本：`scripts/generate_refund_suite.py`（每张工单的期望终态由退款金额算出，避免手写
期望值与实际动作各写一遍）。
