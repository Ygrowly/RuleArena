import { expect, test } from "@playwright/test";

// The live-run UI flow against a stubbed backend: compile -> confirm -> create
// run -> arena -> honest outcome. No real model involved.
test("live run journey compiles, confirms, runs and reports honestly", async ({
  page,
}) => {
  const runId = "12345678-90ab-cdef-1234-567890abcdef";

  await page.route("**/api/templates", (route) =>
    route.fulfill({
      json: {
        templates: [
          {
            id: "promotion",
            scenario_type: "PROMOTION",
            label: "优惠券",
            description: "满减券规则。",
            example_modification: "满 150 减 50。",
          },
        ],
      },
    }),
  );
  await page.route("**/api/policies/compile", (route) =>
    route.fulfill({
      json: {
        policy_id: "policy-1",
        status: "COMPILED",
        template_id: "promotion",
        rule_spec: { scenario_type: "PROMOTION" },
        questions: [],
        errors: [],
        llm_call: null,
      },
    }),
  );
  await page.route("**/api/policies/policy-1/confirm", (route) =>
    route.fulfill({
      json: {
        version_id: "version-1",
        policy_id: "policy-1",
        version: 1,
        template_id: "promotion",
        content_hash: "hash",
        prompt_version: "v1",
      },
    }),
  );
  await page.route("**/api/runs", (route) =>
    route.fulfill({
      json: {
        run_id: runId,
        job_key: "job",
        rule_version_id: "version-1",
        scenario_version_id: "promotion-v1",
        sandbox_version: "vulnerable",
        oracle_version: "1.0",
        status: "SEARCHING",
        outcome: null,
        budget: { max_steps: 12, max_tokens: 12000, max_cost: 1.5, max_time_seconds: 90 },
        random_seed: 1,
        created_at: "2026-09-06T00:00:00Z",
      },
    }),
  );
  await page.route(`**/api/runs/${runId}`, (route) =>
    route.fulfill({
      json: {
        run_id: runId,
        job_key: "job",
        rule_version_id: "version-1",
        scenario_version_id: "promotion-v1",
        sandbox_version: "vulnerable",
        oracle_version: "1.0",
        status: "COMPLETED",
        outcome: "NO_VIOLATION_WITHIN_BUDGET",
        budget: { max_steps: 12, max_tokens: 12000, max_cost: 1.5, max_time_seconds: 90 },
        random_seed: 1,
        created_at: "2026-09-06T00:00:00Z",
      },
    }),
  );
  await page.route(`**/api/runs/${runId}/counterexamples`, (route) =>
    route.fulfill({ json: { counterexamples: [] } }),
  );
  await page.route(`**/api/runs/${runId}/trace`, (route) =>
    route.fulfill({ json: { trace: [], leakage_blocked: 0 } }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "实时运行" }).click();

  await page.getByLabel("业务模板").selectOption("promotion");
  await page.getByLabel("自然语言规则修改").fill("满 150 元减 50 元。");
  await page.getByRole("button", { name: "编译规则" }).click();
  await expect(page.getByText("规则已确认并冻结（content hash 绑定）。")).toBeVisible();

  await page.getByRole("button", { name: "启动实时运行" }).click();
  await expect(page.getByText("预算内未发现违规")).toBeVisible({ timeout: 15_000 });
  // Honest semantics: budget exhaustion must never be presented as "safe".
  await expect(page.getByText("规则安全", { exact: true })).toHaveCount(0);
});
