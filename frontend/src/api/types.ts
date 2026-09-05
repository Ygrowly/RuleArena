// Types mirroring the Control API contracts (all extra="forbid" models).

export type ScenarioType = "PROMOTION" | "REFUND_POINTS" | "MEMBERSHIP_ENTITLEMENT";

export type AttackStatus =
  | "DRAFT"
  | "NEEDS_CONFIRMATION"
  | "READY"
  | "SEARCHING"
  | "REPLAYING"
  | "RECOVERING"
  | "CANCEL_REQUESTED"
  | "COMPLETED"
  | "CANCELLED"
  | "FAILED";

export type AttackOutcome =
  | "CONFIRMED_VIOLATION"
  | "UNCONFIRMED_CANDIDATE"
  | "NO_VIOLATION_WITHIN_BUDGET"
  | "AMBIGUOUS_POLICY"
  | "UNSUPPORTED_RULE"
  | "INFRA_FAILED"
  | "CANCELLED";

export type StrategyType = "VALUE_FLOW" | "LIFECYCLE" | "BOUNDARY";

export interface Budget {
  max_steps: number;
  max_tokens: number;
  max_cost: number;
  max_time_seconds: number;
}

export interface BudgetUsage {
  steps: number;
  tokens: number;
  cost: number;
  elapsed_seconds: number;
}

export interface AttackRun {
  run_id: string;
  job_key: string;
  rule_version_id: string;
  scenario_version_id: string;
  sandbox_version: string;
  oracle_version: string;
  status: AttackStatus;
  outcome: AttackOutcome | null;
  budget: Budget;
  random_seed: number;
  created_at: string;
}

export interface StrategyRun {
  strategy_run_id: string;
  attack_run_id: string;
  strategy_type: StrategyType;
  status: string;
  budget: Budget;
  usage: BudgetUsage;
}

export interface CounterexampleRecord {
  counterexample_id: string;
  attack_run_id: string;
  candidate_key: string;
  invariant_id: string;
  original_actions: FrozenAction[];
  minimized_actions: FrozenAction[];
  replay_run_id: string;
  created_at: string;
}

export interface FrozenAction {
  action_type: string;
  actor_id: string;
  target_id: string | null;
  idempotency_key: string | null;
  arguments: Record<string, string | number | boolean>;
}

export type TraceKind =
  | "LLM_CALL"
  | "ACTION_PROPOSAL"
  | "SIMULATION"
  | "SANDBOX_HTTP"
  | "SNAPSHOT"
  | "ORACLE_CHECK";

export interface TraceRecord {
  trace_id: string;
  run_id: string;
  strategy_id: string | null;
  step_id: number;
  kind: TraceKind;
  rule_version_id: string | null;
  model_config_hash: string | null;
  prompt_version: string | null;
  action_summary: Record<string, unknown>;
  tool_result_summary: Record<string, unknown>;
  before_state_hash: string | null;
  after_state_hash: string | null;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost: number;
  retry_count: number;
  status: string;
  error_type: string | null;
  parent_trace_id: string | null;
  created_at: string;
}

export interface RuntimeEvent {
  cursor: number;
  run_id: string;
  event_type: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface ConfirmationQuestion {
  question_id: string;
  field_path: string;
  question: string;
}

export interface LLMCallRecord {
  call_id: string;
  provider: string;
  model: string;
  temperature: number;
  seed: number | null;
  prompt_version: string;
  schema_version: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  cost: number;
  response_hash: string;
}

export interface CompileResultDto {
  policy_id: string;
  status: "COMPILED" | "NEEDS_CONFIRMATION" | "REJECTED";
  template_id: string;
  rule_spec: Record<string, unknown> | null;
  questions: ConfirmationQuestion[];
  errors: string[];
  llm_call: LLMCallRecord | null;
}

export interface TemplateDto {
  id: string;
  scenario_type: ScenarioType;
  label: string;
  description: string;
  example_modification: string;
}

export interface RuleVersionDto {
  version_id: string;
  policy_id: string;
  version: number;
  template_id: string;
  rule_spec: Record<string, unknown>;
  content_hash: string;
  prompt_version: string;
}

export interface FrozenSnapshot {
  state_hash: string;
  state: {
    users: Array<Record<string, unknown>>;
    orders: Array<Record<string, unknown>>;
    coupons: Array<Record<string, unknown>>;
    memberships: Array<Record<string, unknown>>;
    entitlements: Array<Record<string, unknown>>;
  };
}

export interface FrozenReplay {
  classification: string;
  target_invariant: string;
  actions: FrozenAction[];
  snapshots: FrozenSnapshot[];
  receipts: Array<Record<string, unknown>>;
  events: Array<Record<string, unknown>>;
}

export interface FrozenDemo {
  provenance: {
    generated_by: string;
    honesty: string;
    sandbox_versions: string[];
    oracle_version: string;
  };
  rule: {
    template_id: string;
    chinese_modification: string;
    version_id: string;
    rule_spec: Record<string, unknown>;
  };
  run: AttackRun;
  counterexamples: CounterexampleRecord[];
  evidence: { vulnerable: FrozenReplay; fixed_regression: FrozenReplay };
  trace: TraceRecord[];
  events: RuntimeEvent[];
}
