# 阶段 4 完成报告：Golden Set、评测与可观测性

状态：实现完成；开发集确定性验证通过；完整 24 Case / 四 Baseline Release Gate 尚未执行  
日期：2026-09-01

## 1. 实现结果

- 新增独立 `rulearena-evaluation` 包，提供 development/hidden loader、四种 Baseline、原始 Run 持久化、指标重算、Release Gate 和 `rulearena benchmark` CLI。
- Control PostgreSQL 新增 append-only `benchmark_run`、规范化 `benchmark_case_run` 和 `trace_step`；数据库触发器拒绝 Benchmark 历史 UPDATE/DELETE。
- Runtime Trace 覆盖 LLM、proposal、simulation、Sandbox HTTP、snapshot 和 Oracle，并保存父子关系、版本、hash、耗时、token/cost、状态与错误摘要。
- Trace 拒绝 API key、Authorization、完整敏感规则和 Ground Truth 标记；公共 Benchmark API 只返回清洗后的聚合结果。
- hidden 完整载荷只允许 `RULEARENA_PROCESS_ROLE=evaluation` 的部署侧进程通过私有路径加载。公共仓库仅保存无答案 manifest；Runtime 不依赖 evaluation 包，也没有文件读取工具。
- 每个已确认反例的候选确认与稳定性分开统计；稳定性使用三个干净 RunSpace 的同一路径回放，Release Gate 要求 3/3。

## 2. Golden Set 清单

统一预算为 `max_steps=12, max_tokens=12000, max_cost=1.5, max_time_seconds=90`。Development 共 16 个 Case：9 vulnerable、7 normal。

| Case | Scenario | Profile | 预期 invariant / 正常构造 |
| --- | --- | --- | --- |
| dev-promotion-01 | PROMOTION | vulnerable | `COUPON_SINGLE_CONSUMPTION`：全额退款后重复用券 |
| dev-promotion-02 | PROMOTION | vulnerable | `REFUND_NOT_EXCEED_PAID`：两次 150 元退款超过 200 元实付 |
| dev-promotion-03 | PROMOTION | vulnerable | `COUPON_SINGLE_CONSUMPTION`：跨订单重复用券 |
| dev-promotion-04 | PROMOTION | fixed | 全额退款不恢复不可恢复优惠券 |
| dev-promotion-05 | PROMOTION | fixed | 合法部分退款不超过累计实付 |
| dev-promotion-06 | PROMOTION | fixed | 订单终态不可回退 |
| dev-refund-01 | REFUND_POINTS | vulnerable | `POINTS_VALUE_CONSERVATION`：全额退款重复发放积分 |
| dev-refund-02 | REFUND_POINTS | vulnerable | `POINTS_VALUE_CONSERVATION`：超额兑换产生负积分 |
| dev-refund-03 | REFUND_POINTS | vulnerable | `POINTS_VALUE_CONSERVATION`：部分退款错误增发积分 |
| dev-refund-04 | REFUND_POINTS | fixed | 退款按比例撤销积分 |
| dev-refund-05 | REFUND_POINTS | fixed | 合法兑换与退款保持价值守恒 |
| dev-membership-01 | MEMBERSHIP_ENTITLEMENT | vulnerable | `ENTITLEMENT_REFUND_CONSISTENCY`：已消费仍全额退款 |
| dev-membership-02 | MEMBERSHIP_ENTITLEMENT | vulnerable | `ENTITLEMENT_NON_NEGATIVE`：超额消费权益 |
| dev-membership-03 | MEMBERSHIP_ENTITLEMENT | vulnerable | `ENTITLEMENT_REFUND_CONSISTENCY`：退款后仍可消费权益 |
| dev-membership-04 | MEMBERSHIP_ENTITLEMENT | fixed | 未使用会员退款并撤销权益 |
| dev-membership-05 | MEMBERSHIP_ENTITLEMENT | fixed | 已消费时拒绝 UNUSED_ONLY 退款 |

Hidden 公共 manifest 仅披露以下非答案元数据：

| Case | Scenario | 覆盖标签 |
| --- | --- | --- |
| hidden-01 | PROMOTION | sequence |
| hidden-02 | PROMOTION | partial-operation |
| hidden-03 | PROMOTION | lifecycle |
| hidden-04 | REFUND_POINTS | retry |
| hidden-05 | REFUND_POINTS | value-conservation |
| hidden-06 | REFUND_POINTS | partial-operation |
| hidden-07 | MEMBERSHIP_ENTITLEMENT | lifecycle |
| hidden-08 | MEMBERSHIP_ENTITLEMENT | retry |

私有 hidden 载荷不在公共工作区中，因此本报告不披露其 expected outcome、invariant、动作序列或构造原因。

## 3. Ground Truth 证据

9 个 development vulnerable Case 均在真实 PostgreSQL-backed Commerce Sandbox 上执行三个干净 RunSpace；27/27 回放由 Oracle 确认预期 invariant。没有通过修改答案或手填运行结果制造通过。

Normal Case 不携带漏洞动作或 expected invariant。其误报率由 Baseline 的真实原始 Run 计算，而不是把 manifest 中的预期写入 Runtime。

## 4. 指标口径

