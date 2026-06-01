import { describe, expect, it, vi } from "vitest";
import { configureSentry } from "./sentry";

describe("configureSentry", () => {
	it("does not initialize Sentry without a DSN", () => {
		vi.stubEnv("VITE_SENTRY_DSN", "");
		const sentry = { init: vi.fn() };

		expect(configureSentry(sentry)).toBe(false);
		expect(sentry.init).not.toHaveBeenCalled();
	});

	it("initializes Sentry from Vite env with privacy-first defaults", () => {
		vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
		vi.stubEnv("VITE_SENTRY_ENVIRONMENT", "testing");
		vi.stubEnv("VITE_SENTRY_RELEASE", "bifrost-client@1.2.3");
		vi.stubEnv("VITE_SENTRY_TRACES_SAMPLE_RATE", "0.25");
		const sentry = { init: vi.fn() };

		expect(configureSentry(sentry)).toBe(true);
		expect(sentry.init).toHaveBeenCalledWith({
			dsn: "https://example@sentry.invalid/1",
			environment: "testing",
			release: "bifrost-client@1.2.3",
			sendDefaultPii: false,
			tracesSampleRate: 0.25,
		});
	});

	it("falls back to disabled tracing for invalid sample rates", () => {
		vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
		vi.stubEnv("VITE_SENTRY_TRACES_SAMPLE_RATE", "2");
		const sentry = { init: vi.fn() };

		expect(configureSentry(sentry)).toBe(true);
		expect(sentry.init).toHaveBeenCalledWith(
			expect.objectContaining({ tracesSampleRate: 0 }),
		);
	});
});
