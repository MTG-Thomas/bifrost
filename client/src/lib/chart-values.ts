export function toFiniteNumber(value: unknown, fallback = 0): number {
	const numericValue = Number(Array.isArray(value) ? value[0] : value);
	return Number.isFinite(numericValue) ? numericValue : fallback;
}
