import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authFetch } from "@/lib/api-client";
import { fileService } from "./fileService";

vi.mock("@/lib/api-client", () => ({ authFetch: vi.fn() }));

const mockedAuthFetch = vi.mocked(authFetch);

describe("fileService.deletePath", () => {
	beforeEach(() => {
		vi.useFakeTimers();
		mockedAuthFetch.mockReset();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("waits for an asynchronous recursive deletion to succeed", async () => {
		mockedAuthFetch
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ job_id: "job-1" }), { status: 202 }),
			)
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ status: "running" }), { status: 200 }),
			)
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ status: "succeeded" }), { status: 200 }),
			);

		const deletion = fileService.deletePath("apps/example");
		await vi.waitFor(() => expect(mockedAuthFetch).toHaveBeenCalledTimes(2));
		await vi.advanceTimersByTimeAsync(500);
		await deletion;

		expect(mockedAuthFetch).toHaveBeenNthCalledWith(
			3,
			"/api/platform-jobs/job-1",
		);
	});

	it("surfaces an asynchronous deletion failure", async () => {
		mockedAuthFetch
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ job_id: "job-2" }), { status: 202 }),
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						status: "failed",
						error: { message: "storage unavailable" },
					}),
					{ status: 200 },
				),
			);

		await expect(fileService.deletePath("apps/example")).rejects.toThrow(
			"storage unavailable",
		);
	});
});
