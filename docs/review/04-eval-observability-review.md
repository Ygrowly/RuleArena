# 阶段 4 Review Spec：Golden Set、评测与可观测

角色：独立 Evaluation Reviewer  
模式：只读，重算而非相信大盘数字。  
目标：排除泄题、挑样本、口径漂移、旧结果复用和虚构指标。

## 1. 审查输入

阅读核心 Spec、阶段 3 Review、阶段 4 执行 Spec、全部 Case 元数据、Loader 权限、Benchmark Runner、指标代码、Trace Schema、原始 Run 与实现报告。

## 2. 数据集审查

- 核对 16/8 数量、三类 Scenario 和 normal/vulnerable 分布。
- 抽查每个 Ground Truth 是否能在目标 Profile 3/3 重放、fixed/normal 不触发。
- 确认 development 与 hidden 没有近重复路径导致数据污染。
- 追踪 hidden 内容从文件/数据库到 Worker、Prompt、Trace、API 的完整数据流。
- 检查 Case 是否在看到模型结果后被删除、改答案或调低难度；变更需有版本历史和理由。

## 3. 指标复算

从原始 AttackRun/StrategyRun/Replay/Oracle 数据独立重算：发现率、误报、确认率、稳定率、时间、steps、tokens、cost、pass@k、pass^k。随机抽样逐个核对 Run ID，不接受只有 CSV 汇总或前端数字。

检查四 Baseline 的 Case、预算、模型版本和重复次数是否可比。INFRA_FAILED、取消、超时和 N/A 的处理必须符合定义。

## 4. 泄漏与真实性审查

全文和运行时检查 Ground Truth、Profile、期望 invariant、隐藏动作序列是否出现在 Prompt、模型工具返回、公共 API、SSE、Trace 或错误堆栈。确认多 Agent 提升不是因为获得更多总 Token/时间而未做归一化。

任何简历/README 结论必须能指向实际 BenchmarkRun；不得接受占位数字或“预计提升”。

## 5. 亲自执行

至少执行确定性开发集、指标单元测试、历史回归和泄漏测试。若真实模型预算/密钥可用，运行完整或抽样复测；否则将模型结果标为 `NOT VERIFIED`，不可替实现者背书。

人为修改版本字段，验证 Gate 拒绝旧结果；人为插入一个泄漏标记，验证安全测试能检测，随后恢复工作区。

## 6. 严重级别

- P0：Ground Truth 泄漏；虚构/手填指标；normal confirmed 误报被隐藏；旧版本结果冒充当前；Case 改答案迎合模型。
- P1：Baseline 预算不可比；公式/分母错误；24 Case 不足；Trace 无法回溯；Release Gate 未执行。
- P2：可视化、性能、额外统计置信区间和非阻断文档问题。

## 7. 通过标准

| 维度 | 必须达到 |
| --- | --- |
| Dataset | 24 Case 可复现、版本化、无污染证据 |
| Metrics | 独立复算一致，失败/N/A 口径正确 |
| Ablation | Random/BFS/Single/Multi 公平可比 |
| Leakage | Ground Truth 暴露为 0 |
| Gate | 仅当前版本真实结果可用于发布 |

## 8. 输出模板

```text
结论：PASS | CONDITIONAL PASS | FAIL
数据集版本与分布
亲自执行和独立复算结果
Findings：P0/P1/P2
四 Baseline 对比及公平性结论
门禁逐项 Pass/Fail/Not Verified
Ground Truth 泄漏结论
允许/不允许进入阶段 5
```
