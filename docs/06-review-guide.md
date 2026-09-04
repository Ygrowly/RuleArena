# RuleArena 独立 Review 说明 v0.1

角色：独立 Reviewer / Release Reviewer  
默认模式：只读、亲自验证、不顺手修复  
目标：确认实现是否符合业务不变量、架构边界、质量门禁和事实真实性，而不是评价代码“看起来不错”

---

## 0. Review 的核心原则

1. 不相信实现者的完成声明，检查代码、运行命令和原始数据；
2. 不把测试通过等同于需求完成，检查测试是否真的有判别力；
3. 不把 Agent 输出等同于业务事实，追到 Receipt、Event、Snapshot 和 Oracle；
4. 不把前端数字等同于指标，必须从原始 Run 重算；
5. 不因 Demo 好看忽略 Ground Truth 泄漏、数据串 Run、重复副作用和误报；
6. 不在 Review 会话中直接修复代码，除非用户明确切换为修复模式；
7. 所有结论附文件、命令、Run ID、复现步骤或运行证据；
8. `NOT VERIFIED` 不是 `PASS`。

---

## 1. 审查输入与阅读顺序

Reviewer 开始前依次读取：

1. `README.md`；
2. `01-product-requirements.md`；
3. `02-domain-model.md`；
4. `03-technical-spec.md`；
5. `04-project-overview.md`；
6. 当前阶段 `phases/*-review.md`；
7. 当前阶段 `phases/*-exec.md`；
8. 上一阶段 Review、当前实现报告和未关闭 Findings；
9. 当前 diff、相关代码、测试、迁移、配置和运行证据；
10. `05-development-guide.md` 作为工程规范补充。

先执行：

```bash
git status --short
git branch --show-current
git log -5 --oneline
git diff --stat
git diff
```

若实现者未提供 commit/diff 范围，先确定本次审查边界。保护用户无关改动，不执行 destructive git 命令。

---

## 2. Finding 严重级别

### P0：必须阻断

- 资金、退款、积分、优惠券或会员核心不变量错误；
- Ground Truth、hidden expectation、漏洞 Profile 或密钥泄漏；
- Agent/公共 API 可以直接访问 Sandbox 数据库或绕过动作契约；
- Oracle 与 Sandbox 共享错误逻辑，形成自己验证自己；
- 跨 run_id 数据污染；
- 重试产生重复退款、积分或权益副作用；
- normal Case 被确认违规但被隐藏、过滤或改答案；
- 指标手填、虚构或旧版本冒充当前版本；
- Sandbox 公网可写或公共用户可以绕过成本预算；
- 核心 E2E 的证据由前端伪造。

### P1：修复或明确接受后才能继续

- 当前阶段 P0 功能或验收项缺失；
- 生命周期、Checkpoint、取消、超时或恢复不可靠；
- Baseline 的 Case、模型或预算不可比；
- Trace 无法从 Finding 追到执行证据；
- Frozen Run 并非真实持久化 Run；
- SSE/页面刷新后状态错误；
- 部署不可复现、迁移不阻断或 readiness 语义错误；
- 设计明显偏离 Spec 但没有 ADR 和用户确认；
- 关键测试只验证实现细节，没有验证业务结果。

### P2：当前阶段不阻断

- 局部命名、重复、文档、性能或视觉优化；
- 非关键无障碍和开发体验问题；
- 不影响当前门禁的额外统计与置信区间；
- 有明确升级条件但尚未必要的架构优化。

严重级别根据影响和可利用性判断，不根据修改成本降级。

---

## 3. 通用审查矩阵

### 3.1 业务正确性

- RuleSpec 是否只包含固定领域原语；
- 歧义未确认时是否禁止运行；
- RuleVersion 是否不可变，历史 Run 是否绑定旧版本；
- 金额是否使用 Decimal 和币种；
- 退款、积分、优惠券和权益是否可从账本/事件重建；
- 每条状态转换的前置条件是否正确；
- INV-01～INV-08 是否都有正反测试；
- Vulnerable 和 Fixed 的差异是否最小且可解释；
- 正常路径是否在修复后仍通过。

### 3.2 架构隔离

- Simulator 是否调用了 Sandbox 内部函数或 ORM；
- Oracle 是否复用了 Sandbox 的裁决函数；
- Runtime 是否有 hidden loader 或 Ground Truth 读取路径；
- Agent 是否获得漏洞 Profile、expected invariant 或最短路径；
- Control 和 Sandbox 数据库角色是否隔离；
- 前端是否直接计算 Outcome 或权威指标；
- Redis/SSE 是否被错误当成最终状态。

### 3.3 Agent Harness

- Context 是否只包含当前任务所需信息；
- Tool 是否有严格输入/输出和权限；
- 合法动作是否由 Runtime 计算，而非模型自由创造；
- 每策略的 Context、预算、frontier 和 Trace 是否隔离；
- 模型输出非法时是否 fail closed；
- Agent confidence 是否错误参与裁决；
- 是否存在无限循环、无界工具调用或预算绕过；
- 是否能够取消并从 Checkpoint 恢复。

