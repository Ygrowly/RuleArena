# RuleArena

RuleArena 是电商业务规则上线前的对抗式验证平台。本仓库当前完成阶段 1：Commerce Sandbox 的三类场景、固定/漏洞 Profile、真实 PostgreSQL 事务、事件、回执、快照和 HTTP API；Agent、Oracle 与产品 UI 留待后续阶段。

## 环境要求

- Python 3.12、uv 0.11+
- Node.js 20+、pnpm 10.15+
- Docker Engine 与 Docker Compose

## 本地安装与质量检查

```bash
uv sync --all-groups
uv run ruff check .
uv run mypy services packages scripts
uv run pytest -q
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend lint
pnpm --dir frontend typecheck
pnpm --dir frontend test
pnpm --dir frontend build
```

## 启动本地开发依赖（推荐）

复制 `.env.example` 为 `.env`，替换所有示例密码和内部令牌。该文件只用于本地开发，不应提交。

```powershell
$env:POSTGRES_ADMIN_USER='rulearena_admin'
$env:POSTGRES_ADMIN_PASSWORD='replace-local-admin-password'
$env:CONTROL_DB_PASSWORD='replace-local-control-password'
$env:SANDBOX_DB_PASSWORD='replace-local-sandbox-password'
$env:INTERNAL_SERVICE_TOKEN='replace-with-at-least-32-random-characters'
docker compose -p rulearena-dev up -d postgres redis
```

默认将 PostgreSQL 和 Redis 映射到 `15432`、`16379`，避免和 Windows 原生服务冲突。应用服务优先直接在宿主机运行，便于调试：

```powershell
$env:SANDBOX_DATABASE_URL='postgresql+asyncpg://rulearena_sandbox:<sandbox-password>@127.0.0.1:15432/rulearena'
$env:REDIS_URL='redis://127.0.0.1:16379/0'
$env:INTERNAL_SERVICE_TOKEN='replace-with-at-least-32-random-characters'
uv run alembic -c alembic-sandbox.ini upgrade head
uv run rulearena-commerce-sandbox
```

`/healthz` 只表示进程存活；`/readyz` 会实际查询 PostgreSQL 和 Redis，任一依赖失败返回 503。需要完整容器编排时再执行 `docker compose up -d --build`。

停止服务使用 `docker compose down`。只有明确希望删除本地数据库数据时才额外使用 `docker compose down -v`。

## 本机进程启动

若服务运行在宿主机，把 `.env` 中数据库主机设为 `127.0.0.1:15432`、Redis 设为 `127.0.0.1:16379`，先用 Compose 启动 PostgreSQL/Redis，再执行：

```bash
uv run alembic -c alembic.ini upgrade head
uv run alembic -c alembic-sandbox.ini upgrade head
uv run rulearena-control-api
uv run rulearena-commerce-sandbox
```

阶段 1 Sandbox 的真实 HTTP 验收（服务已在本机 `8001` 或其他端口启动）：

```powershell
$env:SANDBOX_HTTP_URL='http://127.0.0.1:8001'
$env:SANDBOX_TEST_TOKEN='<internal-service-token>'
uv run pytest -q tests/sandbox
```

配置加载会在数据库 URL、Redis URL 或至少 32 字符的内部服务令牌缺失时立即失败。生产环境不得使用 `.env.example` 中的示例值。

## 数据库隔离验证

Compose 启动后，在 PowerShell 中运行：

```powershell
$env:TEST_CONTROL_DATABASE_URL='postgresql+asyncpg://rulearena_control:<control-password>@localhost:15432/rulearena'
$env:TEST_SANDBOX_DATABASE_URL='postgresql+asyncpg://rulearena_sandbox:<sandbox-password>@localhost:15432/rulearena'
uv run pytest -q tests/test_database_isolation.py
```

测试同时检查自身 Schema 的 `USAGE` 权限和对方 Schema 的拒绝访问。不能以 SQLite 或 mock 结果替代此项验收。

## 依赖边界

`control_api` 与 `commerce_sandbox` 只依赖共享包；共享包不反向导入服务。`policy_schema` 定义规则和值对象，`domain_contracts` 定义动作、回执、事件、快照和 API 错误，`observability` 提供统一配置及 JSON 日志。后续阶段目录只保留边界说明，不含提前实现。
