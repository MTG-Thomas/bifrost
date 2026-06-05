import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { render, waitFor } from "@testing-library/react";
import { hashOAuthState } from "@/services/auth";

import { AuthCallback } from "./AuthCallback";

const loginWithOAuth = vi.fn();
const navigate = vi.fn();

vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
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
	beforeEach(async () => {
		loginWithOAuth.mockResolvedValue(undefined);
		navigate.mockReset();
		sessionStorage.clear();
		sessionStorage.setItem(
			"oauth_state",
			await hashOAuthState("server-state"),
		);
	});

	it("validates the stored OAuth state digest before exchanging the code", async () => {
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
});
