# 阶段 2 Review Spec：参考模拟器、Oracle、BFS 与重放

角色：独立 Reviewer  
模式：只读、对抗式复核。  
关键判断：确定性程序是否真正裁决真实 API 状态，以及是否存在与 Sandbox 同源错误。

## 1. 审查输入

阅读全部核心 Spec、阶段 1 Review、阶段 2 执行 Spec、代码、测试、依赖图和实现报告。确认 Ground Truth、Profile 和预期序列的可见路径。

## 2. 优先审计

1. 用 import graph 和全文搜索确认 Simulator/Oracle 没有复用 Sandbox Service、ORM 或判定逻辑。
2. 逐个检查八个 invariant 的数学/业务口径、Decimal、边界和证据字段。
3. 确认 `CONFIRMED_VIOLATION` 只能由干净 Sandbox 重放后的 Oracle 结果产生。
4. 确认 `MODEL_DIVERGENCE`、`INSUFFICIENT_EVIDENCE` 不会被 UI/API 计作漏洞。
5. 检查 state_hash 是否排除时间戳、随机 ID、插入顺序等非语义噪声。
6. 检查 BFS/Random 的预算、去重和 deterministic seed。
7. 检查 Delta Debugging 每次是否 reset 并验证同一 invariant。

## 3. 亲自实验

- 对至少三个开发 Case 连续重放 3 次。
- 修改一个 Simulator 转换制造模型偏差，确认不会产生 confirmed 漏洞；实验后恢复工作区。
- 对一条反例尝试删除每个剩余动作，验证 1-minimal。
- 对 fixed Profile 运行同序列，确认不误报。
- 改变种子和预算，确认结果记录可复现参数且“未发现”不被表述为安全证明。

执行测试、架构检查、lint/typecheck，并报告真实退出码。

## 4. 严重级别

- P0：Oracle 读取 Ground Truth；复用 Sandbox 业务判定；未重放即确认；误把偏差/证据不足当漏洞；金额计算错误。
- P1：重放不稳定；Hash 不确定；八个 invariant 缺失；最小化不满足 1-minimal；BFS 无预算。
- P2：性能、证据可读性、额外剪枝或非阻断文档问题。

## 5. 通过标准

| 维度 | 必须达到 |
| --- | --- |
| Independence | 共享契约但不共享业务实现 |
| Oracle | 结构化、确定性、证据充分 |
| Confirmation | 真实 HTTP 重放且 3/3 稳定 |
| Search | Random/BFS 有界、可复现 |
| Minimization | 同 invariant 的 1-minimal 反例 |
| Honesty | 不把预算内未发现描述为证明安全 |

## 6. 输出模板

```text
结论：PASS | CONDITIONAL PASS | FAIL
代码/依赖审计范围
亲自执行的测试与对抗实验
Findings：P0/P1/P2
八个 invariant 验收表
重放稳定性与最小化证据
Common-mode failure 结论
是否允许进入阶段 3
```
