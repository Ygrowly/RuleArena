import { describe, expect, it } from "vitest";

import { describeOutcome, describeStatus } from "./outcome";

describe("outcome semantics", () => {
  it("never presents budget exhaustion as safe", () => {
    const state = describeOutcome("NO_VIOLATION_WITHIN_BUDGET");
    expect(state.label).not.toContain("安全");
    expect(state.detail).toContain("不等于");
  });

  it("describes confirmed violations as Oracle-confirmed", () => {
    const state = describeOutcome("CONFIRMED_VIOLATION");
    expect(state.tone).toBe("success");
    expect(state.detail).toContain("Oracle");
  });

  it("marks infrastructure failure as unrelated to the business verdict", () => {
    const state = describeOutcome("INFRA_FAILED");
    expect(state.detail).toContain("与业务结论无关");
  });

  it("maps non-terminal statuses to busy states", () => {
    expect(describeStatus("SEARCHING", null).busy).toBe(true);
    expect(describeStatus("REPLAYING", null).busy).toBe(true);
    expect(describeStatus("CANCEL_REQUESTED", null).busy).toBe(true);
    expect(describeStatus("NEEDS_CONFIRMATION", null).tone).toBe("warn");
  });
});
