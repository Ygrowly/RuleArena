# 阶段 3 Review Spec：Rule Compiler 与多策略 Agent Runtime

角色：独立 Reviewer  
模式：只读，使用 FakeLLM、故障注入和最小真实模型烟雾测试。  
目标：确认 Agent 被 Runtime 约束，而不是一段 Prompt 直接控制副作用和结论。

## 1. 审查输入

阅读核心文档、阶段 2 Review、阶段 3 执行 Spec、模型适配器、Prompt、工具 Schema、Worker、数据库迁移、API、测试及 diff。

## 2. 首要审查

- 是否所有 LLM 调用都经过集中 Adapter，是否有旁路 SDK 调用。
- Rule Compiler 对歧义、未知表达和影响资金的缺省值如何处理。
- Agent 是否能直接访问 Sandbox、数据库、文件、网络或 Ground Truth。
- 三策略是否真实隔离，还是共享完整消息/候选后换了名字。
- Runtime 状态、outcome、预算是否由确定性代码决定。
- ARQ 重试、进程崩溃、HTTP timeout unknown 是否会造成双写。
- SSE 是否只是投影，客户端断线会不会改变运行。
- Prompt injection 是否有工具 Schema/权限层防护，而非只写一句“忽略”。

## 3. 对抗测试

用 FakeLLM 依次返回：非法 JSON、额外字段、动态代码、越权工具、Ground Truth 请求、重复动作、超预算动作、伪造 `CONFIRMED_VIOLATION`、无限继续、恶意规则内指令。每项都应被安全拒绝或进入明确失败状态。

在以下位置注入崩溃并重启 Worker：Checkpoint 前后、Sandbox 请求已成功但响应丢失、Oracle 后持久化前。验证 Receipt 查询、CAS 状态更新和唯一约束防止重复副作用/反例。

## 4. 亲自执行

执行阶段测试、全量回归、架构/安全搜索。若真实模型密钥已正常配置，可运行一个有严格预算的烟雾 Case；没有密钥不得降级为假成功，也不阻断纯确定性阶段验收。

全文搜索模型 SDK、`eval/exec`、Shell/文件/DB 工具、`ground_truth`、Profile 标记和 outcome 写入点，列出所有命中及判定。

## 5. 严重级别

- P0：Agent 可设置 confirmed/outcome；Ground Truth 泄漏；越权工具；重复 Worker 造成真实双写；动态代码执行；策略互泄完整 Context。
- P1：歧义自动通过；预算只记录不执行；恢复丢状态；SSE 被当权威；存在 LLM Adapter 旁路。
- P2：Prompt 可读性、非核心 Trace 字段、性能和开发体验。

## 6. 通过标准

| 维度 | 必须达到 |
| --- | --- |
| Compiler | 严格 Schema + deterministic validation + 人工确认 |
| Control | 显式状态机、CAS、幂等、恢复、取消 |
| Agents | 三策略隔离、结构化提议、最小权限 |
| Safety | 注入与越权负例全被阻断 |
| Truth | confirmed 唯一来源是重放后的 Oracle |

## 7. 输出模板

```text
结论：PASS | CONDITIONAL PASS | FAIL
审查范围与模型配置
亲自执行的命令、故障注入和负例
Findings：P0/P1/P2
状态机/恢复/幂等验收矩阵
工具权限与 Ground Truth 泄漏结论
真实模型烟雾结果（如执行）
是否允许进入阶段 4
```
