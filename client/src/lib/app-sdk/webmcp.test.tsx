import { act, render } from "@testing-library/react";
import { useMemo } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
	isWebMcpAvailable,
	registerWebMcpTool,
	useWebMcpTool,
	type WebMcpTool,
} from "./webmcp";

const originalDescriptor = Object.getOwnPropertyDescriptor(
	document,
	"modelContext",
);

function installModelContext(
	registerTool = vi.fn().mockResolvedValue(undefined),
) {
	Object.defineProperty(document, "modelContext", {
		configurable: true,
		value: { registerTool },
	});
	return registerTool;
}

afterEach(() => {
	if (originalDescriptor)
		Object.defineProperty(document, "modelContext", originalDescriptor);
	else
		delete (document as Document & { modelContext?: unknown }).modelContext;
});

describe("registerWebMcpTool", () => {
	it("is a safe no-op when the browser does not support WebMCP", async () => {
		expect(isWebMcpAvailable()).toBe(false);
		const registration = registerWebMcpTool({
			name: "inspect-page",
			description: "Inspect the current page.",
			execute: async () => ({}),
		});

		expect(registration.registered).toBe(false);
		await registration.ready;
		expect(() => registration.unregister()).not.toThrow();
	});

	it("registers with an owned abort signal and unregisters idempotently", async () => {
		const registerTool = installModelContext();
		const tool: WebMcpTool = {
			name: "inspect-page",
			description: "Inspect the current page.",
			execute: async () => ({ ok: true }),
		};

		const registration = registerWebMcpTool(tool);
		await registration.ready;

		expect(registration.registered).toBe(true);
		expect(registerTool).toHaveBeenCalledOnce();
		const options = registerTool.mock.calls[0][1];
		expect(options.signal.aborted).toBe(false);
		registration.unregister();
		registration.unregister();
		expect(options.signal.aborted).toBe(true);
	});

	it("combines caller cancellation with an independently usable unregister", async () => {
		const registerTool = installModelContext();
		const caller = new AbortController();
		const registration = registerWebMcpTool(
			{
				name: "inspect-page",
				description: "Inspect the current page.",
				execute: async () => ({}),
			},
			{ signal: caller.signal },
		);
		const registeredSignal = registerTool.mock.calls[0][1]
			.signal as AbortSignal;
		expect(registeredSignal).not.toBe(caller.signal);
		registration.unregister();
		expect(registeredSignal.aborted).toBe(true);
	});

	it("reports synchronous browser registration failures through ready", async () => {
		installModelContext(
			vi.fn(() => {
				throw new Error("invalid schema");
			}),
		);
		const registration = registerWebMcpTool({
			name: "broken",
			description: "Broken tool.",
			execute: async () => ({}),
		});

		await expect(registration.ready).rejects.toThrow("invalid schema");
	});
});

describe("useWebMcpTool", () => {
	it("tracks component lifetime and calls the latest execute implementation", async () => {
		const registerTool = installModelContext();
		const first = vi.fn().mockResolvedValue("first");
		const second = vi.fn().mockResolvedValue("second");

		function Subject({ execute }: { execute: WebMcpTool["execute"] }) {
			const tool = useMemo<WebMcpTool>(
				() => ({
					name: "page-action",
					description: "Act on this page.",
					execute,
				}),
				[execute],
			);
			useWebMcpTool(tool);
			return null;
		}

		const view = render(<Subject execute={first} />);
		const registeredTool = registerTool.mock.calls[0][0] as WebMcpTool;
		await act(() =>
			registeredTool.execute(
				{},
				{ signal: new AbortController().signal },
			),
		);
		expect(first).toHaveBeenCalledOnce();

		view.rerender(<Subject execute={second} />);
		expect(registerTool).toHaveBeenCalledOnce();
		await act(() =>
			registeredTool.execute(
				{},
				{ signal: new AbortController().signal },
			),
		);
		expect(second).toHaveBeenCalledOnce();

		const signal = registerTool.mock.calls[0][1].signal as AbortSignal;
		view.unmount();
		expect(signal.aborted).toBe(true);
	});

	it("reports Permissions Policy and schema registration failures", async () => {
		const denied = new DOMException(
			"tools policy denied",
			"NotAllowedError",
		);
		installModelContext(vi.fn().mockRejectedValue(denied));
		const onRegistrationError = vi.fn();

		function Subject() {
			const tool = useMemo<WebMcpTool>(
				() => ({
					name: "page-action",
					description: "Act on this page.",
					execute: async () => ({}),
				}),
				[],
			);
			useWebMcpTool(tool, { onRegistrationError });
			return null;
		}

		render(<Subject />);
		await act(async () => Promise.resolve());
		expect(onRegistrationError).toHaveBeenCalledWith(denied);
	});
});
