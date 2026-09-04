# RuleArena 开发说明 v0.1

状态：AI Coding 长期执行规范  
适用对象：Codex、Claude Code、其他代码 Agent 与人工开发者  
当前已知进度：用户说明项目已进入阶段 4；开始任务时必须以仓库现状和最近 Review 为准，不得长期相信本文中的进度描述

---

## 0. 文档权威顺序

发生冲突时按以下顺序处理：

1. 用户在当前会话中的明确要求；
2. 已确认的 `01-product-requirements.md`、`02-domain-model.md`、`03-technical-spec.md`；
3. 当前阶段 `phases/*-exec.md`；
4. 最近一次已确认的阶段 Review 结论；
5. 本开发说明；
6. 根 README、注释和其他辅助文档。

如果核心 Spec 之间互相冲突，停止实现并报告冲突，不自行选择更方便的一方。如果新需求会改变领域模型、系统边界、质量门禁或外部副作用，先修改和确认 Spec，再写代码。

---

## 1. 每个 AI Coding 会话的启动流程

开始修改代码前必须完成：

1. 阅读规格包 README、三份核心 Spec、本说明和当前阶段执行 Spec；
2. 阅读上一阶段 Review 结论和未关闭 Findings；
3. 执行只读检查：工作区状态、当前分支、最近提交、目录结构和已有测试；
4. 用 `rg` 定位现有实现、契约、测试和 TODO，禁止凭文件名猜测；
5. 区分用户已有改动和本次任务范围，保护无关脏工作区；
6. 先写出本次小步计划、影响模块、验收方式和明确不做项；
7. 若发现 P0/P1 前置问题，先报告并处理，不在错误基础上继续堆功能。

建议检查：

```bash
git status --short
git branch --show-current
git log -5 --oneline
rg --files
rg -n "TODO|FIXME|NotImplemented|pass$" .
```

不得自动执行 `git push`、创建云资源、修改线上数据或产生付费模型调用，除非用户明确授权。

---

## 2. 开发总原则

### 2.1 先证明问题存在，再实现修复

- 新能力先写失败测试或最小复现；
- Bug 修复必须有修复前失败、修复后通过的回归用例；
- 测试应验证业务结果和状态变化，不能只验证 HTTP 200 或函数被调用；
- 不为追求覆盖率编写没有判别力的测试。

### 2.2 失败应尽早、明确、可定位

- 结构或前置条件非法时立即拒绝；
- 不用空列表、默认对象、旧缓存或 LLM 文案伪装成功；
- 仅对明确的暂时性错误进行有界重试；
- 结果未知必须记录 `ACTION_UNKNOWN` 或对应失败语义；
- 业务层不吞异常，由边界层统一映射成稳定错误契约。

### 2.3 模型输出永远视为不可信输入

- 只能进入严格 Pydantic Schema；
- `extra="forbid"`；
- 校验枚举、金额、引用、前置条件和预算；
- 不执行模型生成的 Python、SQL、Shell、URL 或动态 import；
- Agent confidence、reason 和自然语言总结不参与最终裁决。

### 2.4 权威事实只能来自确定性系统

- PostgreSQL 保存运行、版本和 Sandbox 权威状态；
- Redis 只负责队列、短期进度、限流或可重建缓存；
- SSE 是通知通道，不是完成事实；
- 前端刷新后必须通过 Run API 恢复；
- 指标从原始持久化 Run 重算，不由前端或模型自报。

### 2.5 最小实现，拒绝无业务依据的抽象

除非当前阶段 Spec 要求，否则不引入：

- LangGraph、CrewAI、AutoGen 等核心 Runtime；
- RAG、向量数据库和通用 Memory；
- RabbitMQ、MinIO、Kubernetes；
- 通用 DAG、动态插件或 Agent 群聊；
- 真实支付、浏览器自动化和完整电商商城；
- 为未来假设需求设计的大型 Provider/Factory 层。

