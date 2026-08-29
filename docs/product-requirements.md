# RuleArena 产品需求文档 v0.1

状态：已确认  
产品类型：AI 应用 / Python 后端 / 业务质量平台  
MVP 周期：约 14 天  
首发形态：公开在线 Demo + 开源仓库

## 1. 背景

促销、退款、积分和会员权益通常分别由产品、交易、营销、会员、测试与风控团队设计和实现。单条规则容易理解，真实风险却经常来自多条规则在特定操作顺序、重复请求、部分退款和状态恢复中的组合。

传统单元测试和人工业务用例擅长验证已知路径，但难以持续枚举长尾操作组合。单纯让 LLM 阅读 PRD 又只能给出可能风险，无法证明风险能够在系统中复现。

RuleArena 需要把自然语言规则、Agent 探索、真实 API 执行和确定性判定组合成一条可审计闭环。

## 2. 产品定位

RuleArena 将自然语言业务规则转换为经人工确认的 RuleSpec，由攻击策略 Agent 搜索可能到达非法状态的动作序列，再在隔离 Commerce Sandbox 中用真实 API 重放，最后通过独立 Oracle 检查业务不变量。

系统主要回答：

1. 一系列合法动作能否到达非法业务状态。
2. 最短可复现路径是什么。
3. 问题来自规则冲突还是实现偏离。
4. 修复后旧漏洞是否消失，正常路径是否回退。

## 3. 目标用户与 Job to Be Done

| 用户 | 核心任务 |
| --- | --- |
| 产品经理 | 在规则发布前暴露歧义、冲突和缺失条件 |
| 测试工程师 | 自动发现并固化长尾业务用例 |
| 后端开发 | 获得可重放的 API、事件和状态证据 |
| 交易风控 | 搜索套利、重复领取、权益残留和退款异常 |
| 营销/会员运营 | 验证活动组合和权益生命周期 |

核心任务：

当一组电商规则准备上线或变更时，用户希望在不修改生产数据的前提下，自动搜索并验证可能造成资金或权益异常的操作路径，将确认问题沉淀为长期回归资产。

## 4. MVP 业务范围

### 4.1 支持

- 新用户或活动优惠券。
- 满额发放积分。
- 积分兑换权益。
- 部分退款和全额退款。
- 订单取消与优惠券恢复。
- 会员购买、次数型权益发放、消费、撤销与退款。
- 重复请求与幂等。
- 固定规则版本下的确定性重放。

### 4.2 不支持

- 任意产品方案、市场、商业模式或 UX 审查。
- 库存、物流、商家结算、真实支付渠道。
- 真实时间调度、复杂并发和分布式事务竞争。
- 任意行业的自由建模。
- 自动修改业务代码和自动发布。
- 形式化证明系统安全。

## 5. 核心用户流程

1. 用户选择预置规则模板。
2. 用户用自然语言修改活动、退款、积分或会员规则。
3. Rule Compiler 输出类型化 RuleSpec 和歧义清单。
4. 用户确认或修改歧义，生成不可变 RuleVersion。
5. 系统先运行确定性规则检查和 BFS 基线。
6. Orchestrator 启动 ValueFlow、Lifecycle、Boundary 三个隔离策略。
7. Agent 在参考模型中提出结构化动作并探索候选路径。
8. 可疑路径从干净场景开始调用 Commerce Sandbox API 重放。
9. Oracle 根据独立不变量检查真实状态与事件。
10. 确认问题被最小化为 Counterexample。
11. 用户切换 Fixed 版本或修改规则并重新回归。
12. Trace、Counterexample、指标与回归结果被保存和展示。

## 6. 产品状态与结果语义

Run 生命周期：

    DRAFT
    → NEEDS_CONFIRMATION
    → READY
    → SEARCHING
    → REPLAYING
    → COMPLETED

业务 Outcome 必须与运行完成分开：

| Outcome | 含义 |
| --- | --- |
| CONFIRMED_VIOLATION | API 重放成功且违反不变量 |
| UNCONFIRMED_CANDIDATE | 模型预测风险，但真实 API 无法复现 |
| NO_VIOLATION_WITHIN_BUDGET | 指定深度、时间和 Token 预算内未发现 |
| AMBIGUOUS_POLICY | 规则含义不足以建立确定性行为 |
| UNSUPPORTED_RULE | 超出 MVP 领域模型 |
| INFRA_FAILED | 模型、Worker、数据库或 Sandbox 失败 |
| CANCELLED | 用户主动取消 |

