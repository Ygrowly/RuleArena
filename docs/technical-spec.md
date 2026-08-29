# RuleArena 技术 Spec v0.1

状态：已确认  
运行时：Python 3.12、Node.js 20+  
部署目标：Docker Compose 本地 + Railway 在线

## 1. 架构原则

1. Workflow 控制有限生命周期，Agent 只负责未知路径探索。
2. Agent 提议动作，结构化工具执行，Oracle 裁决。
3. 模型搜索和 API 重放分层，候选不能直接升级为漏洞。
4. Control、Sandbox、Oracle、Ground Truth 按权限和数据路径隔离。
5. PostgreSQL 保存权威状态，Redis 不承担最终事实。
6. Trace、版本和评测从第一阶段开始建设。
7. 不使用 LangGraph 隐藏核心 Runtime；可在文档中解释映射与取舍。

## 2. 逻辑组件

### Web

React、TypeScript、Vite、React Flow。负责模板、RuleSpec 确认、Arena、状态 Diff、Trace 和版本回归。

### Control API

FastAPI。负责 Policy、RuleVersion、AttackRun、Counterexample、Benchmark 和 SSE。

### Attack Worker

ARQ + Redis。负责确定性基线、三策略 Agent、Checkpoint、候选重放和反例最小化。

### Reference Simulator

纯 Python 状态转换，用于快速搜索。其转换逻辑与 Commerce Sandbox 实现分离，只共享 Schema、值对象和动作契约。

### Commerce Sandbox

独立 FastAPI 服务和数据库角色。通过真实 HTTP API 执行业务动作、事务、幂等、事件账本和快照。

### Oracle

纯确定性代码，从 RuleVersion、状态快照、ActionReceipt 和 BusinessEvent 检查不变量。

### Evaluation

运行 Random、BFS、Single Agent、Multi-strategy，计算发现率、误报、重放稳定性、耗时和 Token。

## 3. 物理部署

    Railway Project
    ├── web-control-api
    ├── attack-worker
    ├── commerce-sandbox
    ├── PostgreSQL
    └── Redis

React 构建产物由 Control API 提供。Sandbox 只开放内部网络。

PostgreSQL 使用 control 和 sandbox 两个 Schema 及不同角色：

- rulearena_control：只能访问 control。
- rulearena_sandbox：只能访问 sandbox。
- 迁移角色仅用于部署，不提供给 Agent Runtime。

## 4. 仓库结构

    rulearena/
    ├── frontend/
    ├── services/
    │   ├── control_api/
    │   ├── attack_worker/
    │   └── commerce_sandbox/
    ├── packages/
    │   ├── policy_schema/
    │   ├── domain_contracts/
    │   ├── attack_runtime/
    │   ├── reference_simulator/
    │   ├── oracle/
    │   └── observability/
    ├── benchmarks/
    │   ├── development/
    │   └── hidden/
    ├── tests/
    ├── docker-compose.yml
    ├── pyproject.toml
    └── README.md

使用 uv 管理 Python 依赖，npm 管理前端依赖。使用 SQLAlchemy 2、Alembic、Pydantic v2、pytest、Ruff。

## 5. Control API

| Method | Path | 说明 |
| --- | --- | --- |
| POST | /api/policies/compile | 编译自然语言规则 |
| POST | /api/policies/{id}/confirm | 确认歧义并冻结版本 |
| GET | /api/policies/{id} | 查看规则和版本 |
| POST | /api/runs | 创建攻击运行 |
| GET | /api/runs/{id} | 查询权威状态 |
| GET | /api/runs/{id}/events | SSE 进度 |
| POST | /api/runs/{id}/cancel | 请求取消 |
| GET | /api/runs/{id}/counterexamples | 查询反例 |
| POST | /api/counterexamples/{id}/replay | 独立重放 |
| POST | /api/counterexamples/{id}/minimize | 最小化 |
| POST | /api/regressions | 运行历史反例 |
| GET | /api/benchmarks/latest | 最新评测结果 |

创建 Policy、Run、Replay 等写接口支持 Idempotency-Key。

## 6. Sandbox 内部 API

| Method | Path | 说明 |
| --- | --- | --- |
| POST | /internal/runs | 创建隔离测试空间 |
| POST | /internal/runs/{id}/reset | 恢复到 Scenario 初始状态 |
| POST | /internal/runs/{id}/actions | 执行业务动作 |
| GET | /internal/runs/{id}/snapshot | 规范化状态快照 |
| GET | /internal/runs/{id}/events | 业务事件 |
| GET | /internal/runs/{id}/receipts/{key} | 查询幂等动作结果 |

写动作请求：

    {
      "action": "refund_order",
      "actor_id": "user-1",
      "target_id": "order-1",
      "arguments": {"amount": "100.00"},
      "idempotency_key": "run-1:strategy-2:step-7"
    }

## 7. Rule Compiler

输入为模板和中文自然语言修改，输出严格 Pydantic RuleSpec。

流程：

1. LLM 生成候选结构。
2. Pydantic 拒绝未知字段、非法枚举、负金额和不支持表达。
3. Deterministic Validator 检查规则之间的引用和范围。
4. 发现歧义时返回 NEEDS_CONFIRMATION。
5. 用户确认后生成不可变 RuleVersion。

不允许自动生成可执行 Python、SQL 或动态表达式。

## 8. Runtime 状态机

    DRAFT
    → NEEDS_CONFIRMATION
    → READY
    → SEARCHING
    → REPLAYING
    → COMPLETED

状态变更使用数据库条件更新，避免重复 Worker 覆盖：

    update attack_run
    set status = next
    where id = run_id and status = expected

Outcome 单独保存，不能由 Agent 文本决定。

## 9. 搜索流程

