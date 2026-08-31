import { useMemo } from "react";
import type {
	UseFormGetFieldState,
	UseFormGetValues,
	UseFormSetFocus,
	UseFormSetValue,
	UseFormTrigger,
} from "react-hook-form";

import type { FormField } from "@/lib/client-types";
import { useWebMcpTool, type WebMcpTool } from "@/lib/app-sdk/webmcp";

type Values = Record<string, unknown>;

interface UseFormWebMcpArgs {
	enabled: boolean;
	formName: string;
	visibleFields: FormField[];
	providerLoading: Record<string, boolean>;
	providerErrors: Record<string, string>;
	providerLoaded: Set<string>;
	getValues: UseFormGetValues<Values>;
	getFieldState: UseFormGetFieldState<Values>;
	setValue: UseFormSetValue<Values>;
	setFocus: UseFormSetFocus<Values>;
	trigger: UseFormTrigger<Values>;
}

function fieldSummary(field: FormField) {
	return {
		name: field.name,
		label: field.label ?? field.name,
		type: field.type,
		required: field.required === true,
		...(field.placeholder ? { placeholder: field.placeholder } : {}),
		...(field.help_text ? { helpText: field.help_text } : {}),
		...(field.options
			? {
					options: field.options.map((option) => ({
						value: option.value,
						label: option.label,
					})),
				}
			: {}),
	};
}

function errorMessage(error: unknown): string | null {
	if (!error || typeof error !== "object" || !("message" in error))
		return null;
	return typeof error.message === "string" ? error.message : null;
}

function isEditableField(field: FormField): boolean {
	return !["markdown", "html", "file"].includes(field.type);
}

/** Register the safe, non-submitting WebMCP pilot for one authenticated form. */
export function useFormWebMcp({
	enabled,
	formName,
	visibleFields,
	providerLoading,
	providerErrors,
	providerLoaded,
	getValues,
	getFieldState,
	setValue,
	setFocus,
	trigger,
}: UseFormWebMcpArgs): void {
	const describeTool = useMemo<WebMcpTool>(
		() => ({
			name: "describe-current-form",
			title: "Describe current form",
			description:
				"Describes the fields currently visible in the open Bifrost form without returning entered values.",
			inputSchema: {
				type: "object",
				properties: {},
				additionalProperties: false,
			},
			annotations: { readOnlyHint: true },
			execute: async () => ({
				formName,
				fields: visibleFields.map(fieldSummary),
				note: "Visibility and validation are current UI state; server validation remains authoritative.",
			}),
		}),
		[formName, visibleFields],
	);

	const fillTool = useMemo<WebMcpTool<{ values: Values }>>(
		() => ({
			name: "fill-current-form",
			title: "Fill current form",
			description:
				"Fills editable fields currently visible in the open Bifrost form. It never submits the form and cannot populate file uploads.",
			inputSchema: {
				type: "object",
				properties: {
					values: {
						type: "object",
						description: "Map of visible field names to values.",
						additionalProperties: true,
					},
				},
				required: ["values"],
				additionalProperties: false,
			},
			annotations: { readOnlyHint: false },
			execute: async ({ values }) => {
				if (
					!values ||
					typeof values !== "object" ||
					Array.isArray(values)
				) {
					throw new Error(
						"values must be an object keyed by visible field name",
					);
				}
				const editable = new Map(
					visibleFields
						.filter(isEditableField)
						.map((field) => [field.name, field]),
				);
				const rejected = Object.keys(values).filter(
					(name) => !editable.has(name),
				);
				if (rejected.length) {
					throw new Error(
						`Fields are unknown, hidden, display-only, or file uploads: ${rejected.join(", ")}`,
					);
				}
				for (const [name, value] of Object.entries(values)) {
					setValue(name, value, {
						shouldDirty: true,
						shouldTouch: true,
						shouldValidate: true,
					});
				}
				await trigger(Object.keys(values));
				return {
					changedFields: Object.keys(values),
					currentValues: Object.fromEntries(
						Object.keys(values).map((name) => [
							name,
							getValues(name),
						]),
					),
					submitted: false,
				};
			},
		}),
		[visibleFields, setValue, trigger, getValues],
	);

	const validateTool = useMemo<WebMcpTool>(
		() => ({
			name: "validate-current-form",
			title: "Validate current form",
			description:
				"Runs the open form's current browser validation and reports visible field errors without submitting.",
			inputSchema: {
				type: "object",
				properties: {},
				additionalProperties: false,
			},
			annotations: { readOnlyHint: true },
			execute: async () => {
				const names = visibleFields
					.filter(isEditableField)
					.map((field) => field.name);
				const valid = await trigger(names);
				const fieldErrors = Object.fromEntries(
					names
						.map(
							(name) =>
								[
									name,
									errorMessage(getFieldState(name).error),
								] as const,
						)
						.filter(
							(entry): entry is [string, string] =>
								entry[1] !== null,
						),
				);
				const providers = visibleFields
					.filter(
						(field) =>
							field.has_dynamic_options || field.data_provider_id,
					)
					.map((field) => ({
						field: field.name,
						loading: providerLoading[field.name] === true,
						loaded: providerLoaded.has(field.name),
						...(providerErrors[field.name]
							? { error: providerErrors[field.name] }
							: {}),
					}));
				return {
					valid,
					fieldErrors,
					providers,
					authoritative: false,
					note: "The server performs authoritative validation if the user submits.",
				};
			},
		}),
		[
			visibleFields,
			trigger,
			getFieldState,
			providerLoading,
			providerLoaded,
			providerErrors,
		],
	);

	const focusTool = useMemo<WebMcpTool<{ field: string }>>(
		() => ({
			name: "focus-form-field",
			title: "Focus form field",
			description:
				"Moves focus to an editable field currently visible in the open Bifrost form.",
			inputSchema: {
				type: "object",
				properties: {
					field: {
						type: "string",
						description: "Visible field name.",
					},
				},
				required: ["field"],
				additionalProperties: false,
			},
			annotations: { readOnlyHint: true },
			execute: async ({ field }) => {
				const target = visibleFields.find(
					(item) => item.name === field && isEditableField(item),
				);
				if (!target)
					throw new Error(
						`Field is not an editable visible field: ${field}`,
					);
				setFocus(field);
				document
					.getElementById(field)
					?.scrollIntoView({ block: "center", behavior: "smooth" });
				return { focusedField: field };
			},
		}),
		[visibleFields, setFocus],
	);

	useWebMcpTool(enabled ? describeTool : null);
	useWebMcpTool(enabled ? fillTool : null);
	useWebMcpTool(enabled ? validateTool : null);
	useWebMcpTool(enabled ? focusTool : null);
}
