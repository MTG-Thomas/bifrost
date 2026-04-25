export function toFiniteNumber(value: unknown, fallback = 0): number {
	const rawValue = Array.isArray(value) ? value[0] : value;
	if (rawValue === undefined || rawValue === null) return fallback;
	const numericValue = Number(rawValue);
	return Number.isFinite(numericValue) ? numericValue : fallback;
}