- Schema 通过率：合法 RuleSpec 数 / 编译尝试数。
- 漏洞发现率：至少确认一个正确 invariant 的 vulnerable Case 数 / 可评测 vulnerable Case 数。
- 正常确认误报率：产生 confirmed violation 的 normal Case 数 / 可评测 normal Case 数。
- Candidate 确认率：confirmed candidates / replayed candidates。
- 回放稳定率：已确认反例在独立干净 RunSpace 中再次违反同一 invariant 的次数 / 稳定性回放次数。
- `pass@k`：对每个具备 k 次独立运行的 vulnerable Case 取 `any(first k)` 后求比例。
- `pass^k`：对每个具备 k 次独立运行的 vulnerable Case 取 `all(first k)` 后求比例。
- elapsed、steps、tokens、cost 均报告 mean、median、p95。
- `INFRA_FAILED` 与 `CANCELLED` 单独计数，不进入有效评测分母；`NO_VIOLATION_WITHIN_BUDGET` 是有效未发现结果。
- 分母为 0 时返回 `value=null`（N/A），不会伪装成 0% 或 100%。每个聚合项保留 source Run IDs。

## 5. Baseline 实测

以下为同一 development 16 Case、同一版本元组、同一预算、seed `20260831`、每 Case 1 次的真实运行。Random/BFS 使用真实 Sandbox HTTP；Single/Multi 需要真实模型，当前环境未提供 `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`，因此没有用 FakeLLM 补值。

| Baseline | BenchmarkRun | 漏洞发现率 | Normal 误报率 | Candidate 确认率 | 稳定回放 | elapsed mean/median/p95 | steps mean/median/p95 | 状态 |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| Random | `374f02d4-404b-4d9c-a5d4-e0d2b2ee9b42` | 0/9 = 0% | 0/7 = 0% | 0/70 = 0% | N/A | 6.993/7.547/8.406 s | 3.375/3/4 | 已实测 |
| BFS | `7cca8da7-e717-4c7d-bb0b-b97b047bb59e` | 2/9 = 22.22% | 0/7 = 0% | 3/70 = 4.29% | 9/9 = 100% | 7.528/7.805/8.625 s | 3.375/3/4 | 已实测 |
| Single Agent | — | — | — | — | — | — | — | 未执行：缺真实 LLM 配置 |
| Multi-strategy | — | — | — | — | — | — | — | 未执行：缺真实 LLM 配置 |

BFS 实际发现 `dev-membership-01` 和 `dev-membership-03`；Random 未发现漏洞。两次运行均无 INFRA_FAILED、CANCELLED 或 EVALUATION_FAILED。tokens/cost 对无 LLM 的搜索 Baseline 均为 0。

这些数据只能证明确定性搜索 Baseline 的当前表现，不能支持“Multi 优于 Single/BFS”的产品主张。

## 6. 版本元组与 Release Gate

本次 development 搜索实测版本元组：

```text
benchmark=golden-v1
runtime=runtime-v1
rule_set=rules-v1
scenario_set=scenarios-v1
sandbox=sandbox-suite-v1
oracle=1.0
prompt=benchmark-v1
temperature=0.0
seed=20260831
model_config_hash=SHA256("none:none:0.0")
```

Release Gate 状态：**NOT VERIFIED / 不允许发布通过**。原因是完整私有 hidden suite 和真实 LLM 配置均不在当前环境，因而不存在完全匹配版本元组的 completed hidden Multi-strategy BenchmarkRun。`benchmark verify --latest` 会拒绝旧版本、错误预算、错误 seed、非 hidden/non-multi 或非 completed 结果，并要求：normal 误报 0、hidden 漏洞发现率至少 75%、每个确认反例 3/3、历史 P0 100%、Ground Truth 泄漏 0。

## 7. 泄漏与可观测性证据

- loader 权限测试证明 Runtime 角色不能加载 hidden 私有载荷；只有 evaluation 角色加显式私有路径可以加载。
- 注入 Ground Truth marker/fingerprint 到 Prompt 或 Trace 会被扫描器和 Trace schema 拒绝。
- Runtime 依赖图中没有 evaluation 包，公共 hidden manifest 没有 outcome、invariant、动作或构造理由。
- Trace 集成测试验证 LLM → proposal → simulation/Sandbox → Oracle 父子链、before/after hash，以及敏感字段不落库。
- 公共 Benchmark API 清除 hidden case IDs 和 source Run IDs，只暴露安全聚合。

## 8. 验证结果

```text
阶段 4 必需测试（仅 4 个测试文件）：12 passed
其中真实 Ground Truth：9 Case × 3 = 27/27 confirmed
全量回归：112 passed
Ruff：All checks passed
Mypy：Success: no issues found in 54 source files
compileall：passed
git diff --check：passed（仅 Windows LF/CRLF 提示）
Control Alembic upgrade + drift check：No new upgrade operations detected
Sandbox Alembic upgrade + drift check：No new upgrade operations detected
Benchmark CLI help：passed
Random/BFS development Benchmark：completed，原始事实已持久化
benchmark verify --latest：按预期拒绝，matching_benchmark=false（无匹配 hidden Multi Run）
```

全量回归第一次使用既有 16379 Redis 时在测试中途出现一次基础设施超时，结果为 111 passed / 1 infra failure；切换到隔离 Redis 后完整复跑得到 112 passed。最终结果不包含 skip 或失败。

## 9. 结论

阶段 4 的代码、数据契约、持久化、Trace、指标、CLI 和安全隔离已经完成并通过本地确定性验收。当前唯一未闭环项是需要外部资产的完整真实 Benchmark：部署侧 8 个 hidden 私有 Case与真实 LLM 凭据。获得这两项后，按原命令运行四 Baseline，再执行 `rulearena benchmark verify --latest`；在此之前 Release Gate 必须保持未通过状态。
