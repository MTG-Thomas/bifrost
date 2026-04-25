import { describe, expect, it } from "vitest";

import { toFiniteNumber } from "./chart-values";

describe("toFiniteNumber", () => {
	it("returns numeric input", () => {
		expect(toFiniteNumber(42)).toBe(42);
	});

	it("uses the first array value", () => {
		expect(toFiniteNumber(["12", 99])).toBe(12);
	});

	it("returns the fallback for non-finite inputs", () => {
		expect(toFiniteNumber(Number.NaN, 7)).toBe(7);
		expect(toFiniteNumber(Number.POSITIVE_INFINITY, 7)).toBe(7);
	});

	it("returns the fallback for non-numeric or missing inputs", () => {
		expect(toFiniteNumber("not-a-number", 7)).toBe(7);
		expect(toFiniteNumber(undefined, 7)).toBe(7);
		expect(toFiniteNumber(null, 7)).toBe(7);
	});
});