出现第三个真实实现、第三处稳定重复或明确替换需求后，再考虑抽象。不要为了展示设计模式而增加间接层。

---

## 3. 架构边界与依赖规则

### 3.1 模块职责

| 模块 | 可以依赖 | 禁止依赖 |
| --- | --- | --- |
| `policy_schema` | 值对象、Pydantic | Sandbox 实现、Ground Truth、前端 |
| `domain_contracts` | 标准库、值对象 | 数据库 Session、LLM SDK |
| `reference_simulator` | RuleSpec、Action Contract、纯领域值 | Sandbox ORM/服务实现、Ground Truth |
| `commerce_sandbox` | 独立领域服务、数据库、Action Contract | Agent Prompt、Evaluation Ground Truth |
| `oracle` | RuleVersion、规范化快照、Receipt、Event | Agent confidence、Sandbox 私有实现分支 |
| `attack_runtime` | 合法动作契约、Simulator、Oracle 接口、Sandbox Client | Sandbox DB、hidden loader、任意工具执行 |
| `observability` | 类型化 Trace 事件 | 反向控制业务流程 |
| `evaluation` | Runtime 公共入口、Ground Truth 私有入口、指标代码 | 向 Runtime 返回 expected path/profile |
| `frontend` | Control API 类型契约 | 直接读取数据库、客户端计算权威指标 |

### 3.2 四个必须分离的数据路径

1. **Policy**：用户规则、RuleSpec、RuleVersion；
2. **Execution**：Simulator 和 Sandbox 的动作、状态与事件；
3. **Verification**：Oracle 的不变量和 Evidence；
4. **Evaluation**：Case Ground Truth、隐藏期望和 Benchmark 裁决。

禁止：

- Simulator 直接调用 Sandbox 内部业务函数；
- Oracle 复用 Sandbox 中“是否有漏洞”的实现分支；
- Runtime 或 Prompt 加载 `ground_truth_ref`、expected path 或 vulnerable profile；
- 前端、SSE、异常栈或公共 Trace 暴露隐藏预期；
- 为了让测试通过，让目标实现和 Oracle 共享同一错误逻辑。

### 3.3 共享内容的最小集合

Simulator 与 Sandbox 只允许共享：

- Pydantic/Dataclass 动作契约；
- Decimal、Currency、ID 等值对象；
- 枚举和序列化协议；
- 规范化快照的公共 Schema。

状态转换、持久化和不变量实现必须独立。

---

## 4. 核心模块实现要求

### 4.1 Rule Compiler

流程必须显式：

```text
LLM 候选结构
→ Pydantic Schema
→ 领域 Validator
→ 歧义列表
→ 人工确认
→ 不可变 RuleVersion
```

要求：

- 未知字段、非法枚举、负金额、未知 invariant 和非法引用立即失败；
- 同一确认版本的内容不可原地覆盖，修改必须生成新版本；
- Prompt 版本、模型配置 Hash 和源文本 Hash 可追溯；
- 不支持的自由表达返回 `UNSUPPORTED_RULE`，不降级成随意文本执行；
- 歧义未确认时不能创建 AttackRun。

### 4.2 Commerce Sandbox

每个动作作为一个明确业务事务：

1. 校验内部令牌、run 隔离和请求 Schema；
2. 查询已有 Idempotency Receipt；
3. 校验聚合版本和业务前置条件；
4. 更新聚合与账本；
5. 追加 BusinessEvent；
6. 写入 ActionReceipt；
7. 同一事务提交；
8. 返回结构化状态和验证入口。

必须保证：

- 金额使用 Decimal 和明确币种；
- 退款总额、积分净额和权益次数可从账本重建；
- 数据查询全部包含 `run_id` 隔离；
- 相同 `run_id + action_type + idempotency_key` 只产生一次效果；
- HTTP 成功不等于业务成功，Receipt 必须区分 accepted/completed/rejected/unknown；
- Vulnerable/Fixed Profile 有明确版本，不能通过公共接口泄漏。

