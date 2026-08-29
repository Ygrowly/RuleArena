# 阶段 1 Review Spec：Commerce Sandbox

角色：独立 Reviewer  
模式：默认只读，亲自运行数据库和 HTTP 测试。  
目标：确认这是真实、隔离、可重放的被测业务系统，而不是为了演示拼出的 Mock。

## 1. 审查输入

阅读核心三文档、阶段 0 Review、阶段 1 执行 Spec、实现报告、迁移、领域代码、API、测试及完整 diff。确认本阶段没有 Oracle/Agent 等无关实现。

## 2. 威胁式审查

重点尝试证明以下失败存在：

- 同一请求因重试产生双退款、双积分或双权益。
- 两个 RunSpace 通过漏传 run_id 互相读取或修改。
- 事务失败留下事件但无状态，或有状态但无 Receipt。
- 并发部分退款/核销突破上限。
- reset 没有恢复真正干净状态。
- vulnerable/fixed 标记通过 API、Snapshot、Event 或日志泄漏。
- 使用 float、非稳定排序或内部 ID 导致相同状态 Hash 不稳定。

## 3. 亲自执行

执行实现者的全部命令，并用真实 HTTP 至少完成：创建 Run → 获取初始 Snapshot → 执行动作 → 查询 Receipt/Event → reset → 重放。额外并发发送相同 idempotency_key 和不同 key 的冲突写入。

建议补充：

```bash
uv run pytest -q tests/sandbox tests/integration tests/concurrency --maxfail=1
uv run alembic check
git diff --check
```

检查数据库约束、索引、隔离级别和查询过滤，不能只看 Service 层的 `if`。

## 4. Ground Truth 检查

全库搜索 `vulnerable`、`ground_truth`、Case ID 和预期动作序列，追踪这些字段是否可能进入共享 Contract、API 响应、Agent 将来的读取路径或公共 Trace。内部 Profile 选择存在是允许的，向攻击策略泄漏其含义不允许。

## 5. 严重级别

- P0：跨 Run 数据污染；可重复资金/权益副作用；事件与状态非原子；Ground Truth 泄漏；未授权公共访问。
- P1：reset/重放不稳定；fixed 正常链路错误；并发覆盖不足；Snapshot 不规范；阶段业务缺失。
- P2：错误文案、索引优化、非关键开发体验问题。

## 6. 通过标准

| 维度 | 必须达到 |
| --- | --- |
| Realism | HTTP、事务、PostgreSQL 均实际参与 |
| Correctness | fixed Profile 的正常与非法迁移符合规则 |
| Fault injection | 至少六个缺陷可复现且对攻击方隐藏 |
| Reliability | 幂等、并发、回滚、reset 全部有证据 |
| Scope | 没有提前实现 Oracle/Agent |

## 7. 输出模板

```text
结论：PASS | CONDITIONAL PASS | FAIL
审查环境与范围
亲自执行的命令和 HTTP/并发实验
Findings：P0/P1/P2，含复现步骤与证据
业务动作 × Profile 验收矩阵
Ground Truth 泄漏结论
残余风险与未验证项
是否允许进入阶段 2
```