### 3.4 副作用与一致性

- 写动作是否具有稳定业务幂等键；
- Receipt、聚合、账本和事件是否在同一事务中提交；
- 提交前超时和提交后超时是否可以区分；
- 结果未知时是否停止而不是换键重试；
- 重复 Worker、重复点击和消息重投是否只产生一次效果；
- 状态更新是否使用 expected status/版本条件；
- Checkpoint 恢复前是否重新查询权威状态；
- 并发或重复退款是否受数据库约束和业务校验双重保护。

### 3.5 Replay、Oracle 与最小化

- Candidate 是否必须经过干净 Sandbox Run；
- Replay 是否固定完整版本元组；
- Oracle Evidence 是否能指向实际字段、账本或事件；
- 相同反例是否连续 3/3 违反同一 invariant；
- Delta Debugging 每次删除后是否重新执行；
- 结果是否只声称 1-minimal；
- POLICY_CONFLICT、IMPLEMENTATION_DIVERGENCE 和 UNCONFIRMED 是否分类正确。

### 3.6 Evaluation 与真实性

- 16 development + 8 hidden 是否数量、版本和分布完整；
- Ground Truth 首次入集是否有 3/3 构造证据；
- hidden 与 development 是否存在近重复污染；
- Random/BFS/Single/Multi 是否使用公平预算；
- 模型、Prompt、seed 或预算变化是否生成新 BenchmarkRun；
- 指标是否可从原始 Run 重算；
- N/A、INFRA_FAILED、CANCELLED 和未发现口径是否正确；
- pass@k/pass^k 是否使用正确量词；
- README、简历和 Demo 数字是否绑定当前 BenchmarkRun。

### 3.7 可观测与安全

- Finding 是否可导航到 StrategyStep、Replay、Receipt、Event 和 Oracle；
- Trace 是否包含版本、Hash、延迟、Token、成本和错误；
- 是否泄漏密钥、完整敏感规则、hidden expected path 或 Chain-of-Thought；
- 用户规则中的 Prompt Injection 是否只能作为数据处理；
- 公共 Demo 是否限流、限时、限步骤、限 Token；
- Sandbox 和内部评测端点是否公网不可达；
- 错误响应是否暴露堆栈、环境变量或内部 URL。

### 3.8 Web 与产品表达

- 30 秒内能否理解“搜索路径 + API 重放 + Oracle”；
- 首屏是否突出业务问题而不是多 Agent 名词；
- 黄金案例是否清楚展示异常权益、动作路径和修复；
- State Diff 是否优先展示实付、退款、积分、券、权益和订单状态；
- 是否明确区分 confirmed、unconfirmed、budget exhausted 和 infra failed；
- `NO_VIOLATION_WITHIN_BUDGET` 是否被误写成“安全”；
- Frozen Run 是否标注来源且可在无模型时查看；
- 页面刷新、SSE 重连和重复点击是否保持一致；
- 技术细节是否可展开但不干扰业务主线。

---

## 4. 阶段审查重点

| 阶段 | 核心问题 | 关键阻断项 |
| --- | --- | --- |
| 0 Foundation | 契约、目录、迁移和测试骨架是否稳定 | Schema 漂移、未锁定依赖、测试不可运行 |
| 1 Sandbox | 业务世界是否真实可执行、隔离和幂等 | 跨 Run、账本不一致、重复副作用 |
| 2 Oracle/Simulator | 是否独立、确定、可重放 | 自己验证自己、状态哈希错误、Oracle 误报 |
| 3 Agent Runtime | Agent 是否有界、隔离、可恢复 | Ground Truth 路径、预算绕过、Outcome 由模型决定 |
| 4 Eval/Observability | 数据集、消融、指标和泄漏是否可信 | 手填指标、Case 污染、旧结果复用、预算不公平 |
| 5 Web/Deployment | 是否可理解、可验证、安全运行 | 伪造证据、Sandbox 公网、成本攻击、核心 E2E 失败 |

对应阶段必须以 `phases/*-review.md` 的具体门禁为准，本表不替代阶段 Review Spec。

---

## 5. Reviewer 必须亲自执行的验证

根据阶段选择最小充分集合，并记录真实输出：

```bash
uv run ruff check .
uv run pytest
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
docker compose config
```

阶段 4 至少：

```bash
uv run pytest -q tests/evaluation tests/observability
uv run pytest -q tests/security/test_ground_truth_leakage.py
uv run pytest -q tests/regression
uv run rulearena benchmark verify --latest
```

Reviewer 还应进行针对性变异验证：

1. 修改一个版本字段，确认 Release Gate 拒绝旧结果；
2. 人为加入 Ground Truth 标记，确认泄漏测试失败，再恢复改动；
3. 重复发送同一幂等写请求，确认只产生一次效果；
4. 在写动作提交后模拟超时，确认通过 Receipt 恢复；
5. 使一个 normal Case 触发 Oracle，确认发布门禁阻断；
6. 删除最小反例中的关键步骤，确认不再违反同一 invariant；
7. 刷新页面或断开 SSE，确认从 Run API 恢复一致状态。

