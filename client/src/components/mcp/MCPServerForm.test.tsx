import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockApiPost = vi.fn();
const mockCreate = vi.fn();

vi.mock("@/lib/api-client", () => ({
	apiClient: {
		POST: (...args: unknown[]) => mockApiPost(...args),
	},
	$api: {
		useMutation: () => ({
			mutateAsync: mockCreate,
			isPending: false,
		}),
	},
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		warning: vi.fn(),
		error: vi.fn(),
	},
}));

import { MCPServerForm } from "./MCPServerForm";

beforeEach(() => {
	mockApiPost.mockReset();
	mockCreate.mockReset();
	mockCreate.mockResolvedValue({ id: "server-1" });
});

describe("MCPServerForm OAuth binding", () => {
	it("persists an explicit issuer and resource for manual OAuth setup", async () => {
		mockApiPost.mockResolvedValue({ data: { metadata: null } });
		const onSuccess = vi.fn();
		const { user } = renderWithProviders(
			<MCPServerForm onSuccess={onSuccess} />,
		);

		await user.type(screen.getByLabelText("Display name"), "Vendor MCP");
		await user.type(
			screen.getByLabelText("Server URL"),
			"https://resource.example.com/mcp",
		);
		await user.click(
			screen.getByRole("button", { name: "Discover OAuth metadata" }),
		);

		await user.type(
			await screen.findByLabelText("Authorization server issuer"),
			"https://issuer.example.com",
		);
		await user.type(
			screen.getByLabelText("Authorization URL"),
			"https://issuer.example.com/authorize",
		);
		await user.type(
			screen.getByLabelText("Token URL"),
			"https://issuer.example.com/token",
		);
		await user.type(
			screen.getByLabelText("Audience / resource indicator"),
			"https://resource.example.com/mcp",
		);
		await user.click(screen.getByRole("button", { name: "Create Server" }));

		await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
		expect(mockCreate).toHaveBeenCalledWith({
			body: expect.objectContaining({
				discovery_metadata: expect.objectContaining({
					issuer: "https://issuer.example.com",
					resource: "https://resource.example.com/mcp",
				}),
			}),
		});
		expect(onSuccess).toHaveBeenCalledWith("server-1");
	});

	it("refuses to create a manual OAuth server without an issuer", async () => {
		mockApiPost.mockResolvedValue({ data: { metadata: null } });
		const { user } = renderWithProviders(<MCPServerForm />);

		await user.type(screen.getByLabelText("Display name"), "Vendor MCP");
		await user.type(
			screen.getByLabelText("Server URL"),
			"https://resource.example.com/mcp",
		);
		await user.click(
			screen.getByRole("button", { name: "Discover OAuth metadata" }),
		);
		await user.type(
			screen.getByLabelText("Authorization URL"),
			"https://issuer.example.com/authorize",
		);
		await user.type(
			screen.getByLabelText("Token URL"),
			"https://issuer.example.com/token",
		);
		await user.click(screen.getByRole("button", { name: "Create Server" }));

		await waitFor(() => expect(mockCreate).not.toHaveBeenCalled());
	});

	it("reads issuer only from authorization-server metadata", async () => {
		mockApiPost.mockResolvedValue({
			data: {
				metadata: {
					authorization_server_metadata: {
						issuer: "https://issuer.example.com",
						authorization_endpoint:
							"https://issuer.example.com/authorize",
						token_endpoint: "https://issuer.example.com/token",
					},
					protected_resource_metadata: {
						resource: "https://resource.example.com/mcp",
						issuer: "https://attacker.example.com",
						authorization_endpoint:
							"https://attacker.example.com/authorize",
						token_endpoint: "https://attacker.example.com/token",
					},
					issuer: "https://attacker.example.com",
					authorization_endpoint:
						"https://attacker.example.com/authorize",
					token_endpoint: "https://attacker.example.com/token",
				},
			},
		});
		const { user } = renderWithProviders(<MCPServerForm />);

		await user.type(
			screen.getByLabelText("Server URL"),
			"https://resource.example.com/mcp",
		);
		await user.click(
			screen.getByRole("button", { name: "Discover OAuth metadata" }),
		);

		expect(
			await screen.findByLabelText("Authorization server issuer"),
		).toHaveValue("https://issuer.example.com");
		expect(screen.getByLabelText("Authorization URL")).toHaveValue(
			"https://issuer.example.com/authorize",
		);
		expect(screen.getByLabelText("Token URL")).toHaveValue(
			"https://issuer.example.com/token",
		);
	});
});
