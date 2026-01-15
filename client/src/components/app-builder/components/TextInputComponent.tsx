/**
 * Text Input Component for App Builder
 *
 * Text input field with label, placeholder, validation, and field tracking.
 * Expression evaluation is handled centrally by ComponentRegistry.
 */

import { useCallback, useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

type TextInputComponent = components["schemas"]["TextInputComponent"];
import type { RegisteredComponentProps } from "../ComponentRegistry";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * Text Input Component
 *
 * Renders a text input field with label, placeholder, and validation.
 * Value is tracked in the expression context under {{ field.<fieldId> }}.
 *
 * @example
 * // Basic text input
 * {
 *   id: "name-input",
 *   type: "text-input",
 *   props: {
 *     field_id: "userName",
 *     label: "Name",
 *     placeholder: "Enter your name",
 *     required: true
 *   }
 * }
 *
 * @example
 * // Email input with validation
 * {
 *   id: "email-input",
 *   type: "text-input",
 *   props: {
 *     field_id: "userEmail",
 *     label: "Email Address",
 *     input_type: "email",
 *     required: true
 *   }
 * }
 */
export function TextInputComponent({
	component,
	context,
}: RegisteredComponentProps) {
	const { props } = component as TextInputComponent;

	// Props are pre-evaluated by ComponentRegistry
	const defaultValue = props.default_value ? String(props.default_value) : "";

	// Local state for the input value
	const [value, setValue] = useState(defaultValue);

	// Props are pre-evaluated by ComponentRegistry (disabled is now boolean)
	const isDisabled = Boolean(props.disabled);
	const label = props.label ? String(props.label) : undefined;
	const placeholder = props.placeholder
		? String(props.placeholder)
		: undefined;

	// Get setFieldValue from context (stable reference)
	const setFieldValue = context.setFieldValue;

	// Update field value in context when value changes
	useEffect(() => {
		if (setFieldValue) {
			setFieldValue(props.field_id, value);
		}
	}, [props.field_id, value, setFieldValue]);

	// Initialize field value on mount
	useEffect(() => {
		if (setFieldValue && defaultValue) {
			setFieldValue(props.field_id, defaultValue);
		}
	}, [props.field_id, defaultValue, setFieldValue]);

	const handleChange = useCallback(
		(e: React.ChangeEvent<HTMLInputElement>) => {
			setValue(e.target.value);
		},
		[],
	);

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
			<Input
				id={inputId}
				type={props.input_type || "text"}
				value={value}
				onChange={handleChange}
				placeholder={placeholder}
				disabled={isDisabled}
				required={props.required ?? undefined}
				minLength={props.min_length ?? undefined}
				maxLength={props.max_length ?? undefined}
				pattern={props.pattern ?? undefined}
			/>
		</div>
	);
}

export default TextInputComponent;
