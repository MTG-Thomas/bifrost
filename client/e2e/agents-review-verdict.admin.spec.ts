/**
 * Agent Review Flipbook (Admin)
 *
 * Smoke test for the per-agent review page (`/agents/:id/review`). We
 * can't seed completed runs without actually executing an agent, so
 * the assertion accepts either the flipbook UI (heading) or the empty
 * "nothing to review" state. Captures a screenshot for the visual
 * review pass.
 */

import { test, expect } from "./fixtures/api-fixture";

test.describe("Agent Run Review + Verdict (admin)", () => {
	test("review flipbook page renders for an agent", async ({
		page,
		api,
	}) => {
		// Get any agent to drive the URL. If none exist, skip — there's
		// nothing to review.
		const agentsRes = await api.get("/api/agents");
		const agents = await agentsRes.json();
		if (!agents.length) {
			test.skip(true, "No agents in test stack to review");
			return;
		}
		const agent = agents[0];

		await page.goto(`/agents/${agent.id}/review`);
		// Either the flipbook UI (heading) or the "nothing to review"
		// empty state must render.
		await expect(
			page
				.getByText(/nothing to review|no flagged runs/i)
				.or(page.getByRole("heading", { name: /review/i }))
				.first(),
		).toBeVisible({ timeout: 10000 });

		await page.screenshot({
			path: "test-results/screenshots/agent-review.png",
			fullPage: true,
		});
	});
});