1. 加载冻结版本与 Scenario。
2. 运行确定性规则检查和 BFS。
3. 启动三个隔离 StrategyRun。
4. 每个 Agent 只获得当前状态摘要、合法动作 Schema、有限历史和预算。
5. LLM 输出结构化 ActionProposal。
6. Runtime 校验动作、参数、预算和重复状态。
7. Reference Simulator 执行动作并计算 state_hash。
8. Oracle 对模型状态做候选检查。
9. 可疑路径进入干净 Sandbox API 重放。
10. API ActionReceipt、事件和快照再次进入 Oracle。
11. 确认后用删除式 Delta Debugging 最小化。
12. 持久化 Counterexample、Trace、指标和回归资产。

## 10. 三种策略

### VALUE_FLOW

关注净支付、退款、优惠、积分、权益价值守恒。

### LIFECYCLE

关注订单、优惠券、会员和权益状态转换顺序。

### BOUNDARY

关注重复请求、部分退款、取消后重试、异常顺序和幂等。

三者不聊天，不共享完整 Context，只共享 RuleVersion、动作契约和已确认 Counterexample ID。

## 11. 状态空间控制

- 规范化 state_hash 去重。
- 默认最大深度 12。
- 跳过非法动作。
- 对无资产或状态变化的重复路径 Early Exit。
- 每策略独立 Beam/Frontier 和预算。
- 同类 Candidate 按 invariant_id 和最小动作数去重。
- 超出预算返回 NO_VIOLATION_WITHIN_BUDGET，不自动扩容。

## 12. Checkpoint 与恢复

每轮保存：

- Strategy status。
- Frontier。
- visited_state_hashes。
- 已用步骤、时间、Token。
- 已发现 Candidate。
- model_config_hash 和 prompt_version。

写动作超时：

1. 使用 idempotency_key 查询 Sandbox ActionReceipt。
2. Receipt 成功则继续。
3. Receipt 明确失败则按策略决定是否重试。
4. 无法确认则记录 ACTION_UNKNOWN 并中止该分支。

## 13. 反例最小化

对确认路径逐段删除动作并从干净 Sandbox 重放。删除后仍违反相同 invariant 才接受删除。

MVP 的最短含义是当前路径删除空间中的 1-minimal，不宣称全局最短。

## 14. Trace 与可观测

层级：

    AttackRun
    ├── StrategyRun
    │   ├── LLM Call
    │   ├── Simulation
    │   └── Action Proposal
    ├── Sandbox Replay
    │   ├── HTTP Action
    │   └── Snapshot
    └── Oracle Check

字段至少包括：

- run_id、strategy_id、step_id。
- model、prompt_version、RuleVersion。
- action、参数摘要、工具结果摘要。
- before_state_hash、after_state_hash。
- latency_ms、input_tokens、output_tokens、cost。
- retry_count、status、error_type。

敏感输入不原样记录；使用摘要、Hash 或字段白名单。

## 15. 评测

Benchmark 固定保存：

- benchmark_version。
- runtime_version。
- RuleVersion、ScenarioVersion、SandboxVersion、OracleVersion。
- model_config_hash、prompt_version。
- baseline。
- random_seed 和预算。

指标：

- RuleSpec Schema 通过率。
- 已知漏洞发现率。
- 正常场景确认误报率。
- Candidate API 确认率。
- Confirmed Counterexample 连续重放率。
- 平均/分位运行时间。
- 平均搜索步数。
- 每个确认漏洞 Token 与成本。
- pass@k 和 pass^k。

开发集 16 Case，隐藏验证集 8 Case。Ground Truth 只存在 Evaluation 访问路径。

## 16. 安全边界

- Agent 无 SQL、文件、Shell 和数据库工具。
- Sandbox 无公网入口，内部请求使用服务令牌。
- RuleSpec extra fields forbid。
- 用户规则视为不可信文本，不得改变系统指令。
- Ground Truth、漏洞 profile 和隐藏集不进入 Prompt、Trace 或公开 API。
- LLM 密钥只存在服务端环境变量。
- 公共 Demo 按 IP/Session 限流。
- 高风险错误 fail closed。

## 17. 测试与 CI

### 单元

RuleSpec、值对象、状态转换、金额计算、不变量、state_hash、最小化算法。

### 集成

Control → Redis → Worker、Worker → Sandbox、Sandbox → PostgreSQL、幂等 Receipt、超时恢复、SSE 顺序。

### Golden

PR 运行无真实 LLM 的确定性子集；按需运行 LLM Benchmark；发布前运行完整 24 Case。

CI 必须运行：

    uv run ruff check .
    uv run pytest
    npm run build
    npm run test
    docker compose config

## 18. 健康与发布

- /health：进程存活。
- /ready：PostgreSQL、Redis、Sandbox 可用。
- Worker 心跳独立检查。
- 数据库迁移先于应用启动。
- Railway 仅公开 Web/Control API。

## 19. 明确不实现

- LangGraph、CrewAI、AutoGen 作为核心 Runtime。
- RAG、向量数据库和通用 Memory。
- RabbitMQ、MinIO、Kubernetes。
- 真实支付、电商平台 API 和浏览器自动化。
- Agent 间自由聊天。
- 自动修改业务实现。

## 20. 版本验收

MVP 只有在以下事实同时成立时完成：

1. 24 Case 评测集可复现。
2. 正常 Case 没有 Confirmed 误报。
3. Confirmed Counterexample 连续重放 3/3。
4. 隐藏漏洞发现率达到 75%。
5. 历史 P0 反例回归 100%。
6. Ground Truth 无泄漏。
7. 在线 Demo 完成 Vulnerable → Attack → Confirm → Fixed → Regression 主线。
8. 所有指标来自实际运行，不填写虚构数字。

