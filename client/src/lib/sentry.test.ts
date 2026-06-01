import { afterEach, describe, expect, it, vi } from "vitest";
import { configureSentry } from "./sentry";

describe("configureSentry", () => {
	afterEach(() => {
		vi.unstubAllEnvs();
		vi.restoreAllMocks();
	});

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

	it("omits blank release values", () => {
		vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
		vi.stubEnv("VITE_SENTRY_RELEASE", "");
		const sentry = { init: vi.fn() };

		expect(configureSentry(sentry)).toBe(true);
		expect(sentry.init).toHaveBeenCalledWith(
			expect.not.objectContaining({ release: expect.anything() }),
		);
	});

	it("falls back to MODE when VITE_SENTRY_ENVIRONMENT is unset", () => {
		vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
		vi.stubEnv("MODE", "development");
		const sentry = { init: vi.fn() };

		expect(configureSentry(sentry)).toBe(true);
		expect(sentry.init).toHaveBeenCalledWith(
			expect.objectContaining({ environment: "development" }),
		);
	});

	it("returns false when the SDK fails to initialize", () => {
		vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
		const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
		const sentry = {
			init: vi.fn(() => {
				throw new Error("bad DSN");
			}),
		};

		expect(configureSentry(sentry)).toBe(false);
		expect(warn).toHaveBeenCalledWith(
			"Sentry initialization failed; continuing without it",
			expect.any(Error),
		);
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

	it.each([
		["negative", "-0.5"],
		["NaN", "NaN"],
		["Infinity", "Infinity"],
		["empty", ""],
		["whitespace", "   "],
	])("falls back to 0 for %s sample rate", (_, value) => {
		vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
		vi.stubEnv("VITE_SENTRY_TRACES_SAMPLE_RATE", value);
		const sentry = { init: vi.fn() };

		expect(configureSentry(sentry)).toBe(true);
		expect(sentry.init).toHaveBeenCalledWith(
			expect.objectContaining({ tracesSampleRate: 0 }),
		);
	});
});
