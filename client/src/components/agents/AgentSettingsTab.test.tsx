/**
 * Tests for AgentSettingsTab.
 *
 * Mocks useAuth + create/update mutations at module scope. Exercises the
 * create vs edit path, admin-only budget visibility, and form submission.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockAuth(),
}));

const mockCreateMutation = vi.fn();
const mockUpdateMutation = vi.fn();
vi.mock("@/hooks/useAgents", async () => {
	const actual = await vi.importActual<typeof import("@/hooks/useAgents")>(
		"@/hooks/useAgents",
	);
	return {
		...actual,
		useCreateAgent: () => ({
			mutateAsync: mockCreateMutation,
			isPending: false,
		}),
		useUpdateAgent: () => ({
			mutateAsync: mockUpdateMutation,
			isPending: false,
		}),
	};
});

beforeEach(() => {
	mockAuth.mockReturnValue({ isPlatformAdmin: false });
	mockCreateMutation.mockReset();
	mockUpdateMutation.mockReset();
	mockCreateMutation.mockResolvedValue({ id: "new-agent-id", name: "Bot" });
	mockUpdateMutation.mockResolvedValue({});
});

async function renderTab(
	props: Partial<{
		mode: "create" | "edit";
		agent: Record<string, unknown> | null;
		onCreated: (id: string) => void;
	}> = {},
) {
	const { AgentSettingsTab } = await import("./AgentSettingsTab");
	return renderWithProviders(
		<AgentSettingsTab
			mode={props.mode ?? "edit"}
			// @ts-expect-error narrowed for tests
			agent={props.agent}
			onCreated={props.onCreated}
		/>,
	);
}

const existingAgent = {
	id: "agent-1",
	name: "Tier-1 Triage",
	description: "Triages support",
	system_prompt: "You are a triage bot.",
	channels: ["chat"],
	access_level: "role_based",
	is_active: true,
	tool_ids: [],
	delegated_agent_ids: [],
	role_ids: [],
	knowledge_sources: [],
	max_iterations: null,
	max_token_budget: null,
	llm_max_tokens: null,
};

describe("AgentSettingsTab — edit mode", () => {
	it("prepopulates fields from the agent", async () => {
		await renderTab({ mode: "edit", agent: existingAgent });
		const nameInput = screen.getByRole("textbox", {
			name: /^name$/i,
		}) as HTMLInputElement;
		expect(nameInput.value).toBe("Tier-1 Triage");
		const promptInput = screen.getByRole("textbox", {
			name: /system prompt/i,
		}) as HTMLTextAreaElement;
		expect(promptInput.value).toBe("You are a triage bot.");
	});

	it("submits via update mutation on Save", async () => {
		const { user } = await renderTab({
			mode: "edit",
			agent: existingAgent,
		});
		await user.click(
			screen.getByRole("button", { name: /save changes/i }),
		);
		await waitFor(() => {
			expect(mockUpdateMutation).toHaveBeenCalledTimes(1);
		});
		const args = mockUpdateMutation.mock.calls[0][0];
		expect(args.params.path.agent_id).toBe("agent-1");
		expect(args.body.name).toBe("Tier-1 Triage");
		expect(mockCreateMutation).not.toHaveBeenCalled();
	});

	it("hides the Budgets section for non-admin users", async () => {
		await renderTab({ mode: "edit", agent: existingAgent });
		expect(screen.queryByTestId("budget-card")).not.toBeInTheDocument();
	});

	it("shows the Budgets section for platform admins", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		await renderTab({ mode: "edit", agent: existingAgent });
		expect(screen.getByTestId("budget-card")).toBeInTheDocument();
	});
});

describe("AgentSettingsTab — create mode", () => {
	it("renders an empty form with Create label on the submit button", async () => {
		await renderTab({ mode: "create", agent: null });
		expect(
			screen.getByRole("button", { name: /create agent/i }),
		).toBeInTheDocument();
	});

	it("blocks submission when name + system prompt are empty", async () => {
		const { user } = await renderTab({ mode: "create", agent: null });
		await user.click(
			screen.getByRole("button", { name: /create agent/i }),
		);
		// Validation prevents the create mutation from firing.
		await waitFor(() => {
			expect(
				screen.getAllByText(/required/i).length,
			).toBeGreaterThan(0);
		});
		expect(mockCreateMutation).not.toHaveBeenCalled();
	});

	it("calls create mutation and onCreated with the new agent id", async () => {
		const onCreated = vi.fn();
		const { user } = await renderTab({
			mode: "create",
			agent: null,
			onCreated,
		});
		await user.type(
			screen.getByRole("textbox", { name: /^name$/i }),
			"Sales Bot",
		);
		await user.type(
			screen.getByRole("textbox", { name: /system prompt/i }),
			"Be helpful.",
		);
		await user.click(
			screen.getByRole("button", { name: /create agent/i }),
		);
		await waitFor(() => {
			expect(mockCreateMutation).toHaveBeenCalledTimes(1);
		});
		expect(mockCreateMutation.mock.calls[0][0].body.name).toBe(
			"Sales Bot",
		);
		expect(onCreated).toHaveBeenCalledWith("new-agent-id");
	});
});
