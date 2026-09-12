/**
 * Short Chinese titles for the business invariants the Oracle checks.
 *
 * Presentation only: the authoritative statement of *why* something is a violation is
 * the Oracle's own `explanation`, which travels with the frozen payload and is shown
 * verbatim. These titles just save a non-technical reader from parsing an enum name.
 */
export const INVARIANT_TITLES_ZH: Record<string, string> = {
  NET_PAID_NON_NEGATIVE: "实付金额不为负",
  REFUND_NOT_EXCEED_PAID: "退款不超过实付",
  COUPON_SINGLE_CONSUMPTION: "优惠券不可重复使用",
  POINTS_VALUE_CONSERVATION: "积分价值守恒",
  ORDER_TERMINAL_MONOTONICITY: "订单终态不可回退",
  ENTITLEMENT_NON_NEGATIVE: "权益数量不为负",
  ENTITLEMENT_REFUND_CONSISTENCY: "退款与权益状态一致",
  IDEMPOTENT_EFFECT: "重复请求不产生二次效果",
};

export function invariantTitle(id: string): string {
  return INVARIANT_TITLES_ZH[id] ?? id;
}
