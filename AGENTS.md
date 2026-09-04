# RuleArena AI Coding Instructions

本文件供代码 Agent 自动读取。详细背景和规则见同目录正式文档。

## 必须阅读

执行实现前依次阅读：

1. `README.md`
2. `01-product-requirements.md`
3. `02-domain-model.md`
4. `03-technical-spec.md`
5. `05-development-guide.md`
6. 当前阶段 `phases/*-exec.md`
7. 上一阶段 Review 结论和当前仓库测试

执行独立审查前另读：

1. `04-project-overview.md`
2. `06-review-guide.md`
3. 当前阶段 `phases/*-review.md`

如果本目录不是仓库根目录，将本文件复制到仓库根目录，并保持上述文档路径可访问。

## 权威顺序

当前用户要求 > 三份核心 Spec > 当前阶段 Exec Spec > 已确认 Review > 开发说明 > README/注释。

发现核心 Spec 冲突、需求越界或 P0/P1 前置问题时停止并报告，不自行猜测。

## 项目不变量

- 最小价值单位是经过 Sandbox API 重放和 Oracle 确认的 Counterexample，不是 Agent 报告。
- Agent 只提议结构化动作；Workflow 控制状态、预算、权限和停止条件。
- `COMPLETED` 与业务 Outcome 分离。
- Simulator、Sandbox、Oracle 和 Evaluation/Ground Truth 必须隔离。
- PostgreSQL 是权威状态；Redis 和 SSE 不是事实源。
- 模型输出不可信，必须通过严格 Schema 和领域校验。
- Candidate 不能直接升级为 Confirmed Finding。
- 写动作必须具备业务幂等键、Receipt、后置查询和 unknown 语义。
- 指标只能从原始 Run 重算；目标和估算不能冒充实测。

## 固定范围

MVP 只支持：

- 优惠券；
- 退款与积分；
- 次数型会员权益；
- Random/BFS/Single/Multi 搜索；
- Reference Simulator、Commerce Sandbox、Oracle、Replay、最小化、Trace、Eval 和在线 Demo。

未经 Spec 变更，不添加：

- LangGraph/CrewAI/AutoGen 核心 Runtime；
- RAG、向量数据库、通用 Memory；
- Agent 间自由聊天或通用 DAG；
- RabbitMQ、MinIO、Kubernetes；
- 真实支付、浏览器自动化、完整商城；
- 任意行业自由建模或自动修改业务代码。

## 开发规则

1. 开始前检查 `git status`、当前分支、已有测试和上一 Review。
2. 使用 `rg`/`rg --files` 搜索，先理解现有实现再修改。
3. 先写失败测试或最小复现，再实现。
4. 异常尽早暴露，不用 fallback、空结果或旧数据伪装成功。
5. 不捕获并吞掉未知异常；边界层统一转换稳定错误契约。
6. 金额使用 Decimal 和币种，不使用 float。
7. 数据访问包含 `run_id` 隔离；状态更新使用 expected status/version 条件。
8. Agent 无 Shell、SQL、文件、数据库或 Ground Truth 工具。
9. 不为未来假设需求创建通用抽象；出现真实重复和替换需求后再抽象。
10. 保护用户现有改动；禁止 destructive git；未经授权不 commit、push、部署或产生费用。

## 写动作超时

```text
查询相同 idempotency_key 的 Receipt
├── 成功：读取权威状态后继续
├── 明确失败：按错误类别进行有界重试
└── 不确定：记录 ACTION_UNKNOWN，停止当前分支
```

不得把 ToolCallID 当业务幂等键，不得在未知状态下更换 key 重试。

## 测试与证据

按当前阶段运行等价检查：

```bash
uv run ruff check .
uv run pytest
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
docker compose config
```

只报告实际执行结果。真实模型、外部依赖或部署无法验证时标记 `NOT RUN/NOT VERIFIED`。

## 完成报告

必须包含：

- 修改文件；
- 关键实现与取舍；
- 实际命令和结果；
- 阶段验收矩阵；
- `IMPLEMENTED/MEASURED/TARGET/NOT VERIFIED` 事实分类；
- 未完成项和 P0/P1/P2 风险；
- 供独立 Reviewer 使用的 commit/diff 范围。

## Review 模式

默认只读，不顺手修复。亲自运行必要测试，从代码和原始 Run 复算，不相信汇总数字。按 P0/P1/P2 输出 Finding；P0 必须阻断，P1 修复或经用户明确接受后才能继续，`NOT VERIFIED` 不得视为通过。

