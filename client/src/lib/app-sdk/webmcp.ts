/**
 * Progressive WebMCP support for standalone v2 applications.
 *
 * WebMCP is still an experimental browser API. Keep its structural types and
 * direct `document.modelContext` access in this module so applications do not
 * take a dependency on one browser's ambient declarations or a polyfill.
 */
import { useEffect, useRef } from "react";

import { useWorkflowMutation } from "./use-workflow-hooks";

export interface WebMcpToolAnnotations {
	readOnlyHint?: boolean;
	untrustedContentHint?: boolean;
}

export interface WebMcpExecuteOptions {
	signal: AbortSignal;
}

export interface WebMcpTool<
	TInput extends object = Record<string, unknown>,
	TResult = unknown,
> {
	name: string;
	title?: string;
	description: string;
	inputSchema?: Record<string, unknown>;
	annotations?: WebMcpToolAnnotations;
	execute: (
		input: TInput,
		options: WebMcpExecuteOptions,
	) => TResult | Promise<TResult>;
}

export interface WebMcpRegisterOptions {
	/** Expose the tool to explicitly trusted origins in addition to native agents. */
	exposedTo?: string[];
	/** Cancels registration. The SDK supplies its own signal when omitted. */
	signal?: AbortSignal;
}

interface ModelContextLike {
	registerTool: (
		tool: WebMcpTool<object, unknown>,
		options?: WebMcpRegisterOptions,
	) => Promise<void> | void;
}

type DocumentWithModelContext = Document & { modelContext?: ModelContextLike };

function getModelContext(): ModelContextLike | null {
	if (typeof document === "undefined") return null;
	const context = (document as DocumentWithModelContext).modelContext;
	return context && typeof context.registerTool === "function"
		? context
		: null;
}

/** Whether the current document has native (or deliberately installed) WebMCP support. */
export function isWebMcpAvailable(): boolean {
	return getModelContext() !== null;
}

/**
 * Register one tool and return an idempotent unregister callback.
 *
 * Unsupported browsers are a successful no-op. Registration failures remain
 * observable through `ready`; callers that care about Permissions Policy or
 * schema errors can await it, while React callers use `useWebMcpTool`.
 */
export function registerWebMcpTool<TInput extends object, TResult>(
	tool: WebMcpTool<TInput, TResult>,
	options: WebMcpRegisterOptions = {},
): { registered: boolean; ready: Promise<void>; unregister: () => void } {
	const context = getModelContext();
	if (!context) {
		return {
			registered: false,
			ready: Promise.resolve(),
			unregister: () => undefined,
		};
	}

	const controller = new AbortController();
	const externalSignal = options.signal;
	const abortFromExternal = () => controller.abort(externalSignal?.reason);
	if (externalSignal?.aborted) abortFromExternal();
	else
		externalSignal?.addEventListener("abort", abortFromExternal, {
			once: true,
		});
	let ready: Promise<void>;
	try {
		ready = Promise.resolve(
			context.registerTool(tool as WebMcpTool<object, unknown>, {
				...options,
				signal: controller.signal,
			}),
		);
	} catch (error) {
		ready = Promise.reject(error);
	}
	let unregistered = false;

	return {
		registered: true,
		ready,
		unregister: () => {
			if (unregistered) return;
			unregistered = true;
			externalSignal?.removeEventListener("abort", abortFromExternal);
			controller.abort();
		},
	};
}

export interface UseWebMcpToolOptions extends Omit<
	WebMcpRegisterOptions,
	"signal"
> {
	enabled?: boolean;
	/** Receives registration errors without creating an unhandled rejection. */
	onRegistrationError?: (error: unknown) => void;
}

/**
 * Register a page/state-local tool for the lifetime of the calling component.
 * Keep the tool object stable with `useMemo` when its definition is static.
 */
export function useWebMcpTool<TInput extends object, TResult>(
	tool: WebMcpTool<TInput, TResult> | null,
	options: UseWebMcpToolOptions = {},
): void {
	const executeRef = useRef(tool?.execute);
	executeRef.current = tool?.execute;
	const { enabled = true, exposedTo, onRegistrationError } = options;
	const exposedToKey = exposedTo?.join("\u0000") ?? "";

	useEffect(() => {
		if (!enabled || !tool) return;

		const registration = registerWebMcpTool(
			{
				...tool,
				execute: (input, executeOptions) => {
					if (!executeRef.current)
						throw new Error("WebMCP tool is no longer available");
					return executeRef.current(input as TInput, executeOptions);
				},
			},
			exposedTo ? { exposedTo } : {},
		);
		registration.ready.catch((error) => onRegistrationError?.(error));
		return registration.unregister;
		// Tool metadata/schema changes intentionally replace the registration.
		// execute is routed through a ref so ordinary callback identity changes do
		// not churn the browser's tool catalog.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [
		enabled,
		tool?.name,
		tool?.title,
		tool?.description,
		tool?.inputSchema,
		tool?.annotations,
		exposedToKey,
		onRegistrationError,
	]);
}

export interface UseWebMcpWorkflowToolDefinition<
	TInput extends Record<string, unknown> = Record<string, unknown>,
	TResult = unknown,
	TOutput = TResult,
> extends Omit<WebMcpTool<TInput, TOutput>, "execute"> {
	/** Portable `path::function` workflow reference. */
	workflowRef: string;
	/** Add current page/application context to the model-supplied input. */
	getWorkflowInput?: (input: TInput) => Record<string, unknown>;
	/** Update visible application state and optionally shape the tool result. */
	onSuccess?: (result: TResult, input: TInput) => TOutput | Promise<TOutput>;
}

/**
 * Expose a scoped Bifrost workflow as a page-local WebMCP tool.
 *
 * This uses the same provider transport and app/org scope as ordinary v2 app
 * workflow mutations. It does not call Bifrost's remote MCP endpoint.
 * A signal already aborted at invocation prevents submission. Once invoked,
 * cancellation must use Bifrost's execution controls because aborting the
 * browser tool cannot stop or undo workflow effects.
 */
export function useWebMcpWorkflowTool<
	TInput extends Record<string, unknown> = Record<string, unknown>,
	TResult = unknown,
	TOutput = TResult,
>(
	definition: UseWebMcpWorkflowToolDefinition<
		TInput,
		TResult,
		TOutput
	> | null,
	options: UseWebMcpToolOptions = {},
): void {
	const workflowRef = definition?.workflowRef ?? "";
	const mutation = useWorkflowMutation<TResult>(workflowRef);
	const definitionRef = useRef(definition);
	definitionRef.current = definition;

	const tool = definition
		? {
				name: definition.name,
				title: definition.title,
				description: definition.description,
				inputSchema: definition.inputSchema,
				annotations: definition.annotations,
				execute: async (
					input: TInput,
					{ signal }: WebMcpExecuteOptions,
				) => {
					if (signal.aborted)
						throw new DOMException(
							"Tool call was aborted",
							"AbortError",
						);
					const current = definitionRef.current;
					if (!current)
						throw new Error(
							"WebMCP workflow tool is no longer available",
						);
					const workflowInput =
						current.getWorkflowInput?.(input) ?? input;
					const result = await mutation.mutate(workflowInput);
					return current.onSuccess
						? current.onSuccess(result, input)
						: (result as unknown as TOutput);
				},
			}
		: null;

	useWebMcpTool(tool, options);
}
