# RuleArena 规格包 v0.1

状态：已确认  
日期：2026-08-29  
目标：作为 RuleArena MVP 的唯一产品、领域、技术、实施与阶段验收依据。

## 项目定位

RuleArena 是面向电商促销、退款、积分与会员权益规则上线前验收的 AI 对抗式业务规则测试平台。

系统把自然语言规则转换为经人工确认的类型化 RuleSpec，由确定性 Workflow 调度三个隔离的攻击策略 Agent，在参考模型中搜索异常操作组合，再通过 Commerce Sandbox 的真实 HTTP API 与数据库进行黑盒重放，最后由独立 Oracle 判定是否违反业务不变量。确认漏洞必须沉淀为可复现 Counterexample 和回归用例。

最小价值单位不是风险报告，而是一条经过 API 重放、确定性验证、能够长期回归的业务反例。

## 文档索引

1. 01-product-requirements.md：产品需求、用户、范围与验收标准。
2. 02-domain-model.md：限界上下文、实体、状态、动作、事件与不变量。
3. 03-technical-spec.md：系统架构、Runtime、API、数据、评测、安全与部署。
4. phases/00-foundation-exec.md：项目骨架与契约。
5. phases/00-foundation-review.md：阶段 0 独立审查。
6. phases/01-commerce-sandbox-exec.md：Commerce Sandbox。
7. phases/01-commerce-sandbox-review.md：阶段 1 独立审查。
8. phases/02-oracle-simulator-exec.md：参考模拟器、Oracle、BFS 与重放。
9. phases/02-oracle-simulator-review.md：阶段 2 独立审查。
10. phases/03-agent-runtime-exec.md：Rule Compiler、Agent Runtime 与策略搜索。
11. phases/03-agent-runtime-review.md：阶段 3 独立审查。
12. phases/04-eval-observability-exec.md：Golden Set、评测与 Trace。
13. phases/04-eval-observability-review.md：阶段 4 独立审查。
14. phases/05-web-deployment-exec.md：在线 Demo 与部署。
15. phases/05-web-deployment-review.md：最终独立验收。

## Codex 执行规则

每次只执行一个阶段，不得跨阶段提前实现。

执行前必须：

1. 阅读本 README、三份核心文档和当前阶段执行 Spec。
2. 检查仓库现状、工作区改动、已有测试和上一阶段 Review 结论。
3. 把阶段目标拆成小步并声明当前计划。
4. 保护用户已有改动，不执行 destructive git 命令，不自动 push。

执行中必须：

- 先写能够暴露缺失能力或缺陷的测试，再实现。
- 确定性事实由代码负责，LLM 只处理规则理解和搜索策略。
- 发生异常立即失败并暴露原因，不用 fallback 伪装成功。
- 每个写操作必须考虑幂等、超时后的 unknown 和权威状态查询。
- 不加入当前阶段未要求的框架、中间件或通用抽象。
- 不把 Ground Truth、漏洞开关或 Oracle 结论暴露给 Agent。

阶段完成时必须返回：

1. 修改文件清单。
2. 关键设计及取舍。
3. 实际执行的测试/检查命令与结果。
4. 阶段验收矩阵。
5. 未完成项、风险和下一阶段前置条件。
6. 可供独立 Reviewer 使用的 commit/diff 范围；未经用户授权不得自动提交或推送。

## 独立 Review 规则

Reviewer 使用独立会话，默认只读，不修复代码。

Reviewer 必须阅读三份核心文档、对应 Review Spec、当前实现和测试证据，亲自运行必要命令，按 P0/P1/P2 输出 Findings。

- P0：安全、数据、确定性、隔离、Ground Truth 泄漏或核心链路错误，必须阻断。
- P1：阶段验收未达成、可靠性明显不足或设计与 Spec 偏离，修复或明确接受后才能继续。
- P2：不阻断当前阶段的优化项。

只有 P0 为 0，且所有 P1 已修复或由用户明确接受，下一阶段才能开始。

## 固定边界

- MVP 只支持优惠、退款积分、次数型会员权益。
- 输入采用内置模板加自然语言修改，不提供任意业务世界建模。
- 一个确定性 Orchestrator 加三个隔离策略 Agent，不做 Agent 间聊天。
- Commerce Sandbox 是真实可运行的测试业务服务，不接生产电商平台。
- 不使用 LangGraph 作为核心 Runtime。
- 不引入 RAG、向量数据库、真实支付、Kubernetes、完整账户组织后台。
- 只能声明有界预算内发现或未发现，不声称形式化证明安全。

