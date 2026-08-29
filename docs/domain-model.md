# RuleArena 领域模型 v0.1

状态：已确认  
领域：电商优惠、退款积分、次数型会员权益

## 1. 建模原则

1. 业务世界由状态、动作、状态转换和不变量构成。
2. Policy、参考模型、被测 Sandbox、Oracle 和 Ground Truth 必须分离。
3. 货币使用 Decimal 和明确币种，不使用 float。
4. 余额和积分必须能由不可变账本重建。
5. RuleVersion、ScenarioVersion、SandboxVersion、OracleVersion 均不可被历史运行覆盖。
6. Agent 只能通过结构化动作操作世界，不可直接访问实现细节。

## 2. 限界上下文

| 上下文 | 职责 | 主要聚合 |
| --- | --- | --- |
| Policy | 规则输入、编译、歧义确认和版本 | PolicyPack、RuleVersion |
| Scenario | 初始状态、版本与隐藏预期 | Scenario、ScenarioVersion |
| Commerce Sandbox | 真实业务动作、事务、事件和快照 | TestUser、Order、Coupon、Refund、Membership |
| Attack | 策略、预算、搜索与 Checkpoint | AttackRun、StrategyRun、ActionAttempt |
| Verification | 重放、不变量、分类和最小化 | OracleResult、Counterexample |
| Evaluation | Golden Set、Baseline、指标和门禁 | BenchmarkCase、EvaluationRun |

## 3. Policy 模型

### PolicyPack

    id
    name
    domain = COMMERCE
    source_text
    active_version_id
    created_at

### RuleVersion

    id
    policy_pack_id
    version
    rule_spec_json
    compile_status
    ambiguities_json
    prompt_version
    confirmed_at
    created_at

状态：

    DRAFT → COMPILED → NEEDS_CONFIRMATION → CONFIRMED

RuleSpec 只允许固定领域原语：

- promotion_rules
- refund_rules
- points_rules
- membership_rules
- invariant_refs
- parameters

RuleSpec 不允许任意代码、表达式求值、SQL 或动态 import。

## 4. Scenario 模型

### Scenario

    id
    name
    category
    latest_version_id

### ScenarioVersion

    id
    scenario_id
    version
    initial_state_json
    sandbox_profile
    is_golden
    visibility = DEVELOPMENT | HIDDEN
    ground_truth_ref

ground_truth_ref 只供 Evaluation 使用，不能进入 Agent Prompt、工具返回、公开 Trace 或状态快照。

## 5. Commerce Sandbox 聚合

### TestUser

    id
    run_id
    balance
    points_balance
    is_new_user
    membership_status
    version

### Coupon

    id
    run_id
    owner_id
    coupon_type
    face_value
    threshold
    status
    reserved_order_id
    used_order_id
    usage_count
    version

状态：

    AVAILABLE → RESERVED → USED
    USED → RESTORED
    RESTORED → RESERVED
    AVAILABLE/RESTORED → EXPIRED

### Order

    id
    run_id
    user_id
    original_amount
    discount_amount
    paid_amount
    refunded_amount
    points_granted
    status
    version

状态：

    DRAFT → PENDING_PAYMENT → PAID
    PAID → PARTIALLY_REFUNDED → REFUNDED
    PAID → REFUNDED
    DRAFT/PENDING_PAYMENT → CANCELLED

### Refund

    id
    run_id
    order_id
    amount
    status
    idempotency_key
    created_at

### PointsLedgerEntry

    id
    run_id
    user_id
    order_id
    entry_type = GRANT | REVOKE | REDEEM
    amount
    idempotency_key
    created_at

points_balance 是可查询投影，账本净额才是权威校验来源。

### Membership

    id
    run_id
    user_id
    paid_amount
    status
    version

状态：

    INACTIVE → ACTIVE → CANCELLED
    ACTIVE → REFUNDED

### Entitlement

    id
    run_id
    membership_id
    entitlement_type
    granted_quantity
    consumed_quantity
    revoked_quantity
    status
    version

状态：

    GRANTED → PARTIALLY_CONSUMED → CONSUMED
    GRANTED/PARTIALLY_CONSUMED → REVOKED

### BusinessEvent

    event_id
    run_id
    aggregate_type
    aggregate_id
    event_type
    payload_json
    idempotency_key
    occurred_at

事件只追加，不原地修改。

## 6. 可执行动作

| Action | 必要参数 | 主要前置条件 |
| --- | --- | --- |
| create_user | initial_balance | run 尚无用户 |
| issue_coupon | value、threshold | 用户存在 |
| create_order | amount | 用户存在，金额大于 0 |
| apply_coupon | order_id、coupon_id | 订单待支付，优惠券可用且达门槛 |
| pay_order | order_id | 订单待支付，余额足够 |
| cancel_order | order_id | 订单允许取消 |
| refund_order | order_id、amount | 订单已支付或部分退款 |
| redeem_points | amount | 可用积分足够 |
| activate_membership | paid_amount、quantity | 会员未激活 |
| consume_entitlement | quantity | 权益可用 |
| cancel_membership | refund_requested | 会员处于 ACTIVE |
| inspect_state | scope | 只读 |

