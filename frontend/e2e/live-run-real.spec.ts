import { expect, test } from "@playwright/test";

/**
 * The live run against the real stack and a real model.
 *
 * `live-run.spec.ts` stubs every backend call, so it proves the UI flow and nothing
 * about the claim the project makes. This one walks the same journey through nginx,
 * the Control API, the queue, the worker, the real Sandbox and the Oracle, and then
 * insists the page states whatever actually happened.
 *
 * Opt-in: it needs `docker compose up`, a configured model, and up to 90 seconds for
 * the run itself, so the default suite stays hermetic.
 */
const enabled = process.env.E2E_LIVE_MODEL === "1";

test.describe("live run against the real backend", () => {
  test.skip(!enabled, "set E2E_LIVE_MODEL=1 with the full stack and a model configured");

  test("reaches a truthful terminal state inside the public budget", async ({ page }) => {
    test.setTimeout(180_000);

    await page.goto("/");
    await page.getByRole("button", { name: "实时运行" }).click();
    await page.getByLabel("业务模板").selectOption({ index: 0 });
    await page
      .getByLabel("自然语言规则修改")
      .fill("每消费 1 元获得 1 积分，退款时按退款金额撤销积分。");
    await page.getByRole("button", { name: "编译规则" }).click();

    // A real compile often surfaces ambiguities; they must be confirmed explicitly
    // before a version can be frozen and run.
    const confirmAmbiguity = page.getByRole("button", { name: /我已确认以上歧义/ });
    if (await confirmAmbiguity.isVisible().catch(() => false)) {
      await confirmAmbiguity.click();
    }
    await expect(page.getByText(/规则已确认并冻结/)).toBeVisible({ timeout: 60_000 });

    // The page must state the budget the service enforces, not a stale copy of it.
    await expect(page.getByText(/预算：12 步 \/ 100000 tokens/)).toBeVisible();

    await page.getByRole("button", { name: "启动实时运行" }).click();

    // Any of the three business outcomes is a valid result; what is not acceptable is
    // the run staying in progress, failing silently, or being dressed up as safety.
    await expect(
      page.getByText(/已确认违规|未确认候选|预算内未发现违规/),
      "the public run must reach a business outcome inside its 90-second budget",
    ).toBeVisible({ timeout: 150_000 });
    await expect(page.getByText("规则安全", { exact: true })).toHaveCount(0);
  });
});
