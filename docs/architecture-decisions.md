# 架构决策记录（ADR 摘要）

## ADR-1 Reference Simulator 与 Commerce Sandbox 分离

- **决策**：搜索阶段使用进程内纯函数 Simulator；确认阶段强制走真实
  Commerce Sandbox HTTP 服务。
- **理由**：Simulator 让多策略搜索 cheap、快速、可穷举；但本地模拟不构成
  证据。资金与状态语义的可信结论只能来自真实事务、真实回执与真实快照。
- **后果**：两套实现存在语义漂移风险。对策：Oracle 只裁决真实重放数据；
  Candidate 不直接升级为 Confirmed Finding（`worker.py` 中唯一写入点以
  `ReplayClassification.CONFIRMED_VIOLATION` 为前置条件）。

## ADR-2 确定性 Runtime，而非 Agent 自治

- **决策**：状态机（`DRAFT→…→COMPLETED`）、预算、取消、恢复、持久化全部由
  确定性 Python 代码控制；LLM 每轮只产出一个结构化 Proposal。
- **理由**：模型输出不可信。CAS 条件更新（`WHERE status=:expected`）+
  Checkpoint 版本 CAS + 唯一约束，使重复投递、进程崩溃、超时 unknown
  都不会双写或非法跳转。
- **后果**：Agent 无法跳过重放、无法写 outcome、无法超预算；代价是
  LLM 不能“智能地”绕过坏路径（这是特性）。

## ADR-3 多策略隔离（VALUE_FLOW / LIFECYCLE / BOUNDARY）

- **决策**：三个 `StrategyAgent` 实例独立构造（复用同一实例在构造期报错），
  各自持有私有 Checkpoint、usage、历史（最近 12 条）；策略之间不共享
  CoT、候选路径或完整 Context，仅共享已确认 Counterexample 的 ID。
- **理由**：隔离保证消融实验（Single vs Multi）可比，也限制单一策略
  被注入后的爆炸半径。
- **后果**：上下文重复注入带来 token 成本；已通过共享确认 ID 平衡。

## ADR-4 Oracle 确定性裁决

- **决策**：`DeterministicOracle` 只消费真实重放产生的快照/回执/事件，
  对固定 invariant 集合给出确定性判定；重放稳定率要求 3/3 独立 RunSpace。
- **理由**：反例的最小价值单位是“可重放、可复核”；LLM/启发式评分不可
  作为确认来源。
- **后果**：Oracle 只覆盖已编码 invariant（三类场景共 7 个）；新漏洞类型
  需要扩展 invariant 编码，不能靠模型即兴。

## ADR-5 不引入 LangGraph/AutoGen 类通用 Runtime

- **决策**：不用任何图编排框架；Workflow 是显式代码。
- **理由**：本项目的不变量（CAS、幂等键、Receipt 查询、预算、隔离）需要
  精确控制与可审计性；通用框架的隐藏状态机与抽象交换机恰好弱化这些性质。
- **后果**：编排代码需要自己维护（约数百行，测试覆盖完整状态转换表），
  换来零隐藏行为与可解释的恢复语义。

## ADR-6（阶段 5）冻结案例优先于实时演示

- **决策**：Demo 默认展示从真实持久化 Run 导出的冻结案例；Live Run 限额
  且失败时如实显示。
- **理由**：可信证据不依赖模型可用性；公共成本可控。
- **后果**：需要导出管线（`scripts/export_frozen_demo.py`）；冻结数据在
  `provenance.honesty` 中声明“动议由确定性脚本驱动”，不冒充真实模型运行。
