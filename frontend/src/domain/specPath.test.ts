import { describe, expect, it } from "vitest";

import { setAtPath, valueAtPath } from "./specPath";

const spec = {
  assets: [],
  rules: [
    { restore_on_full_refund: false, minimum_order_amount: { currency: "CNY" } },
  ],
};

describe("specPath", () => {
  it("reads the paths the compiler asks about", () => {
    expect(valueAtPath(spec, "assets")).toEqual([]);
    expect(valueAtPath(spec, "rules[0].restore_on_full_refund")).toBe(false);
    expect(valueAtPath(spec, "rules[0].minimum_order_amount.currency")).toBe("CNY");
    // A path that does not resolve is undefined, never a throw.
    expect(valueAtPath(spec, "rules[9].restore_on_full_refund")).toBeUndefined();
    expect(valueAtPath(spec, "rules[0].missing")).toBeUndefined();
    expect(valueAtPath(spec, "")).toBeUndefined();
  });

  it("writes an answer back without disturbing the rest of the spec", () => {
    const patched = setAtPath(spec, "rules[0].restore_on_full_refund", true);
    expect(valueAtPath(patched, "rules[0].restore_on_full_refund")).toBe(true);
    expect(valueAtPath(patched, "rules[0].minimum_order_amount.currency")).toBe("CNY");
    // The original is untouched, so a rejected answer cannot leak into the next attempt.
    expect(valueAtPath(spec, "rules[0].restore_on_full_refund")).toBe(false);
  });

  it("leaves a path with no home in the spec alone", () => {
    // `chinese_modification` is not part of the RuleSpec: the question about vague text
    // is answered by accepting the compiled result, and inventing a key would change the
    // contract the frozen version hashes.
    expect(setAtPath(spec, "chinese_modification", "text")).toEqual(spec);
    expect(setAtPath(spec, "rules[9].restore_on_full_refund", true)).toEqual(spec);
  });
});