### 4.3 Reference Simulator

要求：

- 尽可能是纯函数：`next_state = transition(rule, state, action)`；
- 输入状态不可被原地污染；
- 状态哈希只包含影响后续行为的规范化字段；
- 时间、数据库主键、Trace ID 等非语义字段不能进入哈希；
- 合法动作和前置条件必须有单元测试；
- Simulator 发现只生成 Candidate，不生成 Confirmed Finding。

### 4.4 Oracle

Oracle 输入只包括：

- 冻结 RuleVersion；
- 规范化前后快照；
- ActionReceipt；
- BusinessEvent；
- OracleVersion。

Oracle 输出包括：

- invariant_id；
- passed/failed；
- 可机器读取的 EvidenceRef；
- 期望值、实际值和差异；
- classification 和 oracle_version。

不允许：

- 用 Agent reason 或 confidence 判违规；
- 依赖自然语言模糊比较完成资金裁决；
- 在找不到证据时给出 Confirmed；
- 修改 Sandbox 状态；
- 根据 Ground Truth 直接返回答案。

### 4.5 Attack Runtime

Runtime 使用显式状态机和条件更新：

```text
DRAFT → NEEDS_CONFIRMATION → READY → SEARCHING → REPLAYING → COMPLETED
```

要求：

- Outcome 与生命周期状态分开；
- Worker 获取任务后验证 expected status，重复消费保持幂等；
- 三策略 Context、frontier、visited states、预算和 Trace 隔离；
- Agent 只获得当前状态摘要、合法动作、有限历史和预算；
- 每次 ActionProposal 经 Schema、权限、状态、预算和重复校验；
- 超时、取消和预算耗尽都保存稳定 Checkpoint；
- 已确认反例 ID 可用于去重，但不得向策略泄漏路径和 Ground Truth。

### 4.6 Replay 与最小化

- 每次重放创建干净 Sandbox run；
- 固定 Rule/Scenario/Sandbox/Oracle 版本和 seed；
- 每一步保存请求、Receipt、Event、before/after Hash；
- 只有违反同一 invariant 才算重放成功；
- Confirmed 反例发布前连续重放 3/3；
- 删除动作后必须重新从干净环境执行；
- MVP 只声称在删除空间中达到 1-minimal，不声称全局最短。

### 4.7 Evaluation

所有 Baseline 必须使用：

- 相同 Case 和版本元组；
- 明确记录的模型、Prompt、temperature、seed；
- 可比较的时间、步骤、Token 和调用预算；
- 相同的 Candidate → Replay → Oracle 确认机制。

指标必须由原始 Run 计算，分母为 0 时返回 N/A。`INFRA_FAILED`、`CANCELLED`、`NO_VIOLATION_WITHIN_BUDGET` 必须分别统计。

`pass@k` 表示 k 次中至少一次达到目标；`pass^k` 表示 k 次全部达到目标。两者必须用小型手算样例测试。

### 4.8 Observability

层级 Trace：

```text
AttackRun
├── StrategyRun
│   ├── LLMCall
│   ├── ActionProposal
│   └── SimulationStep
├── SandboxReplay
│   ├── HTTPAction
│   ├── Receipt
│   └── Snapshot
└── OracleCheck
```

最少记录：版本元组、strategy/step、模型配置、prompt_version、动作摘要、before/after hash、延迟、Token/cost、retry、status/error。

不记录完整密钥、隐藏期望、敏感规则原文和不受控 Chain-of-Thought。公开 Trace 使用字段白名单、摘要或 Hash。

### 4.9 Web

Web 围绕黄金旅程，不做管理后台功能墙：

1. 规则模板；
2. 自然语言/RuleSpec 双栏和歧义确认；
3. Arena 策略进度、预算和动作；
4. 最小反例、State Diff 和 Evidence Drawer；
5. Vulnerable/Fixed 回归对比。

要求：

