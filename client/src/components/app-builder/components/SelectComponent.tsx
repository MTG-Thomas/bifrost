/**
 * Select Component for App Builder
 *
 * Dropdown select with static or data-driven options and field tracking.
 * Expression evaluation is handled centrally by ComponentRegistry.
 */

import { useCallback, useEffect, useState, useMemo } from "react";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

type SelectComponent = components["schemas"]["SelectComponent"];
type SelectOption = components["schemas"]["SelectOption"];
import type { RegisteredComponentProps } from "../ComponentRegistry";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";

/**
 * Select Component
 *
 * Renders a dropdown select with static or dynamic options.
 * Value is tracked in the expression context under {{ field.<fieldId> }}.
 *
 * @example
 * // Static options
 * {
 *   id: "status-select",
 *   type: "select",
 *   props: {
 *     field_id: "status",
 *     label: "Status",
 *     options: [
 *       { value: "active", label: "Active" },
 *       { value: "inactive", label: "Inactive" },
 *       { value: "pending", label: "Pending" }
 *     ],
 *     default_value: "active"
 *   }
 * }
 *
 * @example
 * // Data-driven options
 * {
 *   id: "category-select",
 *   type: "select",
 *   props: {
 *     field_id: "categoryId",
 *     label: "Category",
 *     options_source: "categories",
 *     value_field: "id",
 *     label_field: "name"
 *   }
 * }
 */
export function SelectComponent({
	component,
	context,
}: RegisteredComponentProps) {
	const { props } = component as SelectComponent;

	// Props are pre-evaluated by ComponentRegistry
	const defaultValue = props.default_value ? String(props.default_value) : "";

	// Local state for the selected value
	const [value, setValue] = useState(defaultValue);

	// Props are pre-evaluated by ComponentRegistry (disabled is now boolean)
	const isDisabled = Boolean(props.disabled);
	const label = props.label ? String(props.label) : undefined;
	const placeholder = props.placeholder
		? String(props.placeholder)
		: "Select an option";

	// Build options from static config or data source
	// Note: options array is pre-evaluated by ComponentRegistry (evaluateDeep)
	const options: SelectOption[] = useMemo(() => {
		// If options were evaluated to an array, use them directly
		if (Array.isArray(props.options) && props.options.length > 0) {
			// Check if it's already SelectOption[] or raw data needing field mapping
			const firstOption = props.options[0];
			if (
				typeof firstOption === "object" &&
				firstOption !== null &&
				"value" in firstOption &&
				"label" in firstOption
			) {
				return props.options as SelectOption[];
			}
			// Raw data - apply field mapping
			const valueField = props.value_field || "value";
			const labelField = props.label_field || "label";
			return props.options.map((item): SelectOption => {
				const itemObj = item as unknown as Record<string, unknown>;
				return {
					value: String(itemObj[valueField] ?? ""),
					label: String(
						itemObj[labelField] ?? itemObj[valueField] ?? "",
					),
				};
			});
		}

		// If options_source is specified, get from workflow results
		if (props.options_source && context.workflow) {
			// Check if options_source references a workflow result (e.g., "get_options.result.items")
			const parts = props.options_source.split(".");
			const workflowKey = parts[0];
			const workflowResult = context.workflow[workflowKey];

			if (workflowResult?.result) {
				// Navigate to the nested path if specified (e.g., "result.items")
				let sourceData: unknown = workflowResult.result;
				const pathParts = parts.slice(1); // Skip the workflow key
				for (const part of pathParts) {
					if (part === "result") continue; // Skip "result" as it's already accessed
					if (
						sourceData &&
						typeof sourceData === "object" &&
						part in (sourceData as Record<string, unknown>)
					) {
						sourceData = (sourceData as Record<string, unknown>)[part];
					} else {
						sourceData = undefined;
						break;
					}
				}

				if (Array.isArray(sourceData)) {
					const valueField = props.value_field || "value";
					const labelField = props.label_field || "label";

					return sourceData.map((item) => ({
						value: String(item[valueField] ?? ""),
						label: String(item[labelField] ?? item[valueField] ?? ""),
					}));
				}
			}
		}

		return [];
	}, [
		props.options,
		props.options_source,
		props.value_field,
		props.label_field,
		context.workflow,
	]);

	// Get setFieldValue from context (stable reference)
	const setFieldValue = context.setFieldValue;

	// Update field value in context when value changes
	useEffect(() => {
		if (setFieldValue) {
			setFieldValue(props.field_id, value || null);
		}
	}, [props.field_id, value, setFieldValue]);

	// Initialize field value on mount
	useEffect(() => {
		if (setFieldValue && defaultValue) {
			setFieldValue(props.field_id, defaultValue);
		}
	}, [props.field_id, defaultValue, setFieldValue]);

	const handleChange = useCallback((newValue: string) => {
		setValue(newValue);
	}, []);

	const inputId = `field-${component.id}`;

	return (
		<div className={cn("space-y-2", props.class_name)}>
			{label && (
				<Label htmlFor={inputId}>
					{label}
					{props.required && (
						<span className="text-destructive ml-1">*</span>
					)}
				</Label>
			)}
			<Select
				value={value}
				onValueChange={handleChange}
				disabled={isDisabled}
				required={props.required ?? undefined}
			>
				<SelectTrigger id={inputId}>
					<SelectValue placeholder={placeholder} />
				</SelectTrigger>
				<SelectContent>
					{options.map((option) => (
						<SelectItem key={option.value} value={option.value}>
							{option.label}
						</SelectItem>
					))}
				</SelectContent>
			</Select>
		</div>
	);
}

export default SelectComponent;
