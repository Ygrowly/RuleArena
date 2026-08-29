# 阶段 0 Review Spec：工程骨架与核心契约

角色：独立 Reviewer  
模式：只读审查；除非用户明确要求，否则不修改、不格式化、不提交代码。  
结论：`PASS`、`CONDITIONAL PASS` 或 `FAIL`。

## 1. 审查输入

完整阅读规格包 README、三份核心文档、阶段 0 执行 Spec、实现者交付报告、当前代码与完整 diff。先运行 `git status`，确认审查范围和非本阶段改动。

## 2. 核心问题

1. 工程是否真能从干净环境构建，而非依赖本机残留？
2. 契约是否严格、可版本化、可供四个执行组件共享？
3. Control 与 Sandbox 是否在代码、配置和数据库权限三层隔离？
4. 健康检查、配置和 CI 是否会真实暴露故障？
5. 实现是否偷跑了后续阶段，产生不必要抽象？

## 3. 必查项

- `RuleSpec`、Action、Receipt、Event、Snapshot 的类型、JSON Schema 和版本字段。
- Pydantic 是否统一 `extra="forbid"`；金额是否始终为 Decimal/字符串表示。
- 是否存在 `eval`、`exec`、动态 import、任意 SQL/代码字段。
- 依赖方向是否为服务依赖共享包，共享包不反向依赖服务。
- 两套数据库角色和 Schema 是否由真实授权语句约束。
- `/readyz` 是否实际探测数据库/Redis，而非固定返回 200。
- 生产配置是否存在弱默认令牌、默认密码或敏感日志。
- Docker/CI/README 命令是否一致，锁文件是否进入版本控制。

## 4. 亲自执行

Reviewer 应在条件允许时执行执行 Spec 中的全部命令，并额外执行：

```bash
git diff --check
uv run pytest -q tests --maxfail=1
docker compose config --quiet
```

还要进行至少五个负例：未知 RuleSpec 字段、float 金额、非法枚举、代码字符串、跨 Schema 查询。不能只引用实现者截图或口头结果。

## 5. 严重级别

- P0：Sandbox 角色可读写 Control；契约含 Ground Truth；动态代码执行；密钥入库；金额使用 float 导致错误。
- P1：干净构建失败；严格校验缺失；ready 假健康；CI 未覆盖关键检查；文档无法复现。
- P2：命名、开发体验、非阻断测试或文档优化。

## 6. 通过标准

| 维度 | 必须达到 |
| --- | --- |
| Build | 后端与前端可从锁文件重建 |
| Contract | 正负例通过，Schema 稳定，无实现泄漏 |
| Isolation | 数据库权限实测阻止越权 |
| Operations | health/readiness/config 语义正确 |
| Scope | 仅完成阶段 0 |

存在任一 P0 必须 `FAIL`；存在未接受的 P1 不得 `PASS`。

## 7. 输出模板

```text
结论：PASS | CONDITIONAL PASS | FAIL
审查范围：commit/diff/文件
已执行命令：命令 + 退出码 + 摘要
Findings：按 P0/P1/P2，含文件位置、复现步骤、影响、建议
验收矩阵：逐项 Pass/Fail/Not Verified
残余风险：当前无法验证的事实
下一阶段条件：允许/不允许进入阶段 1，以及理由
```
