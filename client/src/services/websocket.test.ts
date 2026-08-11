import { afterEach, describe, expect, it, vi } from "vitest";
import {
	type PlatformJobUpdate,
	sanitizeLogText,
	webSocketService,
} from "./websocket";

describe("webSocketService security hardening", () => {
	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("sanitizes control characters before logging", () => {
		expect(sanitizeLogText("first\nsecond\rthird\tfourth")).toBe(
			"first second third fourth",
		);
	});

	it("dispatches git operation completion through registered callbacks", () => {
		const service = webSocketService as unknown as {
			handleMessage: (message: unknown) => void;
		};
		const callback = vi.fn();

		const unsubscribe = webSocketService.onGitOpComplete("job-1", callback);
		service.handleMessage({
			type: "git_op_complete",
			jobId: "job-1",
			status: "success",
			resultType: "status",
			data: { clean: true },
		});

		expect(callback).toHaveBeenCalledWith({
			status: "success",
			resultType: "status",
			data: { clean: true },
			error: undefined,
		});

		unsubscribe();
	});

	it("logs notification receipt without dumping attacker-controlled payloads", () => {
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		const service = webSocketService as unknown as {
			handleMessage: (message: unknown) => void;
		};

		service.handleMessage({
			type: "notification_created",
			notification: {
				id: "note-1\nforged",
				category: "system",
				title: "title",
				description: "description",
				status: "running\rforged",
				percent: null,
				error: null,
				result: null,
				metadata: { untrusted: "\nforged" },
				created_at: "2026-05-21T00:00:00Z",
				updated_at: "2026-05-21T00:00:00Z",
				user_id: "user-1",
			},
		});

		expect(warnSpy).toHaveBeenCalledWith("[WS] Notification received");
		expect(warnSpy.mock.calls[0]).toHaveLength(1);
	});
});

describe("platform-job WebSocket contract", () => {
	it("dispatches the same durable snapshot by job id", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onPlatformJobUpdate("job-1", callback);
		const job = {
			id: "job-1",
			job_type: "application.publish",
			payload_version: 1,
			organization_id: null,
			resource_type: "application",
			resource_id: "app-1",
			resource_lock_key: null,
			priority: 100,
			title: "Publishing Test",
			action_url: "/apps/test/edit",
			requested_by_user_id: "user-1",
			requested_by_name: "Dev",
			status: "running",
			progress: {
				phase: "promoting current bundle",
				current: 2,
				total: 3,
				percent: 66,
			},
			revision: 4,
			attempt: 1,
			max_attempts: 2,
			can_cancel: false,
			result: null,
			error: null,
			notification_id: "notification-1",
			started_at: "2026-07-28T12:00:00Z",
			completed_at: null,
			created_at: "2026-07-28T12:00:00Z",
			updated_at: "2026-07-28T12:00:01Z",
		} satisfies PlatformJobUpdate;

		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage({ type: "platform_job_updated", job });

		expect(callback).toHaveBeenCalledOnce();
		expect(callback).toHaveBeenCalledWith(job);
		unsubscribe();
	});
});
