import { describe, expect, it } from "vitest";

import { routeRevealKey } from "./route-reveal-key";

describe("routeRevealKey", () => {
	it("keeps the Settings shell mounted across subsection navigation", () => {
		expect(routeRevealKey("/settings/ai", "first")).toBe("settings");
		expect(routeRevealKey("/settings/github", "second")).toBe("settings");
	});

	it("keeps chat mounted while a conversation route becomes active", () => {
		expect(routeRevealKey("/chat", "new-chat")).toBe("chat");
		expect(routeRevealKey("/chat/conversation-1", "conversation")).toBe(
			"chat",
		);
		expect(routeRevealKey("/chat/artifacts", "artifacts")).toBe("artifacts");
	});

	it("keeps app runners mounted but remounts ordinary pages", () => {
		expect(routeRevealKey("/apps/example/preview", "preview")).toBe(
			"app-runner",
		);
		expect(routeRevealKey("/apps/example/edit/code", "edit")).toBe("edit");
		expect(routeRevealKey("/apps/example/edit", "edit-root")).toBe(
			"edit-root",
		);
		expect(routeRevealKey("/agents", "agents")).toBe("agents");
	});
});
