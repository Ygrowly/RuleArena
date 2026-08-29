# RuleArena

RuleArena 是电商业务规则上线前的对抗式验证平台。本仓库当前完成阶段 0：可复现的工程骨架、严格共享契约、两个最小服务和 PostgreSQL 权限隔离；尚未实现业务动作、Agent、Oracle 或产品 UI。

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

## 启动完整基础设施

复制 `.env.example` 为 `.env`，替换所有示例密码和内部令牌。该文件只用于本地开发，不应提交。

```bash
docker compose config --quiet
docker compose up -d --build
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
curl http://localhost:8001/healthz
curl http://localhost:8001/readyz
```

Compose 会先创建 PostgreSQL 角色与 `control`、`sandbox` Schema，再分别执行两条 Alembic 基线，成功后才启动服务。`/healthz` 只表示进程存活；`/readyz` 会实际查询 PostgreSQL 和 Redis，任一依赖失败返回 503。

停止服务使用 `docker compose down`。只有明确希望删除本地数据库数据时才额外使用 `docker compose down -v`。

## 本机进程启动

若服务运行在宿主机，把 `.env` 中数据库主机设为 `localhost`，先用 Compose 启动 PostgreSQL/Redis，再执行：

```bash
uv run alembic -c alembic.ini upgrade head
uv run alembic -c alembic-sandbox.ini upgrade head
uv run rulearena-control-api
uv run rulearena-commerce-sandbox
```

配置加载会在数据库 URL、Redis URL 或至少 32 字符的内部服务令牌缺失时立即失败。生产环境不得使用 `.env.example` 中的示例值。

## 数据库隔离验证

Compose 启动后，在 PowerShell 中运行：

```powershell
$env:TEST_CONTROL_DATABASE_URL='postgresql+asyncpg://rulearena_control:<control-password>@localhost:5432/rulearena'
$env:TEST_SANDBOX_DATABASE_URL='postgresql+asyncpg://rulearena_sandbox:<sandbox-password>@localhost:5432/rulearena'
uv run pytest -q tests/test_database_isolation.py
```

测试同时检查自身 Schema 的 `USAGE` 权限和对方 Schema 的拒绝访问。不能以 SQLite 或 mock 结果替代此项验收。

## 依赖边界

`control_api` 与 `commerce_sandbox` 只依赖共享包；共享包不反向导入服务。`policy_schema` 定义规则和值对象，`domain_contracts` 定义动作、回执、事件、快照和 API 错误，`observability` 提供统一配置及 JSON 日志。后续阶段目录只保留边界说明，不含提前实现。
