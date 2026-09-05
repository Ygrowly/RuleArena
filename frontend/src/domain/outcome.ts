import type { AttackOutcome, AttackStatus } from "../api/types";

export interface UiState {
  key:
    | "idle"
    | "compiling"
    | "ambiguous"
    | "unsupported"
    | "rejected"
    | "ready"
    | "searching"
    | "replaying"
    | "recovering"
    | "confirmed"
    | "unconfirmed"
    | "no_violation"
    | "cancelled"
    | "cancel_requested"
    | "draft"
    | "needs_confirmation"
    | "failed"
    | "infra_failed"
    | "budget_exhausted";
  label: string;
  detail: string;
  tone: "neutral" | "info" | "warn" | "danger" | "success";
  busy: boolean;
}

export function describeStatus(status: AttackStatus, outcome: AttackOutcome | null): UiState {
  if (status === "COMPLETED") return describeOutcome(outcome);
  const byStatus: Partial<Record<AttackStatus, UiState>> = {
    DRAFT: {
      key: "draft",
      label: "草稿", detail: "规则尚未编译。", tone: "neutral", busy: false },
    NEEDS_CONFIRMATION: {
      key: "needs_confirmation",
      label: "待确认",
      detail: "规则存在歧义，需要显式确认后才能运行。",
      tone: "warn",
      busy: false,
    },
    READY: { key: "ready",
      label: "就绪", detail: "等待调度执行。", tone: "info", busy: false },
    SEARCHING: {
      key: "searching",
      label: "搜索中",
      detail: "策略 Agent 正在 Reference Simulator 中搜索可疑操作组合。",
      tone: "info",
      busy: true,
    },
    REPLAYING: {
      key: "replaying",
      label: "重放中",
      detail: "候选已提交，正在通过真实 Sandbox API 重放并由 Oracle 裁决。",
      tone: "info",
      busy: true,
    },
    RECOVERING: {
      key: "recovering",
      label: "恢复中",
      detail: "从持久化 Checkpoint 恢复运行状态。",
      tone: "info",
      busy: true,
    },
    CANCEL_REQUESTED: {
      key: "cancel_requested",
      label: "取消中",
      detail: "已收到取消信号，等待安全点退出。",
      tone: "warn",
      busy: true,
    },
    CANCELLED: {
      key: "cancelled",
      label: "已取消",
      detail: "运行已被取消。",
      tone: "neutral",
      busy: false,
    },
    FAILED: {
      key: "failed",
      label: "失败",
      detail: "基础设施失败，可从 Checkpoint 恢复。",
      tone: "danger",
      busy: false,
    },
  };
  return (
    byStatus[status] ?? {
      key: "idle",
      label: status,
      detail: "未知状态。",
      tone: "neutral",
      busy: false,
    }
  );
}

export function describeOutcome(outcome: AttackOutcome | null): UiState {
  const byOutcome: Partial<Record<AttackOutcome, UiState>> = {
    CONFIRMED_VIOLATION: {
      key: "confirmed",
      label: "已确认违规",
      detail: "候选反例经真实 Sandbox 重放并由 Oracle 判定违规，已通过最小化。",
      tone: "success",
      busy: false,
    },
    UNCONFIRMED_CANDIDATE: {
      key: "unconfirmed",
      label: "未确认候选",
      detail: "搜索发现可疑路径，但重放/Oracle 未确认，不能作为反例。",
      tone: "warn",
      busy: false,
    },
    // Honest semantics: budget exhausted is NOT "safe".
    NO_VIOLATION_WITHIN_BUDGET: {
      key: "no_violation",
      label: "预算内未发现违规",
      detail: "在给定步数/Token/时间预算内未找到可确认的违规。这不等于“规则安全”。",
      tone: "neutral",
      busy: false,
    },
    CANCELLED: {
      key: "cancelled",
      label: "已取消",
      detail: "运行在完成前被取消。",
      tone: "neutral",
      busy: false,
    },
    INFRA_FAILED: {
      key: "infra_failed",
      label: "基础设施失败",
      detail: "运行因基础设施问题失败，与业务结论无关。",
      tone: "danger",
      busy: false,
    },
  };
  if (outcome === null) {
    return {
      key: "idle",
      label: "未完成",
      detail: "运行尚未产出结论。",
      tone: "neutral",
      busy: false,
    };
  }
  return (
    byOutcome[outcome] ?? {
      key: outcome === "UNSUPPORTED_RULE" ? "unsupported" : "rejected",
      label: outcome,
      detail: "规则或请求未被接受。",
      tone: "warn",
      busy: false,
    }
  );
}

export const STRATEGY_META: Record<string, { label: string; color: string; focus: string }> = {
  VALUE_FLOW: {
    label: "价值流 VALUE_FLOW",
    color: "#2563eb",
    focus: "资金、优惠、积分、权益价值不守恒",
  },
  LIFECYCLE: {
    label: "生命周期 LIFECYCLE",
    color: "#9333ea",
    focus: "非法顺序、终态回退、跨生命周期组合",
  },
  BOUNDARY: {
    label: "边界 BOUNDARY",
    color: "#ea580c",
    focus: "重复、部分操作、重试与取消后重试",
  },
};