禁止把 COMPLETED 等同于成功，也禁止把 NO_VIOLATION_WITHIN_BUDGET 表述为安全证明。

## 7. 功能需求

### P0

| ID | 能力 | 验收 |
| --- | --- | --- |
| FR-01 | 预置模板 | 覆盖优惠、退款积分、会员权益三类 |
| FR-02 | 规则编译 | 中文输入输出严格 RuleSpec |
| FR-03 | 歧义确认 | 未确认 RuleVersion 不能运行 |
| FR-04 | 规则版本 | 修改产生新版本，历史运行绑定旧版本 |
| FR-05 | Commerce Sandbox | 真实 API、事务、账本和状态快照 |
| FR-06 | 确定性基线 | Random/BFS 可独立运行 |
| FR-07 | 三策略搜索 | 隔离 Context、预算和 Trace |
| FR-08 | 候选重放 | 候选必须从干净场景 API 重放 |
| FR-09 | Oracle | 代码检查金额、积分、优惠和权益不变量 |
| FR-10 | 反例最小化 | 删除冗余动作后仍可连续重放 |
| FR-11 | 状态 Diff | 每步展示金额、资产、状态和事件变化 |
| FR-12 | 修复回归 | Vulnerable 与 Fixed 可对比 |
| FR-13 | Trace | 模型、工具、状态、耗时、Token、错误可查 |
| FR-14 | Golden Set | 24 个开发/隐藏正常与异常 Case |
| FR-15 | 在线 Demo | 可直接查看案例并限额实时运行 |

### P1

- 自动生成候选修复规则，必须人工确认。
- RuleSpec 版本 Diff。
- Counterexample 导出 pytest。
- Benchmark 历史趋势。
- OpenTelemetry 导出。

### P2

- 接入开源电商或企业 Staging。
- 时间与真正并发故障。
- 游戏、SaaS、审批领域包。
- 自动策略学习与长期记忆。

## 8. 非功能需求

### 可复现

同一 RuleVersion、ScenarioVersion、SandboxVersion、OracleVersion、seed 和动作序列必须得到同一确定性结果。

### 隔离

每个 run_id 使用独立业务数据。Agent、Oracle、Ground Truth 和 Sandbox 实现不得共用不应共享的权限与数据。

### 持久化

权威状态存在 PostgreSQL；Redis 只承担队列、限流和短期进度。SSE 只负责通知，不作为完成事实。

### 失败透明

不吞异常，不以空结果伪装成功。写操作超时后先查询 action receipt，再决定是否重试；不能确认时记录 ACTION_UNKNOWN。

### 成本边界

默认最大深度 12、最大并行策略 3、单次公开运行 90 秒。Token、步骤、时间和模型调用次数均可配置并记录。

### 安全

LLM 输出只能进入 Pydantic Schema；Agent 不可执行代码、SQL、访问 Ground Truth 或直接写数据库。Sandbox 仅内部访问。

## 9. MVP 质量门禁

| 指标 | 门禁 |
| --- | --- |
| Golden Set | 至少 24 Case |
| 正常场景确认误报 | 0 |
| Confirmed Counterexample | 同版本连续重放 3/3 成功 |
| 隐藏漏洞发现率 | 不低于 75% |
| 历史 P0 反例 | 回归 100% 通过 |
| Ground Truth 泄漏 | 0 |
| 公开运行 | 默认不超过 90 秒 |

多策略搜索必须与 Random、BFS、Single Agent 做消融。如果没有带来发现率或成本优势，不在产品和简历中强调多 Agent。

## 10. 黄金演示

规则：

- 新用户获得 50 元优惠券。
- 实付满 100 元发放 100 积分。
- 全额退款恢复优惠券。
- 100 积分可兑换 20 元券。

Vulnerable v1 在全额退款时恢复优惠券但未撤销积分。攻击路径完成支付、积分发放、全额退款、优惠券恢复和积分兑换后，Oracle 检测用户已取回全部实付却仍保留额外权益。

Fixed v2 撤销订单积分并按明确政策处理优惠券。旧路径应无法复现，正常退款路径仍然通过。

## 11. 成功标准

MVP 成功不是功能数量，而是完成一条可信闭环：

    自然语言规则
    → 可确认 RuleSpec
    → Agent 发现候选
    → API 黑盒重放
    → Oracle 确认
    → 最短反例
    → 修复回归
    → 在线演示与评测证据

