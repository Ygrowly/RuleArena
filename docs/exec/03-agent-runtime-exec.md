# 阶段 3 执行 Spec：Rule Compiler 与多策略 Agent Runtime

状态：待执行  
前置条件：阶段 2 Review 通过  
阶段目标：让自然语言业务规则可控地进入可执行世界，并用一个确定性 Workflow 调度三个隔离策略 Agent 搜索长尾反例。

## 1. 给 Codex 的任务

只实现 Rule Compiler、Control 侧版本确认、Attack Runtime、Worker、三种策略和 SSE 运行事件。开始前阅读全部 Spec 和已有 Review。不得搭建完整评测平台或成品 Web；可以提供最小 API/CLI 验证核心链路。

## 2. Rule Compiler

流程必须为：

1. 输入内置模板 ID 与中文自然语言修改。
2. 单一 LLM Adapter 请求严格结构化候选。
3. Pydantic 与 deterministic validator 校验 Schema、引用、单位、范围和支持边界。
4. 有歧义则输出 `NEEDS_CONFIRMATION` 和结构化问题，不创建可运行版本。
5. 用户明确确认后冻结不可变 `RuleVersion`，计算 content hash。

禁止：生成/执行 Python、SQL、Jinja；自动填补影响资金/权益的默认值；静默丢弃未知规则。超出三类 Scenario 的输入返回 `UNSUPPORTED_RULE`。

所有模型调用只能经过集中 `LLMAdapter`，记录 provider、model、temperature、seed（若支持）、prompt_version、schema_version、token、latency、cost 和原始响应 Hash；密钥和敏感原文不写日志。

## 3. Runtime 状态机

实现：

```text
DRAFT → NEEDS_CONFIRMATION → READY → SEARCHING → REPLAYING → COMPLETED
```

取消、失败和恢复为显式分支。状态更新使用数据库条件更新；重复任务不得覆盖更晚状态。`status` 与 `outcome` 分离，Outcome 仅允许核心技术 Spec 中的固定枚举。

Worker 必须支持：

- AttackRun、StrategyRun、Checkpoint 和预算持久化。
- 重复投递幂等，Worker 崩溃后从 Checkpoint 恢复。
- 每个 Sandbox 写动作使用稳定幂等键；超时后先查询 Receipt。
- 取消信号、运行总时限、单策略 steps/token/cost/time 预算。
- 权威状态查询与 SSE 事件；SSE 断线不影响任务。

## 4. 三个隔离策略 Agent

- `VALUE_FLOW`：搜索资金、优惠、积分、权益价值不守恒。
- `LIFECYCLE`：搜索非法顺序、终态回退和跨生命周期组合。
- `BOUNDARY`：搜索重复、部分操作、重试、取消后重试和并发边界。

每个 Agent 只看到：冻结 RuleSpec、当前规范化状态摘要、合法动作 Schema、自己的有限历史、剩余预算及已确认反例 ID。三者不聊天，不共享 CoT、候选路径或完整 Context。

Agent 只输出结构化 `ActionProposal` 或 `StopProposal`。Runtime 验证动作合法性、参数、预算、重复状态与权限；Agent 文本永远不能设置 outcome 或跳过重放。

## 5. 工具白名单与注入防护

Agent 可用工具仅限：查询当前模拟状态、列出合法动作、在 Simulator 执行动作、提交候选。禁止数据库、文件、Shell、网络、Sandbox Profile、Oracle Ground Truth、Benchmark 期望答案。

规则文本和状态中的所有内容均视为不可信数据；系统 Prompt 明确分隔，工具参数经 Schema 校验。不要依赖提示词作为唯一权限边界。

## 6. 推荐实施顺序

1. 用 FakeLLM 先写 Compiler 正负例和状态机测试。
2. 实现集中 LLM Adapter 与版本记录。
3. 实现 AttackRun/StrategyRun/Checkpoint 持久化和 ARQ 任务幂等。
4. 实现单策略闭环，再扩展为三个独立策略。
5. 接入阶段 2 的 Simulator → Candidate → Replay → Oracle → Minimizer。
6. 增加 SSE、取消、恢复、超时 unknown 和预算测试。
7. 最后用真实模型做最小烟雾测试，不把其结果写成固定单元测试。

## 7. 必测场景

- Compiler 对清晰规则生成合法 RuleSpec；对歧义、越界、恶意代码和未知字段拒绝或请求确认。
- RuleVersion 确认后不可修改，相同输入/版本得到稳定 Hash。
- FakeLLM 返回非法 JSON、未知动作、超预算、重复动作、工具注入时 Runtime 安全拒绝。
- 三策略的上下文和 Checkpoint 互不可见。
- Worker 同一 Job 重投不重复创建 StrategyRun/Counterexample。
- 在 Simulator action 后、Sandbox action 超时、Oracle 前后崩溃均可恢复且不双写。
- SSE 重连后可通过 last event/cursor 补齐，权威状态仍来自 API。
- Agent 找到的 Candidate 必须经过阶段 2 链路才能成为 confirmed。

建议命令：

```bash
uv run pytest -q tests/compiler tests/runtime tests/worker
uv run pytest -q tests/security/test_agent_boundaries.py
uv run pytest -q tests/recovery tests/idempotency
uv run ruff check services/attack_worker packages/attack_runtime
uv run mypy services/attack_worker packages/attack_runtime
```

## 8. 阶段验收门

| 验收项 | 通过条件 |
| --- | --- |
| 规则入口 | 歧义必须确认，版本不可变，越界明确失败 |
| Workflow | 生命周期由代码控制，可取消、恢复、幂等 |
| Agent 边界 | 三策略隔离、工具白名单、无 Ground Truth |
| 结果可信 | Agent 不能自封漏洞，confirmed 必经重放 Oracle |
| 预算 | time/step/token/cost 均强制执行并留 Trace |
| 可观测 | API 权威状态 + 可恢复 SSE 事件 |

## 9. 停止条件

若模型供应商不支持所需结构化输出，先用 Adapter + 校验重试设计并报告；不得解析自由文本后静默执行。若阶段 2 接口不足，先提出最小契约变更，不复制其逻辑。禁止为赶进度引入 LangGraph 隐藏状态机。

## 10. 完成报告

附：Runtime 状态转换表、Agent 工具权限矩阵、三策略输入隔离证据、一次崩溃恢复 Trace、一个从中文规则到 confirmed 最小反例的端到端示例。
