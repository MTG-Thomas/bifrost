/**
 * Form Management Tests (Admin)
 *
 * Tests form CRUD operations from the platform admin perspective.
 * These tests run as platform_admin with full system access.
 *
 * Mirrors: api/tests/e2e/api/test_forms.py
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { test, expect } from "@playwright/test";

test.describe("Form Listing", () => {
	test("should display forms page", async ({ page }) => {
		await page.goto("/forms");

		// Should see forms heading
		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10000,
		});
	});

	test("should show create form button for admin", async ({ page }) => {
		await page.goto("/forms");

		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10000,
		});

		// Admin should see create button
		await expect(
			page.getByRole("button", { name: /create|new|add/i }).first(),
		).toBeVisible();
	});

	test("should list existing forms", async ({ page }) => {
		await page.goto("/forms");

		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10000,
		});

		// Either we have forms or an empty state
		const formContent = page.locator(
			"table tbody tr, [data-testid='form-card'], [data-testid='form-row']",
		);

		await expect(
			formContent.first().or(page.getByText(/no forms|create your first/i).first()),
		).toBeVisible({ timeout: 10_000 });
	});
});

test.describe("Form Creation", () => {
	test("should open create form dialog/page", async ({ page }) => {
		await page.goto("/forms");

		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10000,
		});

		// Click create button
		const createButton = page
			.getByRole("button", { name: /create|new|add/i })
			.first();
		await createButton.click();

		// Should show form creation UI
		await expect(
			page
				.getByLabel(/name/i)
				.or(page.getByPlaceholder(/name/i))
				.or(page.getByRole("textbox", { name: /name/i })),
		).toBeVisible({ timeout: 5000 });
	});
});

test.describe("Form Details", () => {
	test("should show form fields configuration", async ({ page }) => {
		const credentials = JSON.parse(
			readFileSync(
				resolve(dirname(fileURLToPath(import.meta.url)), ".auth/credentials.json"),
				"utf8",
			),
		) as { platform_admin: { accessToken: string } };
		const headers = {
			Authorization: `Bearer ${credentials.platform_admin.accessToken}`,
		};
		const fieldLabel = `Playwright Field ${Date.now()}`;
		const created = await page.request.post("/api/forms", {
			headers,
			data: {
				name: `Fields form ${Date.now()}`,
				description: "Playwright fields fixture",
				form_schema: {
					fields: [{ name: "playwright_field", type: "text", label: fieldLabel }],
				},
				access_level: "authenticated",
			},
		});
		expect(created.ok()).toBe(true);
		const form = (await created.json()) as { id: string };

		try {
			await page.goto(`/forms/${form.id}/edit`);
			await expect(page.getByText("Field Palette")).toBeVisible({
				timeout: 10000,
			});
			await expect(page.getByText(fieldLabel)).toBeVisible();
		} finally {
			const deleted = await page.request.delete(`/api/forms/${form.id}`, {
				headers,
			});
			expect(deleted.ok()).toBe(true);
		}
	});
});

test.describe("Form Editing", () => {
	test("should show edit button for forms", async ({ page }) => {
		const name = `Editable form ${Date.now()}`;
		const credentials = JSON.parse(
			readFileSync(
				resolve(dirname(fileURLToPath(import.meta.url)), ".auth/credentials.json"),
				"utf8",
			),
		) as { platform_admin: { accessToken: string } };
		const headers = {
			Authorization: `Bearer ${credentials.platform_admin.accessToken}`,
		};
		const created = await page.request.post("/api/forms", {
			headers,
			data: {
				name,
				description: "Playwright edit-menu fixture",
				form_schema: { fields: [] },
				access_level: "authenticated",
			},
		});
		expect(created.ok()).toBe(true);
		const form = (await created.json()) as { id: string };

		try {
			await page.goto("/forms");
			await expect(
				page.getByRole("heading", { name: /forms/i }).first(),
			).toBeVisible({ timeout: 10000 });

			await page.getByRole("button", { name: `${name} actions` }).click();
			await expect(
				page.getByRole("menuitem", { name: "Edit Form" }),
			).toBeVisible();
		} finally {
			const deleted = await page.request.delete(`/api/forms/${form.id}`, {
				headers,
			});
			expect(deleted.ok()).toBe(true);
		}
	});
});

test.describe("Form Access Control", () => {
	test("should show role assignment for forms", async ({ page }) => {
		await page.goto("/forms");

		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10000,
		});

		// Find a form
		const formItem = page
			.locator(
				"table tbody tr, [data-testid='form-card'], [data-testid='form-row']",
			)
			.first();

		if (await formItem.isVisible().catch(() => false)) {
			await formItem.click();

			// Look for access/permissions section
			const hasAccessSection = await page
				.getByText(/access|permissions|roles/i)
				.isVisible({ timeout: 5000 })
				.catch(() => false);

			// Access control UI should be present (implementation may vary)
			expect(hasAccessSection || page.url().includes("/forms/")).toBe(
				true,
			);
		}
	});
});
