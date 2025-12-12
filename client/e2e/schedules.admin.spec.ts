/**
 * Schedule Management Tests (Admin)
 *
 * Tests scheduled workflow management from the platform admin perspective.
 * These tests run as platform_admin with full system access.
 *
 * Mirrors: api/tests/e2e/api/test_schedules.py
 */

import { test, expect } from "@playwright/test";

test.describe("Schedule Listing", () => {
	test("should display schedules page", async ({ page }) => {
		await page.goto("/schedules");

		// Should see "Scheduled Workflows" heading
		await expect(
			page.getByRole("heading", { name: /scheduled workflows/i }).first(),
		).toBeVisible({ timeout: 10000 });
	});

	test("should list scheduled workflows", async ({ page }) => {
		await page.goto("/schedules");

		await expect(
			page.getByRole("heading", { name: /scheduled workflows/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Should show schedule list or empty state
		const scheduleContent = page.locator(
			"table tbody tr, [data-testid='schedule-row'], [data-testid='schedule-card']",
		);

		const hasSchedules = await scheduleContent.count().catch(() => 0);
		// Empty state can be in a heading (CardTitle) or description
		const hasEmptyState = await page
			.locator("text=No Scheduled Workflows")
			.isVisible()
			.catch(() => false);

		expect(hasSchedules > 0 || hasEmptyState).toBe(true);
	});

	test("should show next run time", async ({ page }) => {
		await page.goto("/schedules");

		await expect(
			page.getByRole("heading", { name: /scheduled workflows/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// If schedules exist, should show next run
		const hasSchedules =
			(await page
				.locator("table tbody tr, [data-testid='schedule-row']")
				.count()
				.catch(() => 0)) > 0;

		if (hasSchedules) {
			await expect(
				page.getByText(/next run|next execution/i),
			).toBeVisible({ timeout: 5000 });
		}
	});
});

test.describe("Schedule Details", () => {
	test("should show schedule details when clicked", async ({ page }) => {
		await page.goto("/schedules");

		await expect(
			page.getByRole("heading", { name: /scheduled workflows/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Find a schedule
		const scheduleItem = page
			.locator(
				"table tbody tr, [data-testid='schedule-row'], [data-testid='schedule-card']",
			)
			.first();

		if (await scheduleItem.isVisible().catch(() => false)) {
			await scheduleItem.click();

			// Should show schedule details
			await page.waitForTimeout(1000);
			await expect(
				page.getByText(/cron|interval|frequency/i),
			).toBeVisible({ timeout: 5000 });
		}
	});

	test("should show execution history for schedule", async ({ page }) => {
		await page.goto("/schedules");

		await expect(
			page.getByRole("heading", { name: /scheduled workflows/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Find a schedule
		const scheduleItem = page
			.locator(
				"table tbody tr, [data-testid='schedule-row'], [data-testid='schedule-card']",
			)
			.first();

		if (await scheduleItem.isVisible().catch(() => false)) {
			await scheduleItem.click();

			// Should show history section
			await expect(
				page.getByText(/history|executions|runs/i),
			).toBeVisible({ timeout: 5000 });
		}
	});
});
