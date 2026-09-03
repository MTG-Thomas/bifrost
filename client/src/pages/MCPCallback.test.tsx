import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { MCPCallback } from "./MCPCallback";

describe("MCPCallback", () => {
	afterEach(() => {
		vi.restoreAllMocks();
		vi.unstubAllGlobals();
	});

	it("shows the detected client after a successful callback", async () => {
		const clearTimeoutSpy = vi.spyOn(globalThis, "clearTimeout");
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue({
				ok: true,
				json: async () => ({
					redirect_url: "cursor://callback?code=code-1",
					mcp_client: { label: "Cursor" },
				}),
			}),
		);

		const { unmount } = renderWithProviders(<MCPCallback />, {
			initialEntries: ["/mcp/callback?internal_state=state-1"],
		});

		expect(await screen.findByText("Authorization Complete")).toBeVisible();
		expect(
			screen.getByText("You can close this tab and return to Cursor."),
		).toBeVisible();

		unmount();
		expect(clearTimeoutSpy).toHaveBeenCalled();
	});

	it("uses generic copy when callback metadata is malformed", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue({
				ok: true,
				json: async () => ({
					redirect_url: "https://client.example/callback?code=code-1",
					mcp_client: { label: { untrusted: true } },
				}),
			}),
		);

		renderWithProviders(<MCPCallback />, {
			initialEntries: ["/mcp/callback?internal_state=state-1"],
		});

		expect(
			await screen.findByText(
				"You can close this tab and return to your MCP client.",
			),
		).toBeVisible();
	});
});
