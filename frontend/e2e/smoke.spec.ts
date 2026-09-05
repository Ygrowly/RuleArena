import { expect, test } from "@playwright/test";

test.describe("golden journey smoke", () => {
  test("frozen demo renders the honest outcome without any backend calls", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "RuleArena" })).toBeVisible();
    await expect(page.getByText(/AI 搜索电商规则的异常操作组合/)).toBeVisible();
    await expect(page.getByText(/已完成的真实运行/)).toBeVisible();
    await expect(page.getByText("已确认违规", { exact: true })).toBeVisible();
    await expect(page.getByText(/REFUND_ORDER/).first()).toBeVisible();
    await expect(page.getByText(/Fixed v2 回归：旧反例不再成立/)).toBeVisible();
  });

  test("live run view loads business templates from the control API", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "实时运行" }).click();
    await expect(page.getByLabel("业务模板")).toBeVisible();
    const options = page.getByLabel("业务模板").locator("option");
    await expect(options).toHaveCount(3);
    await expect(page.getByText(/预算：12 步/)).toBeHidden(); // run panel appears after compile only
  });

  test("outcome semantics stay honest in the frozen view", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByText(/不等于「规则安全」|不等于“规则安全”/).first(),
    ).toBeVisible();
  });
});
