import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExecutionAttemptHistory } from "./ExecutionAttemptHistory";

describe("ExecutionAttemptHistory", () => {
	it("renders recorded attempts and bounded failure evidence", () => {
		render(
			<ExecutionAttemptHistory
				history={{
					coverage: "recorded",
					attempts: [
						{
							attempt_id: "a1",
							attempt_number: 1,
							status: "worker_lost",
							phase: "terminal",
							failure_code: "worker_process_lost",
							policy_version: "workflow-attempt/v1",
							created_at: "2026-08-31T11:59:59Z",
							claimed_at: "2026-08-31T12:00:00Z",
							duration_ms: 250,
						},
					],
				}}
			/>,
		);

		expect(screen.getByText("Attempt 1")).toBeInTheDocument();
		expect(screen.getByText("worker lost")).toBeInTheDocument();
		expect(screen.getByText("worker_process_lost")).toBeInTheDocument();
	});

	it("does not invent attempts for legacy executions", () => {
		render(
			<ExecutionAttemptHistory
				history={{ coverage: "legacy_unavailable", attempts: [] }}
			/>,
		);

		expect(
			screen.getByText(
				"Attempt history is unavailable for this legacy execution.",
			),
		).toBeInTheDocument();
	});

	it("distinguishes a tracked execution that never reached a worker", () => {
		render(
			<ExecutionAttemptHistory
				history={{ coverage: "recorded", attempts: [] }}
			/>,
		);

		expect(screen.getByText("No worker attempt was claimed.")).toBeInTheDocument();
	});
});
