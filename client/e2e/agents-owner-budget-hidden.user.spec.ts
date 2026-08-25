/**
 * Agent Settings — Budget Field Visibility (Non-Admin User)
 *
 * Server-gates the budget fields (max_iterations, max_token_budget,
 * llm_max_tokens) to platform admins (T19). The settings tab also visually
 * hides both the Budgets section AND the Organization selector for non-admins.
 * This spec runs under the org-user storage state and verifies neither
 * appears.
 *
 * The org user seeds a private agent so the settings surface is reachable
 * without weakening the current owner-only access contract.
 */

import { test, expect } from "@playwright/test";
import { seedAgentViaPage } from "./setup/seed-agent";

test.describe("Agent Settings — Budget Visibility (non-admin user)", () => {
	test("budget fields are not visible to non-admin users", async ({
		page,
	}) => {
		const seededAgent = await seedAgentViaPage(page, {
			namePrefix: "Budget Vis Spec",
			accessLevel: "private",
		});

		await page.goto(`/agents/${seededAgent.id}`);
		await page.getByRole("tab", { name: /settings/i }).click();
		await expect(
			page.getByRole("textbox", { name: /name/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Budget fields must not appear for non-admins.
		await expect(page.getByLabel(/max iterations/i)).toHaveCount(0);
		await expect(page.getByLabel(/max token budget/i)).toHaveCount(0);
		await expect(
			page.getByLabel(/max tokens \/ response/i),
		).toHaveCount(0);

		// Organization selector is also admin-only (see AgentSettingsTab).
		await expect(page.getByLabel(/^organization/i)).toHaveCount(0);

		await page.screenshot({
			path: "test-results/screenshots/agent-settings-no-budget.png",
			fullPage: true,
		});
	});
});