- SSE 仅更新提示，页面使用 Run API 恢复权威状态；
- 所有写请求带 Idempotency-Key；
- 明确展示 ambiguous、unsupported、cancelled、infra_failed、unconfirmed 和 budget exhausted；
- 不展示模型思维链；
- Frozen Demo 必须来自真实持久化 Run，不能由前端硬编码伪造；
- 动画服务理解，必须尊重 reduced motion 和证据可读性。

---

## 5. 副作用、幂等、超时和恢复

### 5.1 工具契约

每个写工具必须定义：

- 输入/输出 Schema；
- 前置条件；
- 副作用；
- 权限；
- 幂等键范围；
- 是否允许安全重试；
- 后置验证方式；
- 明确的错误类型。

禁止只返回：

```json
{"success": true}
```

至少返回 action_id/idempotency_key、业务对象、接受状态、最终状态或查询入口、版本和错误语义。

### 5.2 超时决策

写动作超时后：

```text
查询相同 idempotency_key 的 Receipt
├── 已成功：读取权威状态后继续
├── 明确失败：在预算内按错误类别决定是否重试
└── 无法确认：记录 ACTION_UNKNOWN，停止当前分支
```

不得把 ToolCallID 当成业务幂等键。不得在结果未知时直接换新幂等键重试。

### 5.3 Checkpoint

Checkpoint 至少保存：

- 状态机位置；
- frontier 和 visited_state_hashes；
- 已使用步骤、时间、Token 和调用次数；
- 已发现 Candidate 引用；
- Rule/Scenario/Sandbox/Oracle 版本；
- model_config_hash 和 prompt_version。

恢复前重新读取权威 AttackRun，检查版本和 expected status。Checkpoint 不是业务事实的替代品。

---

## 6. 测试策略

### 6.1 单元测试

- RuleSpec Schema、歧义和领域 Validator；
- Decimal、金额、状态机和账本；
- 每条核心 invariant 的正反例；
- state_hash 等价与非等价；
- Delta Debugging；
- 指标公式与 N/A；
- pass@k/pass^k。

### 6.2 集成测试

- Control → Redis → Worker；
- Worker → Sandbox HTTP；
- Sandbox → PostgreSQL 事务和隔离；
- 幂等 Receipt；
- 写动作超时与权威查询；
- Worker 重启与 Checkpoint；
- SSE 断线和 API 恢复；
- Replay → Oracle → Counterexample。

### 6.3 安全与泄漏测试

- Runtime 无 hidden loader 路径；
- Ground Truth 标记进入 Prompt/Trace/API 时测试失败；
- Sandbox 内部令牌、数据库角色和公网边界；
- Prompt injection 不能改变 Action Schema 和系统边界；
- 跨 run_id 数据查询为 0；
- 日志、错误栈和前端资源不含密钥或 hidden expectation。

### 6.4 Golden 与 E2E

- PR：无真实 LLM 的契约、Oracle、确定性开发子集和历史 P0；
- 按需：真实模型完整 Benchmark；
- 发布前：24 Case、四 Baseline、最新版本 Gate；
- E2E：模板 → 确认 → Attack → Confirm → Minimize → Fixed → Regression。

### 6.5 失败注入

至少覆盖：

- Sandbox 在提交前/提交后超时；
- Worker 执行中重启；
- Redis 短暂不可用；
- SSE 中断和页面刷新；
- 重复点击创建 Run；
- 版本字段变化导致旧 Gate 拒绝；
- LLM 不可用或输出非法结构；
- 正常 Case 被错误标记时发布阻断。

---

## 7. 阶段 4 开发重点

进入阶段 4 时，优先级固定为：

1. 固化 16 development + 8 hidden Case；
2. 证明每个 Ground Truth 3/3 可重放；
3. 隔离 hidden loader、Prompt、Trace、API 和 CI 权限；
4. 实现 Random/BFS/Single/Multi 的公平运行；
5. 保存每个原始 Run 和完整版本元组；
6. 实现可独立重算的指标；
7. 建立层级 Trace 和 Counterexample 导航；
8. 建立当前版本 Release Gate；
9. 运行泄漏与口径测试；
10. 如实报告 Multi-strategy 的边际收益。

