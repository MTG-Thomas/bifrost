import * as Sentry from "@sentry/react";

type SentryModule = Pick<typeof Sentry, "init">;
type SentryOptions = Parameters<typeof Sentry.init>[0];

function parseSampleRate(value: string | undefined): number {
	if (value === undefined || value.trim() === "") {
		return 0;
	}

	const parsed = Number(value);
	if (!Number.isFinite(parsed) || parsed < 0 || parsed > 1) {
		return 0;
	}

	return parsed;
}

export function configureSentry(sentryModule: SentryModule = Sentry): boolean {
	const dsn = import.meta.env.VITE_SENTRY_DSN;
	if (!dsn) {
		return false;
	}

	const release = import.meta.env.VITE_SENTRY_RELEASE?.trim();
	const options: SentryOptions = {
		dsn,
		environment: import.meta.env.VITE_SENTRY_ENVIRONMENT || import.meta.env.MODE,
		sendDefaultPii: false,
		tracesSampleRate: parseSampleRate(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE),
	};
	if (release) {
		options.release = release;
	}

	try {
		sentryModule.init(options);
	} catch (error) {
		console.warn("Sentry initialization failed; continuing without it", error);
		return false;
	}

	return true;
}
