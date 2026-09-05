# 安全模型

## 资产

| 资产 | 位置 | 保护 |
| --- | --- | --- |
| LLM API Key | `.env`（部署侧） | 不进 Trace/日志（Trace schema 拒绝 api_key/authorization）；不入库 |
| Ground Truth（hidden 答案） | 部署侧私有路径（`RULEARENA_HIDDEN_SUITE_PATH`） | 仅 `RULEARENA_PROCESS_ROLE=evaluation` 可加载；公共仓库只有无答案 manifest |
| 内部服务令牌 | `.env` | Sandbox/Worker 间共享；Sandbox 校验 Bearer |
| 数据库凭据 | `.env` | control/sandbox 角色分离，跨 Schema 拒绝访问（有测试） |
| 冻结案例 JSON | `frontend/public/frozen/` | 只含已确认反例与非敏感证据，导出脚本记录诚实声明 |

## 信任边界

```
不可信: 规则文本、状态字段、模型输出
半可信: Reference Simulator（进程内，无副作用）
可信:   Commerce Sandbox（真实事务）、Deterministic Oracle、PostgreSQL、Workflow 代码
```

- 模型输出只能以 `extra=forbid` 的 Proposal 进入系统；动作白名单、参数范围、
  重复与预算校验在 Runtime 执行（`agents.validate_action_proposal`）。
- Agent 没有工具通道：无 Shell/SQL/文件/数据库/Ground Truth 访问路径。
- Prompt injection 防护是结构化的：规则文本包在 `<UNTRUSTED_RULE>` 中、
  Context 键 denylist + 递归 `assert_safe`（含 rule_spec）、系统提示为第二层。

## Ground Truth 泄漏防护（纵深）

1. 架构隔离：`attack_runtime` 不依赖 `evaluation`；Runtime 无读取路径。
2. 入口权限：hidden 载荷需要 evaluation 角色与显式私有路径。
3. Trace Schema 拒绝敏感键与标记（`observability.trace`）。
4. 评测侧出口扫描：`scan_ground_truth_leakage`（指纹 + 标记）在 Benchmark
   Runner 与 Agent 模型边界 fail-closed。
5. 公共出口扫描：Control API 对 SSE 事件与 Trace 响应做标记扫描
   （`scan_forbidden_markers`），命中即阻断/剔除。
6. 公共 API 聚合剥离 run/case 级证据（`public_metric_summary`）。

## 公共成本攻击

- Live Run 预算硬上限：12 步 / 12k tokens / $1.5 / 90 秒（Worker 强制，非 UI 约束）。
- `POST /api/runs` 按 IP 固定窗口限流（默认 10 次 / 5 分钟，429 响应）。
- 单次 LLM 调用带 `max_tokens`（剩余 token 预算）；cost 预算按 token 定价估算
  （`LLM_INPUT/OUTPUT_COST_PER_MTOKEN`）或 provider 自报。
- LLM 不可用/未配置时 fail-closed（`UnavailableLLMAdapter`），不降级为假成功。

## 公开端点与部署

| 端点 | 暴露 | 说明 |
| --- | --- | --- |
| Web（nginx:8080） | 公开 | 静态资源 + `/api` 反代 |
| Control API | 经 Web 反代 | 编译/确认/运行/SSE；管理/评测内部端点不公开 |
| Commerce Sandbox | **仅私网** | 公共部署不暴露端口；Bearer 令牌校验；公网不可达有 E2E 断言 |
| PostgreSQL / Redis | 仅私网 | 不公开 |

- CORS：`PUBLIC_ALLOWED_ORIGINS` 显式白名单；未配置即不开放跨域。
- 安全响应头：`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`、
  `Cache-Control: no-store`。
- 迁移在服务启动前执行，失败阻断发布（compose `service_completed_successfully`）。
- 日志统一 JSON 且不含密钥/敏感原文；`/healthz` 存活、`/readyz` 依赖探测。

### Railway 部署拓扑（未经授权不执行）

Web（公开）+ Control（公开）+ Worker + Sandbox（私网）+ PostgreSQL + Redis。
仓库不含云凭据；执行部署需要用户明确授权。

## 已知限制

- Worker 心跳面板未实现（ARQ 原生 health check 可用但未接入 UI）。
- 公共 Demo 无账户体系；限流是 IP 维度，共享出口会互相影响。
- `benchmark verify` 依赖部署侧 hidden 资产与真实模型凭据，当前为 NOT VERIFIED。