Review 中产生的临时变异必须恢复，不得覆盖用户改动。若环境、密钥或预算不足，标记 `NOT VERIFIED` 并说明缺失条件。

---

## 6. 逆向失败推演

Reviewer 至少从以下失败目标反推一次：

### 如何让系统产生假阳性

- Oracle 将正常优惠恢复误判为重复价值；
- Replay 使用了污染的 Sandbox；
- 前端把 Candidate 当 Confirmed；
- normal Case 被错误分类或从分母移除。

### 如何让系统漏掉真实问题

- 状态哈希错误合并了不同资产状态；
- Agent 的合法动作集合缺少关键动作；
- BFS/策略预算在关键深度前耗尽；
- Oracle 只检查最终状态，遗漏中间非法事件；
- Simulator 与 Sandbox 语义漂移。

### 如何让系统重复副作用

- ToolCallID 被误当业务幂等键；
- 超时后用新 key 重试；
- Receipt 与业务写不在同一事务；
- Worker 重投覆盖 Checkpoint 或重复 Replay。

### 如何让评测看起来更好

- 给 Multi-strategy 更多 Token 或时间；
- 删除失败 Case 或修改 Ground Truth；
- hidden 与 development 近重复；
- 将 INFRA_FAILED 从分母排除却不披露；
- 复用旧模型/旧版本的最好 Run；
- 只展示 pass@k，不展示 pass^k、成本和误报。

### 如何攻击公开 Demo

- 绕过 IP/Session 限流；
- 构造超长规则消耗 Token；
- 直接请求 Sandbox 或内部 Benchmark API；
- 通过错误信息读取密钥、Profile 或隐藏集；
- 重复点击创建大量 Run；
- 利用 SSE 连接占满资源。

发现防线仅存在于 Prompt 或 UI 时，按实际影响至少记为 P1，高风险边界记为 P0。

---

## 7. 事实审计

对 README、项目网站、简历和完成报告逐条建立：

| Claim | 类型 | 证据 | 结论 |
| --- | --- | --- | --- |
| “支持三类规则” | IMPLEMENTED | E2E/契约测试 | PASS/FAIL |
| “重放 3/3” | MEASURED | ReplayRun ID | PASS/FAIL |
| “hidden ≥75%” | MEASURED/TARGET | 当前 BenchmarkRun | PASS/FAIL/NOT VERIFIED |
| “在线部署” | IMPLEMENTED | URL + smoke | PASS/FAIL |
| “真实业务使用” | 外部事实 | 用户/流量/部署证据 | 不得由 Demo 推断 |

禁止接受：

- “预计”“理论上”被删去限定词后成为结果；
- Mock 压测被描述为生产流量；
- 漏洞 Profile 被描述为真实企业事故；
- 设计文档被描述为已实现代码；
- 单次最好成绩被描述为稳定表现。

---

## 8. Review 输出格式

```text
结论：PASS | CONDITIONAL PASS | FAIL
审查阶段、版本、commit/diff、环境：

执行证据：
- command：result
- Run/Replay/Benchmark ID：

Findings（按严重级别和影响排序）：

[P0|P1|P2] 标题
- 影响：
- 证据：文件/行、命令输出、Run ID
- 最小复现：
- Spec/不变量依据：
- 建议修复方向：不直接改代码
- 重新验证方法：

验收矩阵：
- 项目：PASS | FAIL | NOT VERIFIED，证据

事实审计：
- 可公开表述：
- 不可公开/需限定表述：

遗留风险：
- 已接受/未接受

下一步：
- ALLOW NEXT PHASE | BLOCK | USER DECISION REQUIRED
```

只有 P0 为 0，且 P1 已修复或由用户逐项明确接受，才能进入下一阶段。最终发布时，P1 只有明确接受后才能使用 `RELEASE WITH ACCEPTED RISKS`。

---

## 9. 最终 Release Review

最终独立验收至少回答：

1. 非技术用户能否在 30 秒说出“搜索路径 + API 重放 + Oracle”；
2. 技术面试官能否从 UI 追到 RuleSpec、Simulator、Sandbox、Oracle、Trace 和评测；
3. 黄金案例能否完成 Vulnerable → Confirm → Minimize → Fixed → Regression；
4. 24 Case、0 normal confirmed 误报、3/3、hidden ≥75%、P0 回归 100% 是否来自当前实际 Run；
5. Ground Truth、密钥和隐藏路径是否无公开泄漏；
6. Sandbox、评测内部端点和数据库是否不暴露公网；
7. 刷新、断线、超时、取消、Worker 重启和按钮连点是否保持权威状态；
8. Docker 本地和线上部署是否可复现；
9. README 和简历是否诚实区分真实业务问题、测试服务和生产使用；
10. Multi-strategy 是否通过消融证明边际价值，若没有是否已降低宣传权重。

任一核心证据无法验证，都不能由“代码看起来合理”替代。
