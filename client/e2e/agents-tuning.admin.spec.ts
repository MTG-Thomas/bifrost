/**
 * Agent Tuning Page (Admin)
 *
 * Seeds an agent and navigates to its tune page. Without real flagged runs
 * the tuning UI shows its empty state — that's still a meaningful render.
 * Backend test `api/tests/e2e/api/test_agent_management_m1.py` covers the
 * full verdict → proposal → apply lifecycle at the API level.
 */

import { test, expect } from "@playwright/test";
import { seedAgentViaPage } from "./setup/seed-agent";

test.describe("Agent Tuning (admin)", () => {
	test("tuning page renders for an agent", async ({ page }) => {
		const agent = await seedAgentViaPage(page, {
			namePrefix: "Tune Spec",
		});

		await page.goto(`/agents/${agent.id}/tune`);
		await expect(
			page
				.getByRole("heading", { name: /tune agent/i })
				.or(page.getByText(/no flagged runs/i))
				.first(),
		).toBeVisible({ timeout: 10000 });

		// "Propose change" button renders when flagged runs exist; with zero
		// flagged runs it should be either absent or disabled. Either is fine.
		await page.screenshot({
			path: "test-results/screenshots/agent-tune.png",
			fullPage: true,
		});
	});
});
