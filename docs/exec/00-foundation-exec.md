# 阶段 0 执行 Spec：工程骨架与核心契约

状态：待执行  
前置条件：仅需本规格包  
阶段目标：建立可运行、可测试、边界清晰的 Monorepo，并冻结跨组件共享契约；本阶段不实现电商业务动作。

## 1. 给 Codex 的任务

你是 RuleArena 的实现工程师。只完成阶段 0。开始前完整阅读根目录 `README.md`、三份核心文档和本文件；先检查仓库、`git status`、现有配置与用户改动，再给出短计划。不得跨阶段实现 Sandbox 业务、Oracle、Agent 或 Web 产品页面。

## 2. 本阶段交付

1. 建立技术 Spec 规定的仓库结构。
2. 使用 `uv` 配置 Python 3.12 Workspace，使用 npm 配置 `frontend/`。
3. 建立 `control_api` 与 `commerce_sandbox` 两个最小 FastAPI 服务，均提供 `/healthz` 和 `/readyz`。
4. 建立 PostgreSQL、Redis、本地服务的 Docker Compose；Control 与 Sandbox 使用不同数据库角色和 Schema。
5. 建立 `packages/policy_schema` 与 `packages/domain_contracts`，冻结首批严格 Pydantic 契约。
6. 建立 SQLAlchemy 2、Alembic 的双 Schema 基线，不创建阶段 1 业务表。
7. 建立 Ruff、mypy、pytest、前端 lint/typecheck/build 和 CI。
8. 建立 `.env.example`、结构化日志、统一配置加载和根 README 启动说明。

## 3. 必须定义的契约

`policy_schema` 至少包含：

- `Money`：货币枚举与十进制定点金额，禁止 float。
- `RuleSpec`：`schema_version`、`scenario_type`、参与者、资产、规则、不变量和歧义列表。
- 三种 `ScenarioType`：`PROMOTION`、`REFUND_POINTS`、`MEMBERSHIP_ENTITLEMENT`。
- 规则和不变量使用可辨识联合类型；所有模型 `extra="forbid"`。
- 只接受声明式字段，不接受 Python、SQL、模板表达式或任意代码。

`domain_contracts` 至少包含：

- `BusinessAction` 可辨识联合类型和动作信封 `ActionRequest`。
- `ActionReceipt`、`BusinessEvent`、`StateSnapshot`。
- `RunId`、`ActorId`、`AssetId`、`IdempotencyKey` 等强类型 ID。
- API 错误信封和可稳定序列化的版本字段。

契约应能被 Simulator、Sandbox、Oracle 和 Runtime 共享，但不得包含任何漏洞开关、Ground Truth 或 Sandbox 实现细节。

## 4. 配置与隔离

- 缺失数据库 URL、Redis URL、内部令牌等必需配置时必须 fail fast。
- 开发环境可以从 `.env` 加载，生产环境不得有硬编码默认密钥。
- Control 角色只能访问 `control` Schema；Sandbox 角色只能访问 `sandbox` Schema。
- `/healthz` 仅代表进程存活；`/readyz` 必须实际检查依赖。
- Sandbox 的内部 API 预留内部令牌配置，但本阶段只开放健康接口。

## 5. 推荐实施顺序

1. 盘点仓库与版本，记录不兼容项。
2. 先写契约验证、配置失败和健康检查测试。
3. 建立 Python Workspace 与服务入口。
4. 建立前端空壳，仅验证 Vite/TypeScript 构建。
5. 建立数据库角色、Schema 与 Alembic 基线。
6. 建立 Docker Compose 和 CI。
7. 从全新环境执行安装、迁移、启动和全部检查。

## 6. 必须通过的测试

- 合法 RuleSpec 可 round-trip；未知字段、float 金额、负金额、非法枚举和代码表达式被拒绝。
- ActionRequest 和 Receipt 可稳定 JSON 序列化，金额精度不丢失。
- 必需配置缺失时进程启动失败，并给出可定位错误。
- 两个服务的存活与就绪语义不同且测试覆盖。
- 数据库测试证明两个运行角色不能访问对方 Schema。
- 全新 clone 按 README 可以安装、迁移和启动。

建议验证命令，若仓库脚本不同可等价替换并说明：

```bash
uv sync --all-groups
uv run ruff check .
uv run mypy services packages
uv run pytest -q
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run build
docker compose config
docker compose up -d --build
```

## 7. 阶段验收门

| 验收项 | 通过条件 |
| --- | --- |
| 可复现构建 | 后端锁文件、前端锁文件存在，干净环境安装成功 |
| 契约严格性 | 无未知字段吞噬、无 float、无动态代码入口 |
| 服务边界 | Control/Sandbox 可独立启动，依赖方向不反转 |
| 数据隔离 | 两个角色的跨 Schema 访问被数据库拒绝 |
| 质量门 | lint、类型、测试、构建全部通过 |
| 范围控制 | 未出现业务动作、Agent、Oracle 或成品 UI |

## 8. 停止条件

若现有仓库与 Spec 有冲突、关键工具版本无法满足、用户改动会被覆盖，或数据库权限无法在当前环境验证，停止并报告，不擅自改目标。不得用 SQLite 或内存假实现冒充 PostgreSQL 隔离验收。

## 9. 完成报告格式

按根 README 的六项要求报告；额外附上：契约清单、依赖图、数据库权限验证证据、全新环境启动步骤。未经授权不 commit、不 push。
