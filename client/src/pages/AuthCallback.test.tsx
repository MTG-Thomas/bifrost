import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";
import { render, screen, waitFor } from "@testing-library/react";

import { AuthCallback } from "./AuthCallback";

const loginWithOAuth = vi.fn();
const navigate = vi.fn();

vi.mock("react-router", async () => {
	const actual =
		await vi.importActual<typeof import("react-router")>(
			"react-router",
		);
	return {
		...actual,
		useNavigate: () => navigate,
	};
});

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		loginWithOAuth,
	}),
}));

describe("AuthCallback", () => {
	beforeEach(() => {
		loginWithOAuth.mockReset();
		loginWithOAuth.mockResolvedValue(undefined);
		navigate.mockReset();
		sessionStorage.clear();
	});

	it("lets the server validate OAuth state when no browser state is stored", async () => {
		const getItem = vi.spyOn(Storage.prototype, "getItem");
		const removeItem = vi.spyOn(Storage.prototype, "removeItem");

		render(
			<MemoryRouter
				initialEntries={[
					"/auth/callback/microsoft?code=auth-code&state=server-state",
				]}
			>
				<Routes>
					<Route
						path="/auth/callback/:provider"
						element={<AuthCallback />}
					/>
				</Routes>
			</MemoryRouter>,
		);

		await waitFor(() => {
			expect(loginWithOAuth).toHaveBeenCalledWith(
				"microsoft",
				"auth-code",
				"server-state",
			);
		});
		expect(navigate).toHaveBeenCalledWith("/", { replace: true });
		expect(getItem).toHaveBeenCalledWith("oauth_state");
		expect(removeItem).toHaveBeenCalledWith("oauth_state");
		expect(removeItem).not.toHaveBeenCalledWith("oauth_provider");
	});

	it("rejects and clears a mismatched stored OAuth state", async () => {
		const removeItem = vi.spyOn(Storage.prototype, "removeItem");
		sessionStorage.setItem("oauth_state", "stale-client-state");

		render(
			<MemoryRouter
				initialEntries={[
					"/auth/callback/microsoft?code=auth-code&state=server-state",
				]}
			>
				<Routes>
					<Route
						path="/auth/callback/:provider"
						element={<AuthCallback />}
					/>
				</Routes>
			</MemoryRouter>,
		);

		expect(await screen.findByText("Invalid OAuth state")).toBeTruthy();
		expect(loginWithOAuth).not.toHaveBeenCalled();
		expect(removeItem).toHaveBeenCalledWith("oauth_state");
	});
});