每个写动作必须携带 idempotency_key，并返回 ActionReceipt。

## 7. 业务事件

- USER_CREATED
- COUPON_ISSUED
- COUPON_RESERVED
- COUPON_USED
- COUPON_RESTORED
- ORDER_CREATED
- PAYMENT_CAPTURED
- ORDER_CANCELLED
- REFUND_ISSUED
- POINTS_GRANTED
- POINTS_REVOKED
- POINTS_REDEEMED
- MEMBERSHIP_ACTIVATED
- ENTITLEMENT_GRANTED
- ENTITLEMENT_CONSUMED
- ENTITLEMENT_REVOKED
- MEMBERSHIP_CANCELLED
- MEMBERSHIP_REFUNDED

## 8. 核心不变量

### INV-01 Refund Conservation

    sum(refunds.amount) <= order.paid_amount

### INV-02 Reward Conservation

    valid_order_points <= points_function(order.paid_amount - order.refunded_amount)

### INV-03 Coupon Single Value

一张优惠券不能对两个最终有效订单同时贡献优惠价值。恢复行为必须由明确规则允许。

### INV-04 Entitlement Refund Consistency

会员全额退款后，未消费权益必须撤销；已消费权益必须减少可退金额或明确阻止全额退款。

### INV-05 Idempotent Effect

相同 run_id、action_type、idempotency_key 只能产生一次业务效果和一份成功 Receipt。

### INV-06 Legal Transition

动作只能从允许状态迁移。REFUNDED 订单不得再次退款，CANCELLED 订单不得支付。

### INV-07 Ledger Consistency

    user.points_balance == sum(points_ledger signed amounts)

### INV-08 Non-negative Assets

余额、积分、可用权益次数、未退款金额均不得为负。

## 9. Attack 模型

### AttackRun

    id
    rule_version_id
    scenario_version_id
    sandbox_version
    oracle_version
    status
    outcome
    max_depth
    token_budget
    time_budget_seconds
    random_seed
    started_at
    finished_at

### StrategyRun

    id
    attack_run_id
    strategy_type = VALUE_FLOW | LIFECYCLE | BOUNDARY
    status
    frontier_json
    visited_state_hashes
    checkpoint_version
    token_usage
    model_calls
    latency_ms

### ActionAttempt

    id
    strategy_run_id
    step_index
    action_type
    arguments_json
    reason
    expected_risk
    model_confidence
    validation_status
    simulation_result_ref

Agent 的 confidence 不能作为漏洞成立依据。

## 10. Verification 模型

### ReplayRun

    id
    candidate_trace_id
    clean_sandbox_run_id
    status
    action_receipts
    final_snapshot_hash

### OracleResult

    id
    replay_run_id
    invariant_id
    passed
    evidence_refs
    classification
    oracle_version

### Counterexample

    id
    attack_run_id
    invariant_id
    classification
    original_actions
    minimized_actions
    replay_success_count
    severity
    created_at

分类：

| 类型 | 判定 |
| --- | --- |
| POLICY_CONFLICT | 参考规则模型本身可到达违规状态 |
| IMPLEMENTATION_DIVERGENCE | 参考模型安全，但 Sandbox 实现违反预期 |
| UNCONFIRMED_CANDIDATE | 模型怀疑，但 API 无法复现 |
| AMBIGUOUS_POLICY | 规则无法唯一映射为状态转换 |
| UNSUPPORTED_RULE | 超出固定领域原语 |

## 11. Evaluation 模型

### BenchmarkCase

    id
    scenario_version_id
    rule_version_id
    visibility
    expected_outcome
    expected_invariant_ids
    max_budget

### EvaluationRun

    id
    benchmark_version
    runtime_version
    model_config_hash
    prompt_version
    baseline_type
    metrics_json
    started_at
    finished_at

Baseline：

- RANDOM
- BFS
- SINGLE_AGENT
- MULTI_STRATEGY

## 12. 状态哈希与搜索等价

状态哈希只包含影响后续行为的规范化字段：

- 用户余额、积分、新用户状态。
- 优惠券状态、使用次数、关联订单。
- 订单状态、实付、累计退款、已发积分。
- 会员和权益数量。

不包含数据库主键、创建时间、Trace ID 等非语义字段。相同哈希表示在当前规则版本下可视为同一搜索状态。

## 13. 黄金案例

Vulnerable v1：

1. 创建用户并发放 50 元券。
2. 创建 150 元订单并应用优惠券。
3. 支付 100 元，获得 100 积分。
4. 全额退款 100 元。
5. 实现错误地恢复优惠券但未撤销积分。
6. 用户兑换 20 元券。

INV-02 和 INV-03 产生违规证据。

Fixed v2 必须撤销订单产生的积分，并按 RuleVersion 明确处理优惠券恢复。相同动作序列应无法再违反不变量。

