/**
 * Tests for the Config list page.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const mockUseConfigs = vi.fn();
const mockUseDeleteConfig = vi.fn();

vi.mock("@/hooks/useConfig", () => ({
	useConfigs: (...a: unknown[]) => mockUseConfigs(...a),
	useDeleteConfig: (...a: unknown[]) => mockUseDeleteConfig(...a),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: false }),
}));

vi.mock("@/contexts/OrgScopeContext", () => ({
	useOrgScope: () => ({
		scope: { orgName: "Acme" },
		isGlobalScope: false,
	}),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [] }),
}));

vi.mock("@/components/config/ConfigDialog", () => ({
	ConfigDialog: () => null,
}));

vi.mock("@/components/ImportDialog", () => ({
	ImportDialog: () => null,
}));

const regularConfig = {
	id: "cfg-1",
	key: "api_token",
	value: "x",
	type: "string",
	scope: "global",
	org_id: null,
	description: "",
	integration_name: null,
};

beforeEach(() => {
	vi.clearAllMocks();
	mockUseConfigs.mockReturnValue({
		data: [regularConfig],
		isFetching: false,
		refetch: vi.fn(),
	});
	mockUseDeleteConfig.mockReturnValue({ mutate: vi.fn() });
});

async function renderPage() {
	const { Config } = await import("./Config");
	return renderWithProviders(<Config />);
}

describe("Config — list", () => {
	it("fetches without include_orphaned (orphaned UI stripped)", async () => {
		await renderPage();
		// useConfigs(scope, include_orphaned)
		expect(mockUseConfigs).toHaveBeenLastCalledWith(undefined, false);
		expect(screen.getByRole("checkbox", { name: /show orphaned/i })).not.toBeChecked();
	});
});
