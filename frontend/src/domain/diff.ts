import type { FrozenSnapshot, FrozenAction } from "../api/types";

interface Row {
  label: string;
  before: string;
  after: string;
  changed: boolean;
}

function sum(values: Array<string | number | undefined>): string {
  let total = 0;
  for (const value of values) {
    const n = typeof value === "number" ? value : Number(value ?? 0);
    if (!Number.isNaN(n)) total += n;
  }
  return String(total);
}

function describeState(snapshot: FrozenSnapshot | undefined): Map<string, string> {
  const map = new Map<string, string>();
  if (!snapshot) return map;
  const state = snapshot.state;
  map.set("用户数", String(state.users?.length ?? 0));
  map.set("用户余额(净支付)", sum((state.users ?? []).map((u) => u.balance as string)));
  map.set("积分余额", sum((state.users ?? []).map((u) => u.points_balance as number)));
  map.set("订单数", String(state.orders?.length ?? 0));
  map.set("累计实付", sum((state.orders ?? []).map((o) => o.paid_amount as string)));
  map.set("累计退款", sum((state.orders ?? []).map((o) => o.refunded_amount as string)));
  map.set(
    "订单状态",
    (state.orders ?? [])
      .map((o) => `${o.id}:${o.status}`)
      .join(", ") || "—",
  );
  map.set("优惠券数", String(state.coupons?.length ?? 0));
  map.set(
    "优惠券状态",
    (state.coupons ?? [])
      .map((c) => `${c.id}:${c.status ?? "?"}`)
      .join(", ") || "—",
  );
  map.set(
    "积分发放合计",
    sum((state.orders ?? []).map((o) => o.points_granted as number)),
  );
  map.set(
    "积分撤销合计",
    sum((state.orders ?? []).map((o) => o.points_revoked as number)),
  );
  map.set("会员数", String(state.memberships?.length ?? 0));
  map.set("权益数", String(state.entitlements?.length ?? 0));
  map.set(
    "权益已用次数",
    sum((state.entitlements ?? []).map((e) => e.used_count as number)),
  );
  return map;
}

/** Business-first state diff between two consecutive sandbox snapshots. */
export function diffSnapshots(before: FrozenSnapshot | undefined, after: FrozenSnapshot): Row[] {
  const beforeMap = describeState(before);
  const afterMap = describeState(after);
  const rows: Row[] = [];
  for (const [label, afterValue] of afterMap) {
    const beforeValue = beforeMap.get(label) ?? "—";
    rows.push({
      label,
      before: beforeValue,
      after: afterValue,
      changed: beforeValue !== afterValue,
    });
  }
  return rows;
}

export function actionLabel(action: FrozenAction): string {
  const target = action.target_id ? ` → ${action.target_id}` : "";
  const args = Object.entries(action.arguments)
    .map(([key, value]) => `${key}=${value}`)
    .join(", ");
  return `${action.action_type}${target}${args ? ` (${args})` : ""}`;
}

export const ACTION_LABELS_ZH: Record<string, string> = {
  CREATE_USER: "创建用户",
  CREATE_ORDER: "创建订单",
  PAY_ORDER: "支付订单",
  REFUND_ORDER: "退款",
  REDEEM_POINTS: "积分兑换",
  CONSUME_ENTITLEMENT: "消费权益",
  USE_COUPON: "使用优惠券",
};
