/**
 * Agent Settings — Budget Field Visibility (Non-Admin User)
 *
 * Server-gates the budget fields (max_iterations, max_token_budget,
 * llm_max_tokens) to platform admins (T19). The settings tab also
 * visually hides the entire Budgets card for non-admins. This spec
 * runs under the org-user storage state and verifies none of the
 * budget labels appear.
 */

import { test, expect } from "./fixtures/api-fixture";

test.describe("Agent Settings — Budget Visibility (non-admin user)", () => {
	test("budget fields are not visible to non-admin users", async ({
		page,
		api,
	}) => {
		// Get an agent the user can see. Skip if the user has no agents
		// to view (we don't seed any in CI for non-admins).
		const agentsRes = await api.get("/api/agents");
		if (!agentsRes.ok()) {
			test.skip(true, "User cannot list agents");
			return;
		}
		const agents = await agentsRes.json();
		if (!agents.length) {
			test.skip(true, "No agents visible to this user");
			return;
		}
		const agent = agents[0];

		await page.goto(`/agents/${agent.id}`);
		await page.getByRole("tab", { name: /settings/i }).click();
		// Wait for the settings tab content to render.
		await expect(
			page.getByRole("textbox", { name: /name/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Budget fields must not appear for non-admins.
		await expect(page.getByLabel(/max iterations/i)).toHaveCount(0);
		await expect(page.getByLabel(/max token budget/i)).toHaveCount(0);
		await expect(
			page.getByLabel(/max tokens \/ response/i),
		).toHaveCount(0);

		await page.screenshot({
			path: "test-results/screenshots/agent-settings-no-budget.png",
			fullPage: true,
		});
	});
});
