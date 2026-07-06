import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { ApiError, getErrorMessage, parseApiError } from "./api-error";

const fuzzRuns = Number(process.env.BIFROST_FAST_CHECK_RUNS ?? "100");
const fuzzTimeoutMs = Math.max(5000, Math.ceil(fuzzRuns / 4));

describe("api error property checks", () => {
	it("preserves arbitrary API error messages through parseApiError", () => {
		fc.assert(
			fc.property(
				fc.string(),
				fc.string(),
				fc.integer({ min: 100, max: 599 }),
				(errorCode, message, statusCode) => {
					const parsed = parseApiError(
						{ error: errorCode, message },
						statusCode,
					);

					expect(parsed).toBeInstanceOf(ApiError);
					expect(parsed.errorCode).toBe(errorCode);
					expect(parsed.message).toBe(message);
					expect(parsed.statusCode).toBe(statusCode);
				},
			),
			{ numRuns: fuzzRuns },
		);
	}, fuzzTimeoutMs);

	it("falls back for arbitrary non-object values without throwing", () => {
		fc.assert(
			fc.property(
				fc.oneof(fc.string(), fc.integer(), fc.boolean(), fc.constant(null)),
				fc.string({ minLength: 1 }),
				(value, fallback) => {
					expect(() => getErrorMessage(value, fallback)).not.toThrow();
					expect(typeof getErrorMessage(value, fallback)).toBe("string");
				},
			),
			{ numRuns: fuzzRuns },
		);
	}, fuzzTimeoutMs);
});
