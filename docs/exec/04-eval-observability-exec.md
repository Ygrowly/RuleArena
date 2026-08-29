# 阶段 4 执行 Spec：Golden Set、评测与可观测

状态：待执行  
前置条件：阶段 3 Review 通过  
阶段目标：用可复现数据回答“多策略 Agent 是否真比 Prompt/单 Agent/确定性搜索更有价值”，并建立运行级证据链和发布门禁。

## 1. 给 Codex 的任务

只完成 Benchmark、Golden Set、Trace、指标计算和质量门。先阅读全部 Spec 和前序 Review。不得为了满足指标改 Ground Truth、给 Agent 泄题或填入虚构结果；不得提前美化最终 Web。

## 2. Golden Set

建立固定版本的 24 个 Case：

- development 16：实现期间可见，用于调试与回归。
- hidden 8：攻击 Runtime 不可读，只能由 Evaluation Runner 在最终裁决时加载。
- 三类 Scenario 均覆盖 vulnerable 与 fixed/normal。
- 覆盖顺序组合、重复/幂等、部分退款、生命周期、价值守恒和无漏洞正常场景。
- 每个 Case 固定 RuleVersion、ScenarioVersion、SandboxVersion、OracleVersion、预算、期望 invariant 集合和 Ground Truth 元数据。

Case 应来自明确规则推导与 Sandbox 可重放事实。每个 confirmed Ground Truth 首次入集前必须手工/确定性重放 3/3，并记录构造理由。

开发集与隐藏集必须在存储、加载接口、日志和 CI 权限上隔离。公共仓库若无法真正保密，至少做到 Runtime 无读取路径，并把隐藏评测资产作为部署侧私有配置；文档诚实说明威胁模型。

## 3. Baseline 与消融

在相同 Case、版本、预算口径下运行：

1. Random。
2. BFS。
3. Single Agent：选择一个预定义通用策略，不得拼接三策略结果。
4. Multi-strategy：VALUE_FLOW + LIFECYCLE + BOUNDARY。

保存每次原始 Run，不只保存聚合数字。模型、prompt、temperature、seed、预算任一变化都生成新的 BenchmarkRun，不覆盖历史。

## 4. 指标定义

- RuleSpec Schema 通过率 = 合法 RuleSpec 数 / 编译尝试数。
- 漏洞发现率 = 至少找到一个正确 confirmed invariant 的 vulnerable Case 数 / vulnerable Case 数。
- 正常确认误报率 = 产生 confirmed violation 的 normal Case 数 / normal Case 数。
- Candidate 确认率 = confirmed candidates / replayed candidates。
- 重放稳定率 = 同一反例成功违反同一 invariant 的次数 / 重放次数。
- 时间、steps、tokens、cost 同时报告 mean、median、p95；不得只选最好值。
- `pass@k`：k 次独立运行至少一次发现正确漏洞的概率估计。
- `pass^k`：k 次独立运行全部满足目标的比例；实现中必须用全称聚合，避免与 pass@k 混淆。

所有分母为 0 的指标返回明确 N/A，不得填 0 或 100%。指标查询必须可追溯到 Run ID。

## 5. Trace 与版本绑定

实现技术 Spec 的层级 Trace，并至少保存：版本元组、策略、step、模型配置、prompt_version、动作摘要、before/after hash、工具结果、延迟、token/cost、retry、status/error。

- 原始敏感规则和密钥不得进入 Trace。
- LLM 原始回复只保存受控摘要或加密/Hash，按配置决定。
- 每个 Counterexample 可导航到 Replay、Oracle Finding 和所有源 Step。
- 指标从持久化事实重算，禁止由前端或 LLM 自报。

## 6. CI 与评测运行

- PR CI：无真实 LLM 的契约、Oracle、开发集确定性子集和历史 P0 回归。
- 按需 Benchmark：使用真实模型，完整 24 Case 与四 Baseline。
- Release Gate：读取最近一个完全匹配版本元组的 BenchmarkRun，不得用旧结果。
- 评测失败和基础设施失败分开统计，不能把 INFRA_FAILED 当未发现。

门禁：正常 confirmed 误报 0；反例重放 3/3；隐藏漏洞发现率 ≥75%；历史 P0 回归 100%；Ground Truth 泄漏 0。若 Multi-strategy 未优于 Single/BFS，保留实现但在结论中如实降级其价值主张。

## 7. 必测要求

- Case loader 的权限与 Runtime 隔离测试。
- 人为向 Prompt/Trace 注入 Ground Truth 标记时测试失败。
- 同一原始 Run 重算指标结果一致。
- pass@k/pass^k 使用小型手算样例验证。
- INFRA_FAILED、CANCELLED、NO_VIOLATION 的分母口径正确。
- 修改任一版本字段后 Release Gate 拒绝复用旧 Benchmark。
- Trace 中找不到密钥、完整敏感字段和隐藏预期序列。

建议命令：

```bash
uv run pytest -q tests/evaluation tests/observability
uv run pytest -q tests/security/test_ground_truth_leakage.py
uv run pytest -q tests/regression
uv run rulearena benchmark --suite development --baselines random,bfs,single,multi
uv run rulearena benchmark verify --latest
```

## 8. 阶段验收门

| 验收项 | 通过条件 |
| --- | --- |
| 数据集 | 16 development + 8 hidden，版本与构造依据完整 |
| 可比性 | 四 Baseline 使用相同预算口径和 Case |
| 指标 | 可由原始 Run 重算，定义和失败口径明确 |
| Trace | 反例到模型/动作/API/Oracle 全链可追溯 |
| 泄漏 | Runtime/Prompt/Trace/Public API 中 Ground Truth 为 0 |
| 门禁 | 用真实运行结果判定，不手填、不挑样本 |

## 9. 停止条件

如果 24 Case 尚不能被固定 RuleSpec 表达、隐藏集无法隔离、模型成本超出用户预算，先提交缩减运行频率或部署隔离方案。不得减少 Case、调低门禁或改答案来制造通过。

## 10. 完成报告

附：24 Case 清单（隐藏集只给非泄题元数据）、指标公式、四 Baseline 实际运行表、失败 Case、版本元组、Release Gate 结果、Ground Truth 泄漏扫描证据。明确区分测得事实和推断。
