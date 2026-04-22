/**
 * Agent Tuning Page (Admin)
 *
 * Smoke test for `/agents/:id/tune`. Without seeded flagged runs we
 * just verify the page renders and the tuning UI or empty state shows.
 * Captures a screenshot for the visual review pass.
 */

import { test, expect } from "./fixtures/api-fixture";

test.describe("Agent Tuning (admin)", () => {
	test("tuning page renders for an agent", async ({ page, api }) => {
		const agentsRes = await api.get("/api/agents");
		const agents = await agentsRes.json();
		if (!agents.length) {
			test.skip(true, "No agents in test stack");
			return;
		}
		const agent = agents[0];

		await page.goto(`/agents/${agent.id}/tune`);
		await expect(
			page
				.getByText(/no flagged runs|tune/i)
				.or(page.getByRole("heading"))
				.first(),
		).toBeVisible({ timeout: 10000 });

		await page.screenshot({
			path: "test-results/screenshots/agent-tune.png",
			fullPage: true,
		});
	});
});
