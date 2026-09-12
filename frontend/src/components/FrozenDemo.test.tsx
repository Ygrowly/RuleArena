import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FrozenDemoView } from "./FrozenDemo";
import frozenJson from "../../public/frozen/golden-run.json";
import type { FrozenDemo } from "../api/types";

const demo: FrozenDemo = {
  provenance: {
    generated_by: "scripts/export_frozen_demo.py",
    honesty: "真实运行：由真实 Sandbox HTTP 重放产出。",
    sandbox_versions: ["vulnerable", "fixed"],
    oracle_version: "1.0",
  },
  rule: {
    template_id: "refund-points",
    chinese_modification: "每消费 1 元获得 1 积分，退款时按退款金额撤销积分。",
    version_id: "v-1",
    rule_spec: { scenario_type: "REFUND_POINTS" },
  },
  run: {
    run_id: "01234567-89ab-cdef-0123-456789abcdef",
    job_key: "frozen-demo",
    rule_version_id: "v-1",
    scenario_version_id: "refund-points-v1",
    sandbox_version: "vulnerable",
    oracle_version: "1.0",
    status: "COMPLETED",
    outcome: "CONFIRMED_VIOLATION",
    budget: { max_steps: 8, max_tokens: 1000, max_cost: 1, max_time_seconds: 30 },
    random_seed: 1,
    created_at: "2026-09-05T00:00:00Z",
  },
  counterexamples: [
    {
      counterexample_id: "c-1",
      attack_run_id: "01234567-89ab-cdef-0123-456789abcdef",
      candidate_key: "k",
      invariant_id: "POINTS_VALUE_CONSERVATION",
      original_actions: [],
      minimized_actions: [
        {
          action_type: "REFUND_ORDER",
          actor_id: "user-1",
          target_id: "order-1",
          idempotency_key: "k1",
          arguments: { amount: "50.00" },
        },
      ],
      replay_run_id: "r-1",
      created_at: "2026-09-05T00:00:00Z",
    },
  ],
  evidence: {
    vulnerable: {
      classification: "CONFIRMED_VIOLATION",
      target_invariant: "POINTS_VALUE_CONSERVATION",
      actions: [
        {
          action_type: "REFUND_ORDER",
          actor_id: "user-1",
          target_id: "order-1",
          idempotency_key: "k1",
          arguments: { amount: "50.00" },
        },
      ],
      snapshots: [
        {
          state_hash: "a",
          state: {
            users: [{ id: "u", points_balance: 100 }],
            orders: [],
            coupons: [],
            memberships: [],
            entitlements: [],
          },
        },
        {
          state_hash: "b",
          state: {
            users: [{ id: "u", points_balance: 250 }],
            orders: [],
            coupons: [],
            memberships: [],
            entitlements: [],
          },
        },
      ],
      receipts: [{ receipt_id: "r1", status: "SUCCEEDED" }],
      events: [{ event_type: "ORDER_REFUNDED" }],
    },
    fixed_regression: {
      classification: "MODEL_DIVERGENCE",
      target_invariant: "POINTS_VALUE_CONSERVATION",
      actions: [],
      snapshots: [],
      receipts: [],
      events: [],
    },
  },
  trace: [],
  events: [],
};

describe("FrozenDemoView", () => {
  it("renders the honest provenance note and confirmed outcome", () => {
    render(<FrozenDemoView demo={demo} />);
    expect(screen.getByText(/已完成的真实运行/)).toBeTruthy();
    expect(screen.getByText("已确认违规")).toBeTruthy();
    expect(screen.getByText(/Fixed v2 回归：旧反例不再成立/)).toBeTruthy();
    expect(screen.getByText(/REFUND_ORDER/)).toBeTruthy();
  });

  it("never claims NO_VIOLATION is safe", () => {
    const unsafe = {
      ...demo,
      run: { ...demo.run, outcome: "NO_VIOLATION_WITHIN_BUDGET" as const },
    };
    render(<FrozenDemoView demo={unsafe} />);
    expect(screen.getByText(/预算内未发现违规/)).toBeTruthy();
    // The only occurrence of "规则安全" is the honest disclaimer itself.
    expect(screen.queryByText("规则安全", { exact: true })).toBeNull();
    expect(screen.getByText(/不等于/)).toBeTruthy();
  });

  it("shows a loading placeholder while the frozen asset loads", () => {
    render(<FrozenDemoView demo={null} />);
    expect(screen.getByText(/正在加载冻结黄金案例/)).toBeTruthy();
  });
});

/**
 * The shipped demo asset, asserted directly.
 *
 * The tests above use a fixture, so they pass whatever the frozen run actually contains.
 * These read the committed JSON: if the export is regenerated with a scripted path or a
 * different profile, the demo's claims must change with it, and that is what they check.
 */
const frozen = frozenJson as unknown as FrozenDemo;

describe("the committed frozen demo", () => {
  it("claims the model found the path only when the export used one", () => {
    render(<FrozenDemoView demo={frozen} />);
    if (frozen.provenance.search_mode === "live_model") {
      expect(screen.getByText(/模型在 \d+ 步内搜索到/)).toBeTruthy();
    } else {
      // A scripted export must not be presented as a model finding.
      expect(screen.queryByText(/模型在 \d+ 步内搜索到/)).toBeNull();
      expect(screen.getByText(/路径由确定性脚本给出/)).toBeTruthy();
    }
  });

  it("marks the step that trips the invariant on the shipped run", () => {
    render(<FrozenDemoView demo={frozen} />);
    const tags = screen.getAllByText("违规发生在此步");
    // One in the evidence timeline, one in the vulnerable card of the comparison.
    expect(tags.length).toBeGreaterThan(0);
    const pointsBefore = pointsBalance(frozen.evidence.vulnerable.snapshots, -2);
    const pointsAfter = pointsBalance(frozen.evidence.vulnerable.snapshots, -1);
    expect(screen.getAllByText(pointsBefore).length).toBeGreaterThan(0);
    expect(screen.getAllByText(pointsAfter).length).toBeGreaterThan(0);
  });

  it("shows the fixed profile leaving the same path's points alone", () => {
    render(<FrozenDemoView demo={frozen} />);
    const fixedAfter = pointsBalance(frozen.evidence.fixed_regression.snapshots, -1);
    expect(screen.getByText(/Fixed v2 回归：旧反例不再成立/)).toBeTruthy();
    expect(screen.getAllByText(fixedAfter).length).toBeGreaterThan(0);
    expect(fixedAfter).not.toBe(pointsBalance(frozen.evidence.vulnerable.snapshots, -1));
  });

  it("states how many invariants the Oracle checked", () => {
    const findings = frozen.evidence.vulnerable.findings;
    expect(findings && findings.length).toBeGreaterThan(0);
    render(<FrozenDemoView demo={frozen} />);
    expect(screen.getByText(new RegExp(`全部 ${findings?.length} 条不变量`))).toBeTruthy();
  });
});

function pointsBalance(
  snapshots: FrozenDemo["evidence"]["vulnerable"]["snapshots"],
  index: number,
): string {
  const users = snapshots.at(index)?.state.users as { points_balance?: number }[] | undefined;
  return String(users?.[0]?.points_balance);
}
