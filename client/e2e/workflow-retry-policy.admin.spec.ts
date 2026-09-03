import { test, expect } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}_${Math.floor(Math.random() * 10_000)}`;
const WORKFLOW_FUNCTION = `e2e_retry_policy_${UNIQUE}`;
const WORKFLOW_PATH = `e2e_retry_policy_${UNIQUE}.py`;
const WORKFLOW_CONTENT = `from bifrost import workflow

@workflow(name="${WORKFLOW_FUNCTION}")
async def ${WORKFLOW_FUNCTION}() -> dict:
    return {"ok": True}
`;

test.describe("Workflow infrastructure retry policy", () => {
	let workflowId: string | undefined;

	test.beforeAll(async ({ api }) => {
		const write = await api.put("/api/files/editor/content", {
			data: {
				path: WORKFLOW_PATH,
				content: WORKFLOW_CONTENT,
				encoding: "utf-8",
			},
		});
		expect(write.ok(), await write.text()).toBe(true);

		const register = await api.post("/api/workflows/register", {
			data: { path: WORKFLOW_PATH, function_name: WORKFLOW_FUNCTION },
		});
		expect(register.ok(), await register.text()).toBe(true);
		workflowId = ((await register.json()) as { id: string }).id;
	});

	test.afterAll(async ({ api }) => {
		if (workflowId) {
			const removeWorkflow = await api.delete(
				`/api/workflows/${workflowId}`,
				{
					data: { force_deactivation: true },
				},
			);
			expect(removeWorkflow.ok(), await removeWorkflow.text()).toBe(true);
		}
		const removeFile = await api.delete(
			`/api/files/editor?path=${encodeURIComponent(WORKFLOW_PATH)}`,
		);
		expect(removeFile.ok(), await removeFile.text()).toBe(true);
	});

	test("configures and persists an opt-in retry policy", async ({
		page,
		api,
	}) => {
		await page.goto("/workflows");
		await page.getByPlaceholder(/search by name/i).fill(WORKFLOW_FUNCTION);
		await page.getByLabel("Table view").click();

		const workflowRow = page.getByRole("row", {
			name: new RegExp(WORKFLOW_FUNCTION),
		});
		await expect(workflowRow).toBeVisible();
		await workflowRow
			.getByRole("button", { name: "Edit organization scope" })
			.click();

		await expect(
			page.getByRole("dialog", { name: "Edit Workflow Settings" }),
		).toBeVisible();
		await page.getByRole("tab", { name: "Execution" }).click();
		await page.getByLabel("Retry infrastructure failures").click();
		await page.getByLabel("Maximum attempts").fill("3");
		await page.getByLabel("Worker lease expires").click();
		await page.getByLabel("Workflow subprocess crashes").click();

		const updateResponse = page.waitForResponse(
			(response) =>
				response.request().method() === "PATCH" &&
				response.url().endsWith(`/api/workflows/${workflowId}`),
		);
		await page.getByRole("button", { name: "Save Changes" }).click();
		expect((await updateResponse).ok()).toBe(true);
		await expect(page.getByText("Workflow updated")).toBeVisible();

		const workflows = await api.get("/api/workflows");
		expect(workflows.ok(), await workflows.text()).toBe(true);
		const persisted = (
			(await workflows.json()) as Array<{
				id: string;
				retry_policy: unknown;
			}>
		).find((workflow) => workflow.id === workflowId);
		expect(persisted?.retry_policy).toEqual({
			version: "execution-retry/v1",
			enabled: true,
			max_attempts: 3,
			retry_on: ["worker_lost", "subprocess_crash"],
		});
	});
});
