import { describe, expect, it } from "vitest";

import { diffSnapshots, actionLabel } from "./diff";
import type { FrozenSnapshot } from "../api/types";

function snapshot(overrides: Record<string, unknown>): FrozenSnapshot {
  return {
    state_hash: "hash",
    state: {
      users: [],
      orders: [],
      coupons: [],
      memberships: [],
      entitlements: [],
      ...overrides,
    },
  };
}

describe("diffSnapshots", () => {
  it("highlights business-first changes between sandbox snapshots", () => {
    const before = snapshot({
      users: [{ id: "u1", balance: "400.00", points_balance: 100 }],
      orders: [
        {
          id: "o1",
          paid_amount: "100.00",
          refunded_amount: "0.00",
          status: "PAID",
          points_granted: 100,
          points_revoked: 0,
        },
      ],
    });
    const after = snapshot({
      users: [{ id: "u1", balance: "450.00", points_balance: 200 }],
      orders: [
        {
          id: "o1",
          paid_amount: "100.00",
          refunded_amount: "50.00",
          status: "PARTIALLY_REFUNDED",
          points_granted: 100,
          points_revoked: 0,
        },
      ],
    });
    const rows = diffSnapshots(before, after);
    const changed = Object.fromEntries(rows.filter((row) => row.changed).map((r) => [r.label, r]));
    expect(changed["累计退款"].before).toBe("0");
    expect(changed["累计退款"].after).toBe("50");
    expect(changed["积分余额"].after).toBe("200");
    expect(changed["订单状态"].after).toContain("PARTIALLY_REFUNDED");
    expect(changed["优惠券数"]).toBeUndefined();
  });

  it("treats a missing first snapshot as all-new", () => {
    const after = snapshot({ users: [{ id: "u1", points_balance: 5 }] });
    const rows = diffSnapshots(undefined, after);
    expect(rows.find((row) => row.label === "用户数")?.after).toBe("1");
  });
});

describe("actionLabel", () => {
  it("formats actions with target and arguments", () => {
    expect(
      actionLabel({
        action_type: "REFUND_ORDER",
        actor_id: "user-1",
        target_id: "order-1",
        idempotency_key: "k1",
        arguments: { amount: "50.00" },
      }),
    ).toBe("REFUND_ORDER → order-1 (amount=50.00)");
  });
});