阶段 4 不提前进行大规模 Web 美化，不为满足指标删除 Case、修改答案、泄漏 Profile 或调整门禁。

---

## 8. CI 与质量检查

根据仓库脚本调整具体路径，但不能省略等价检查：

```bash
uv run ruff check .
uv run pytest
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
docker compose config
```

阶段 4 额外：

```bash
uv run pytest -q tests/evaluation tests/observability
uv run pytest -q tests/security/test_ground_truth_leakage.py
uv run pytest -q tests/regression
uv run rulearena benchmark --suite development --baselines random,bfs,single,multi
uv run rulearena benchmark verify --latest
```

不要声称执行了环境中无法运行的命令。缺少模型密钥时，将真实模型 Benchmark 标记为 `NOT RUN`，确定性测试仍需完成。

---

## 9. 部署要求

- Docker Compose 本地一条命令启动；
- Railway 只公开 Web/Control；
- Sandbox 只允许私网和内部令牌；
- 数据库迁移先于应用启动，失败阻断；
- `/healthz`、`/readyz` 和 Worker heartbeat 语义分开；
- 公共 Live Run 进行 IP + Session 限流和模型预算限制；
- Frozen Run 在模型/Worker 暂时不可用时仍可查看；
- 环境变量不进入前端构建产物；
- 未经用户授权不创建服务、不产生费用、不公开仓库。

---

## 10. 事实、指标和文档更新

所有公开数字必须记录：

- BenchmarkRun ID；
- benchmark/runtime/model/prompt 版本；
- Case 和重复次数；
- 预算和硬件/部署环境；
- 指标公式和失败口径；
- 生成命令和时间。

统一标签：

| 标签 | 含义 |
| --- | --- |
| `DESIGN` | 已确认设计，但不代表实现 |
| `IMPLEMENTED` | 代码存在且通过指定测试 |
| `MEASURED` | 来自可追溯实际 Run |
| `TARGET` | 验收目标 |
| `ESTIMATE` | 容量估算或 Mock 压测口径 |
| `NOT VERIFIED` | Reviewer 无法独立验证 |

README、在线 Demo、简历和面试材料不得把 `TARGET` 或 `ESTIMATE` 改写成 `MEASURED`。

---

## 11. 每次任务的完成报告

AI Coding 完成后必须返回：

```text
任务与阶段：
结论：COMPLETED | PARTIAL | BLOCKED

修改文件：
- path：修改目的

关键实现与取舍：
- 业务不变量/架构边界/替代方案

实际执行：
- command
- result

验收矩阵：
- 验收项：PASS | FAIL | NOT RUN，证据

事实标签：
- IMPLEMENTED：
- MEASURED：
- TARGET/NOT VERIFIED：

未完成项和风险：
- P0/P1/P2

供 Reviewer 使用：
- commit 或 diff 范围
- 建议重点审查位置
```

未经用户授权不自动 commit 或 push。若工作区包含用户未提交改动，说明本次改动与原改动的边界。

---

## 12. 开发前的最后检查

- 这项能力是否直接服务“搜索 → 重放 → 裁决 → 回归”闭环？
- 能否由简单 Workflow 完成，为什么需要 Agent？
- 确定性规则是否错误地放进 Prompt？
- 工具是否定义副作用、幂等、失败和后置验证？
- 是否在依赖模型文本判断业务事实？
- 是否破坏 Simulator/Sandbox/Oracle/Evaluation 隔离？
- 是否为尚不存在的扩展过度设计？
- 是否有失败测试、验收证据和可复现命令？
- 是否诚实区分设计目标与实际结果？

任一关键问题无法回答时，先停下来补充设计或测试。
