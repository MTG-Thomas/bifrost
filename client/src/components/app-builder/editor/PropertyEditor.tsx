/**
 * Property Editor Panel for App Builder Visual Editor
 *
 * Displays editable properties for the currently selected component,
 * organized into sections using accordions.
 */

import { useState, useCallback, useMemo } from "react";
import { Trash2, Shield, AlertTriangle } from "lucide-react";
import { useRoles } from "@/hooks/useRoles";
import { $api } from "@/lib/api-client";
import {
	WorkflowSelectorDialog,
	type EntityRole,
} from "@/components/workflows/WorkflowSelectorDialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
	Accordion,
	AccordionContent,
	AccordionItem,
	AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type {
	AppComponent,
	LayoutContainer,
	ComponentType,
	LayoutType,
	HeadingLevel,
	LayoutAlign,
	LayoutJustify,
	LayoutMaxWidth,
	ButtonActionType,
	PageDefinition,
} from "@/lib/app-builder-helpers";
import { isLayoutContainer } from "@/lib/app-builder-helpers";
import {
	KeyValueEditor,
	ColumnBuilder,
	OptionBuilder,
	TableActionBuilder,
	WorkflowParameterEditor,
} from "./property-editors";

/**
 * Helper type for accessing optional properties that may exist on some layout container types.
 * The API uses snake_case names. This interface provides a superset of all layout properties
 * for easier access with optional chaining when the LayoutContainer union doesn't have a property.
 */
interface LayoutContainerProps {
	id: string;
	type: "row" | "column" | "grid";
	children?: unknown[];
	gap?: number | string | null;
	padding?: number | string | null;
	align?: "start" | "center" | "end" | "stretch" | null;
	justify?: "start" | "center" | "end" | "between" | "around" | null;
	distribute?: "natural" | "equal" | "fit" | null;
	max_width?: "sm" | "md" | "lg" | "xl" | "2xl" | "full" | "none" | null;
	max_height?: number | null;
	overflow?: "auto" | "scroll" | "hidden" | "visible" | null;
	sticky?: "top" | "bottom" | null;
	sticky_offset?: number | null;
	columns?: number | string;
	class_name?: string | null;
	visible?: string | null;
	style?: Record<string, unknown> | null;
}

export interface PropertyEditorProps {
	/** Currently selected component or layout container */
	component: AppComponent | LayoutContainer | null;
	/** Callback when properties change */
	onChange: (updates: Partial<AppComponent | LayoutContainer>) => void;
	/** Callback when component should be deleted */
	onDelete?: () => void;
	/** Page being edited (for page-level settings like launch workflow) */
	page?: PageDefinition;
	/** Callback when page properties change */
	onPageChange?: (updates: Partial<PageDefinition>) => void;
	/** App-level access control settings */
	appAccessLevel?: "authenticated" | "role_based";
	/** Role IDs allowed for the app (when role_based) */
	appRoleIds?: string[];
	/** Additional CSS classes */
	className?: string;
}

/** Field wrapper component for consistent form field styling */
function FormField({
	label,
	children,
	description,
}: {
	label: string;
	children: React.ReactNode;
	description?: string;
}) {
	return (
		<div className="space-y-2">
			<Label className="text-sm font-medium">{label}</Label>
			{children}
			{description && (
				<p className="text-xs text-muted-foreground">{description}</p>
			)}
		</div>
	);
}

/** JSON editor for complex object values */
function JsonEditor({
	value,
	onChange,
	rows = 6,
}: {
	value: unknown;
	onChange: (value: unknown) => void;
	rows?: number;
}) {
	const [jsonString, setJsonString] = useState(() =>
		JSON.stringify(value, null, 2),
	);
	const [error, setError] = useState<string | null>(null);

	const handleChange = useCallback(
		(newValue: string) => {
			setJsonString(newValue);
			try {
				const parsed = JSON.parse(newValue);
				setError(null);
				onChange(parsed);
			} catch {
				setError("Invalid JSON");
			}
		},
		[onChange],
	);

	return (
		<div className="space-y-1">
			<Textarea
				value={jsonString}
				onChange={(e) => handleChange(e.target.value)}
				rows={rows}
				className={cn(
					"font-mono text-xs",
					error &&
						"border-destructive focus-visible:border-destructive",
				)}
			/>
			{error && <p className="text-xs text-destructive">{error}</p>}
		</div>
	);
}

/** Common properties section for all components */
function CommonPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent | LayoutContainer;
	onChange: (updates: Partial<AppComponent | LayoutContainer>) => void;
}) {
	const isLayout = isLayoutContainer(component);
	const id = isLayout ? undefined : (component as AppComponent).id;
	const appComponent = !isLayout ? (component as AppComponent) : null;

	return (
		<AccordionItem value="common">
			<AccordionTrigger>Common</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				{id && (
					<FormField
						label="ID"
						description="Unique component identifier"
					>
						<Input value={id} disabled className="bg-muted" />
					</FormField>
				)}
				<FormField
					label="Visibility Expression"
					description="Expression to control visibility (e.g., {{ user.role == 'admin' }})"
				>
					<Input
						value={component.visible ?? ""}
						onChange={(e) =>
							onChange({ visible: e.target.value || undefined })
						}
						placeholder="Always visible"
					/>
				</FormField>

				{appComponent && (
					<>
						<FormField
							label="Grid Span"
							description="Columns to span (for components in grid layouts)"
						>
							<Input
								type="number"
								value={appComponent.grid_span ?? 1}
								onChange={(e) =>
									onChange({
										grid_span: Number(e.target.value),
									})
								}
								min={1}
								max={12}
							/>
						</FormField>

						<FormField
							label="CSS Classes"
							description="Custom Tailwind or CSS classes"
						>
							<Input
								value={appComponent.class_name ?? ""}
								onChange={(e) =>
									onChange({
										class_name: e.target.value || undefined,
									})
								}
								placeholder="text-blue-500 font-bold"
							/>
						</FormField>

						<Accordion type="single" collapsible>
							<AccordionItem value="inline-styles">
								<AccordionTrigger>Inline Styles</AccordionTrigger>
								<AccordionContent>
									<JsonEditor
										value={appComponent.style ?? {}}
										onChange={(value) =>
											onChange({ style: value as Record<string, unknown> })
										}
										rows={4}
									/>
									<p className="text-xs text-muted-foreground mt-2">
										Use camelCase: maxHeight, backgroundColor
									</p>
								</AccordionContent>
							</AccordionItem>

							<AccordionItem value="repeat">
								<AccordionTrigger>Repeat Component</AccordionTrigger>
								<AccordionContent className="space-y-4">
									<FormField
										label="Items Expression"
										description="Array to iterate over"
									>
										<Input
											value={appComponent.repeat_for?.items ?? ""}
											onChange={(e) =>
												onChange({
													repeat_for: e.target.value
														? {
																items: e.target.value,
																as: appComponent.repeat_for?.as || "item",
																item_key: appComponent.repeat_for?.item_key || "id",
														  }
														: undefined,
												})
											}
											placeholder="{{ workflow.clients }}"
										/>
									</FormField>

									{appComponent.repeat_for && (
										<>
											<FormField
												label="Loop Variable"
												description="Name to access each item"
											>
												<Input
													value={appComponent.repeat_for.as}
													onChange={(e) =>
														onChange({
															repeat_for: {
																items: appComponent.repeat_for!.items,
																as: e.target.value,
																item_key: appComponent.repeat_for!.item_key,
															},
														})
													}
													placeholder="client"
												/>
											</FormField>

											<FormField
												label="Item Key Property"
												description="Unique property for React keys"
											>
												<Input
													value={appComponent.repeat_for.item_key}
													onChange={(e) =>
														onChange({
															repeat_for: {
																items: appComponent.repeat_for!.items,
																as: appComponent.repeat_for!.as,
																item_key: e.target.value,
															},
														})
													}
													placeholder="id"
												/>
											</FormField>
										</>
									)}
								</AccordionContent>
							</AccordionItem>
						</Accordion>
					</>
				)}
			</AccordionContent>
		</AccordionItem>
	);
}

/** Layout-specific properties */
/**
 * Page Properties Section
 * Shows page-level settings like title, path, data sources, and launch workflow
 */
function PagePropertiesSection({
	page,
	onChange,
	appAccessLevel,
	appRoleIds,
}: {
	page: PageDefinition;
	onChange: (updates: Partial<PageDefinition>) => void;
	appAccessLevel?: "authenticated" | "role_based";
	appRoleIds?: string[];
}) {
	// Dialog state for launch workflow selector
	const [launchWorkflowDialogOpen, setLaunchWorkflowDialogOpen] =
		useState(false);

	// Fetch roles for page-level access control
	const { data: rolesData } = useRoles();

	// Fetch workflows for display name lookup
	const { data: workflowsData } = $api.useQuery("get", "/api/workflows", {});

	// Convert appRoleIds to EntityRole[] format for the dialog
	const entityRoles: EntityRole[] = useMemo(
		() =>
			rolesData
				?.filter((r) => appRoleIds?.includes(r.id))
				.map((r) => ({ id: r.id, name: r.name })) ?? [],
		[rolesData, appRoleIds],
	);

	// Get launch workflow name for button display
	const launchWorkflowName = useMemo(
		() =>
			workflowsData?.find((w) => w.id === page.launch_workflow_id)?.name,
		[workflowsData, page.launch_workflow_id],
	);

	// Get current page-level allowed roles
	const pageAllowedRoles = page.permission?.allowed_roles ?? [];

	// Filter roles to only show those that are allowed at app level
	const availableRoles =
		rolesData?.filter(
			(role) => !appRoleIds?.length || appRoleIds.includes(role.id),
		) ?? [];

	// Handle role toggle
	const handleRoleToggle = (roleId: string, checked: boolean) => {
		const currentRoles = pageAllowedRoles;
		const newRoles = checked
			? [...currentRoles, roleId]
			: currentRoles.filter((id: string) => id !== roleId);

		onChange({
			permission: {
				...page.permission,
				allowed_roles: newRoles.length > 0 ? newRoles : undefined,
			},
		});
	};

	// Handle launch workflow selection from dialog
	const handleLaunchWorkflowSelect = useCallback(
		(workflowIds: string[], _assignRoles: boolean) => {
			const workflowId = workflowIds[0] || undefined;
			onChange({
				launch_workflow_id: workflowId,
				launch_workflow_params: workflowId
					? page.launch_workflow_params
					: undefined,
				launch_workflow_data_source_id: workflowId
					? page.launch_workflow_data_source_id
					: undefined,
			});
		},
		[onChange, page.launch_workflow_params, page.launch_workflow_data_source_id],
	);

	return (
		<AccordionItem value="page">
			<AccordionTrigger>Page Settings</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Title"
					description="Page title shown in navigation"
				>
					<Input
						value={page.title}
						onChange={(e) => onChange({ title: e.target.value })}
						placeholder="Page Title"
					/>
				</FormField>

				<FormField label="Path" description="URL path for this page">
					<Input
						value={page.path}
						onChange={(e) => onChange({ path: e.target.value })}
						placeholder="/page-path"
					/>
				</FormField>

				{/* Launch Workflow Section */}
				<div className="pt-2 border-t">
					<FormField
						label="Launch Workflow"
						description="Workflow when page loads. Access via {{ workflow.<dataSourceId> }}"
					>
						<div className="flex gap-2">
							<Button
								variant="outline"
								className="flex-1 justify-start font-normal"
								onClick={() => setLaunchWorkflowDialogOpen(true)}
							>
								{launchWorkflowName || "Select launch workflow (optional)"}
							</Button>
							{page.launch_workflow_id && (
								<Button
									variant="ghost"
									size="icon"
									onClick={() =>
										onChange({
											launch_workflow_id: undefined,
											launch_workflow_params: undefined,
											launch_workflow_data_source_id: undefined,
										})
									}
								>
									<Trash2 className="h-4 w-4" />
								</Button>
							)}
						</div>
						<WorkflowSelectorDialog
							open={launchWorkflowDialogOpen}
							onOpenChange={setLaunchWorkflowDialogOpen}
							entityRoles={entityRoles}
							workflowType="workflow"
							mode="single"
							selectedWorkflowIds={
								page.launch_workflow_id ? [page.launch_workflow_id] : []
							}
							onSelect={handleLaunchWorkflowSelect}
						/>
					</FormField>

					{page.launch_workflow_id && (
						<>
							<FormField
								label="Data Source ID"
								description="Access workflow result via {{ workflow.<id> }}. Defaults to workflow function name."
							>
								<Input
									value={page.launch_workflow_data_source_id ?? ""}
									onChange={(e) =>
										onChange({
											launch_workflow_data_source_id:
												e.target.value || undefined,
										})
									}
									placeholder="Auto (workflow function name)"
								/>
							</FormField>

							<FormField
								label="Launch Workflow Parameters"
								description="Parameters to pass to the launch workflow"
							>
								<WorkflowParameterEditor
									workflowId={page.launch_workflow_id}
									value={page.launch_workflow_params ?? {}}
									onChange={(params) =>
										onChange({ launch_workflow_params: params })
									}
									isRowAction={false}
								/>
							</FormField>
						</>
					)}
				</div>

				{/* Page Access Control (only shown when app uses role-based access) */}
				{appAccessLevel === "role_based" && (
					<div className="pt-2 border-t">
						<div className="flex items-center gap-2 mb-2">
							<Shield className="h-4 w-4" />
							<Label className="text-sm font-medium">Page Access</Label>
						</div>
						<p className="text-xs text-muted-foreground mb-3">
							Restrict this page to specific roles. Leave empty to allow all app roles.
						</p>

						{availableRoles.length === 0 ? (
							<p className="text-sm text-muted-foreground">
								No roles available at app level.
							</p>
						) : (
							<div className="space-y-2 max-h-40 overflow-y-auto">
								{availableRoles.map((role) => {
									const isSelected = pageAllowedRoles.includes(role.id);
									return (
										<label
											key={role.id}
											htmlFor={`page-role-${role.id}`}
											className={`flex items-start space-x-3 rounded-md border p-2 hover:bg-accent/50 transition-colors cursor-pointer ${
												isSelected ? "border-primary bg-primary/5" : ""
											}`}
										>
											<Checkbox
												id={`page-role-${role.id}`}
												checked={isSelected}
												onCheckedChange={(checked) =>
													handleRoleToggle(role.id, checked as boolean)
												}
											/>
											<div className="flex-1 min-w-0">
												<span className="cursor-pointer text-sm font-medium">
													{role.name}
												</span>
											</div>
										</label>
									);
								})}
							</div>
						)}

						{pageAllowedRoles.length > 0 && (
							<Alert className="mt-2 py-2">
								<AlertTriangle className="h-3 w-3" />
								<AlertDescription className="text-xs">
									Only selected roles can access this page.
								</AlertDescription>
							</Alert>
						)}

						<FormField
							label="Redirect Path"
							description="Where to redirect users without access"
						>
							<Input
								value={page.permission?.redirect_to ?? ""}
								onChange={(e) =>
									onChange({
										permission: {
											...page.permission,
											redirect_to: e.target.value || undefined,
										},
									})
								}
								placeholder="/access-denied (optional)"
							/>
						</FormField>
					</div>
				)}

				{/* Page CSS Section */}
				<div className="pt-2 border-t">
					<FormField
						label="Custom CSS"
						description="CSS rules scoped to this page"
					>
						<Textarea
							value={page.styles ?? ""}
							onChange={(e) =>
								onChange({
									styles: e.target.value || undefined,
								})
							}
							rows={10}
							placeholder=".custom-sidebar { position: sticky; top: 0; }"
							className="font-mono text-sm"
						/>
					</FormField>
				</div>
			</AccordionContent>
		</AccordionItem>
	);
}

function LayoutPropertiesSection({
	component,
	onChange,
}: {
	component: LayoutContainer;
	onChange: (updates: Partial<LayoutContainer>) => void;
}) {
	const isGrid = component.type === "grid";
	// Cast to helper type for accessing optional properties across the union
	const layoutProps = component as unknown as LayoutContainerProps;

	return (
		<AccordionItem value="layout">
			<AccordionTrigger>Layout</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Type">
					<Select
						value={component.type}
						onValueChange={(value) =>
							onChange({ type: value as LayoutType })
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="row">Row</SelectItem>
							<SelectItem value="column">Column</SelectItem>
							<SelectItem value="grid">Grid</SelectItem>
						</SelectContent>
					</Select>
				</FormField>

				<FormField
					label="Gap"
					description="Space between children (pixels)"
				>
					<Input
						type="number"
						value={component.gap ?? ""}
						onChange={(e) =>
							onChange({
								gap: e.target.value
									? Number(e.target.value)
									: undefined,
							})
						}
						placeholder="0"
						min={0}
					/>
				</FormField>

				<FormField label="Padding" description="Inner padding (pixels)">
					<Input
						type="number"
						value={component.padding ?? ""}
						onChange={(e) =>
							onChange({
								padding: e.target.value
									? Number(e.target.value)
									: undefined,
							})
						}
						placeholder="0"
						min={0}
					/>
				</FormField>

				<FormField label="Align" description="Cross-axis alignment">
					<Select
						value={layoutProps.align ?? "stretch"}
						onValueChange={(value) =>
							onChange({ align: value as LayoutAlign } as Partial<LayoutContainer>)
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="start">Start</SelectItem>
							<SelectItem value="center">Center</SelectItem>
							<SelectItem value="end">End</SelectItem>
							<SelectItem value="stretch">Stretch</SelectItem>
						</SelectContent>
					</Select>
				</FormField>

				<FormField label="Justify" description="Main-axis distribution">
					<Select
						value={layoutProps.justify ?? "start"}
						onValueChange={(value) =>
							onChange({ justify: value as LayoutJustify } as Partial<LayoutContainer>)
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="start">Start</SelectItem>
							<SelectItem value="center">Center</SelectItem>
							<SelectItem value="end">End</SelectItem>
							<SelectItem value="between">
								Space Between
							</SelectItem>
							<SelectItem value="around">Space Around</SelectItem>
						</SelectContent>
					</Select>
				</FormField>

				<FormField
					label="Max Width"
					description="Constrain layout width (use lg for forms)"
				>
					<Select
						value={layoutProps.max_width ?? "none"}
						onValueChange={(value) =>
							onChange({
								max_width:
									value === "none"
										? undefined
										: (value as LayoutMaxWidth),
							} as Partial<LayoutContainer>)
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="none">
								None (full width)
							</SelectItem>
							<SelectItem value="sm">Small (384px)</SelectItem>
							<SelectItem value="md">Medium (448px)</SelectItem>
							<SelectItem value="lg">Large (512px)</SelectItem>
							<SelectItem value="xl">X-Large (576px)</SelectItem>
							<SelectItem value="2xl">
								2X-Large (672px)
							</SelectItem>
						</SelectContent>
					</Select>
				</FormField>

				<FormField
					label="Distribute"
					description="How children fill available space"
				>
					<Select
						value={layoutProps.distribute ?? "natural"}
						onValueChange={(value) =>
							onChange({
								distribute:
									value === "natural"
										? undefined
										: (value as "natural" | "equal" | "fit"),
							} as Partial<LayoutContainer>)
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="natural">Natural (default)</SelectItem>
							<SelectItem value="equal">Equal (flex-1)</SelectItem>
							<SelectItem value="fit">Fit Content</SelectItem>
						</SelectContent>
					</Select>
				</FormField>

				<FormField
					label="Max Height"
					description="Container height limit (pixels, enables scrolling)"
				>
					<Input
						type="number"
						value={layoutProps.max_height ?? ""}
						onChange={(e) =>
							onChange({
								max_height: e.target.value
									? Number(e.target.value)
									: undefined,
							} as Partial<LayoutContainer>)
						}
						placeholder="None"
						min={0}
					/>
				</FormField>

				<FormField
					label="Overflow"
					description="Behavior when content exceeds bounds"
				>
					<Select
						value={layoutProps.overflow ?? "visible"}
						onValueChange={(value) =>
							onChange({
								overflow:
									value === "visible"
										? undefined
										: (value as "auto" | "scroll" | "hidden" | "visible"),
							} as Partial<LayoutContainer>)
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="visible">Visible (default)</SelectItem>
							<SelectItem value="auto">Auto (scroll when needed)</SelectItem>
							<SelectItem value="scroll">Always Scroll</SelectItem>
							<SelectItem value="hidden">Hidden (clip)</SelectItem>
						</SelectContent>
					</Select>
				</FormField>

				<FormField
					label="Sticky Position"
					description="Pin container to edge when scrolling"
				>
					<Select
						value={layoutProps.sticky ?? "none"}
						onValueChange={(value) =>
							onChange({
								sticky:
									value === "none"
										? undefined
										: (value as "top" | "bottom"),
							} as Partial<LayoutContainer>)
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="none">None (default)</SelectItem>
							<SelectItem value="top">Sticky Top</SelectItem>
							<SelectItem value="bottom">Sticky Bottom</SelectItem>
						</SelectContent>
					</Select>
				</FormField>

				{layoutProps.sticky && (
					<FormField
						label="Sticky Offset"
						description="Distance from edge (pixels)"
					>
						<Input
							type="number"
							value={layoutProps.sticky_offset ?? 0}
							onChange={(e) =>
								onChange({
									sticky_offset: Number(e.target.value),
								} as Partial<LayoutContainer>)
							}
							min={0}
						/>
					</FormField>
				)}

				<FormField
					label="CSS Classes"
					description="Custom Tailwind or CSS classes"
				>
					<Input
						value={layoutProps.class_name ?? ""}
						onChange={(e) =>
							onChange({
								class_name: e.target.value || undefined,
							} as Partial<LayoutContainer>)
						}
						placeholder="bg-blue-500 rounded-lg"
					/>
				</FormField>

				<Accordion type="single" collapsible>
					<AccordionItem value="inline-styles">
						<AccordionTrigger>Inline Styles (Advanced)</AccordionTrigger>
						<AccordionContent>
							<JsonEditor
								value={component.style ?? {}}
								onChange={(value) =>
									onChange({ style: value as Record<string, unknown> })
								}
								rows={4}
							/>
							<p className="text-xs text-muted-foreground mt-2">
								Use camelCase: maxHeight, backgroundColor, etc.
							</p>
						</AccordionContent>
					</AccordionItem>
				</Accordion>

				{isGrid && (
					<FormField
						label="Columns"
						description="Number of grid columns"
					>
						<Input
							type="number"
							value={component.columns ?? ""}
							onChange={(e) =>
								onChange({
									columns: e.target.value
										? Number(e.target.value)
										: undefined,
								})
							}
							placeholder="1"
							min={1}
							max={12}
						/>
					</FormField>
				)}
			</AccordionContent>
		</AccordionItem>
	);
}

/** Heading component properties */
function HeadingPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "heading") return null;

	return (
		<AccordionItem value="heading">
			<AccordionTrigger>Heading</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Text"
					description="Supports expressions like {{ data.title }}"
				>
					<Input
						value={component.text}
						onChange={(e) =>
							onChange({ text: e.target.value })
						}
					/>
				</FormField>

				<FormField
					label="Level"
					description="Heading size (1 = largest)"
				>
					<Select
						value={String(component.level ?? 1)}
						onValueChange={(value) =>
							onChange({
								level: Number(value) as HeadingLevel,
							})
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="1">H1 - Extra Large</SelectItem>
							<SelectItem value="2">H2 - Large</SelectItem>
							<SelectItem value="3">H3 - Medium</SelectItem>
							<SelectItem value="4">H4 - Small</SelectItem>
							<SelectItem value="5">H5 - Extra Small</SelectItem>
							<SelectItem value="6">H6 - Smallest</SelectItem>
						</SelectContent>
					</Select>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** Text component properties */
function TextPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "text") return null;

	return (
		<AccordionItem value="text">
			<AccordionTrigger>Text</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Text"
					description="Supports expressions like {{ user.name }}"
				>
					<Textarea
						value={component.text}
						onChange={(e) =>
							onChange({ text: e.target.value })
						}
						rows={3}
					/>
				</FormField>

				<FormField
					label="Label"
					description="Optional label above the text"
				>
					<Input
						value={component.label ?? ""}
						onChange={(e) =>
							onChange({
								label: e.target.value || undefined,
							})
						}
						placeholder="None"
					/>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** HTML/JSX component properties */
function HtmlPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "html") return null;

	return (
		<AccordionItem value="html">
			<AccordionTrigger>HTML/JSX Content</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Content"
					description="Plain HTML or JSX with context access (use className= for JSX, class= for HTML)"
				>
					<Textarea
						value={component.content}
						onChange={(e) =>
							onChange({ content: e.target.value })
						}
						rows={12}
						className="font-mono text-xs"
						placeholder={
							'<div className="p-4 bg-muted rounded">\n  <p>Hello {context.workflow.user.name}!</p>\n</div>'
						}
					/>
				</FormField>
				<p className="text-xs text-muted-foreground">
					JSX templates have access to{" "}
					<code className="bg-muted px-1 rounded">
						context.workflow.*
					</code>{" "}
					for variables and data.
				</p>
			</AccordionContent>
		</AccordionItem>
	);
}

/** Button component properties */
function ButtonPropertiesSection({
	component,
	onChange,
	appRoleIds,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
	appRoleIds?: string[];
}) {
	// Dialog state for workflow selectors
	const [workflowDialogOpen, setWorkflowDialogOpen] = useState(false);
	const [submitWorkflowDialogOpen, setSubmitWorkflowDialogOpen] =
		useState(false);

	// Fetch roles for entity role context
	const { data: rolesData } = useRoles();

	// Fetch workflows for display name lookup
	const { data: workflowsData } = $api.useQuery("get", "/api/workflows", {});

	// Convert appRoleIds to EntityRole[] format for the dialog
	const entityRoles: EntityRole[] = useMemo(
		() =>
			rolesData
				?.filter((r) => appRoleIds?.includes(r.id))
				.map((r) => ({ id: r.id, name: r.name })) ?? [],
		[rolesData, appRoleIds],
	);

	if (component.type !== "button") return null;

	// Get workflow name for button display
	const workflowName = workflowsData?.find(
		(w) => w.id === component.workflow_id,
	)?.name;

	// Handle workflow selection from dialog
	const handleWorkflowSelect = (
		workflowIds: string[],
		_assignRoles: boolean,
	) => {
		const workflowId = workflowIds[0] || undefined;
		onChange({
			workflow_id: workflowId,
			action_params: {},
		});
	};

	return (
		<AccordionItem value="button">
			<AccordionTrigger>Button</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Label">
					<Input
						value={component.label}
						onChange={(e) => onChange({ label: e.target.value })}
						placeholder="Button text..."
					/>
				</FormField>

				<FormField label="Variant">
					<Select
						value={component.variant ?? "default"}
						onValueChange={(value) =>
							onChange({
								variant: value as typeof component.variant,
							})
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="default">Default</SelectItem>
							<SelectItem value="destructive">
								Destructive
							</SelectItem>
							<SelectItem value="outline">Outline</SelectItem>
							<SelectItem value="secondary">Secondary</SelectItem>
							<SelectItem value="ghost">Ghost</SelectItem>
							<SelectItem value="link">Link</SelectItem>
						</SelectContent>
					</Select>
				</FormField>

				<FormField label="Size">
					<Select
						value={component.size ?? "default"}
						onValueChange={(value) =>
							onChange({
								size: value as typeof component.size,
							})
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="sm">Small</SelectItem>
							<SelectItem value="default">Default</SelectItem>
							<SelectItem value="lg">Large</SelectItem>
						</SelectContent>
					</Select>
				</FormField>

				<FormField
					label="Disabled"
					description="Boolean or expression (e.g., {{ status == 'completed' }})"
				>
					<Input
						value={
							typeof component.disabled === "boolean"
								? component.disabled
									? "true"
									: ""
								: (component.disabled ?? "")
						}
						onChange={(e) => {
							const value = e.target.value;
							// Empty = not disabled
							if (!value || value === "false") {
								onChange({ disabled: false });
							} else if (value === "true") {
								onChange({ disabled: true });
							} else {
								// Expression string
								onChange({ disabled: value });
							}
						}}
						placeholder="false, true, or {{ expression }}"
					/>
				</FormField>

				<FormField label="Action Type">
					<Select
						value={component.action_type ?? ""}
						onValueChange={(value) =>
							onChange({
								action_type: value as ButtonActionType,
							})
						}
					>
						<SelectTrigger>
							<SelectValue placeholder="Select action..." />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="navigate">Navigate</SelectItem>
							<SelectItem value="workflow">Workflow</SelectItem>
							<SelectItem value="submit">Submit Form</SelectItem>
							<SelectItem value="custom">Custom</SelectItem>
						</SelectContent>
					</Select>
				</FormField>

				{component.action_type === "navigate" && (
					<FormField
						label="Navigate To"
						description="Path to navigate (supports expressions)"
					>
						<Input
							value={component.navigate_to ?? ""}
							onChange={(e) =>
								onChange({
									navigate_to: e.target.value,
								})
							}
							placeholder="/path/to/page"
						/>
					</FormField>
				)}

				{component.action_type === "workflow" && (
					<>
						<FormField label="Workflow">
							<div className="flex gap-2">
								<Button
									variant="outline"
									className="flex-1 justify-start font-normal"
									onClick={() => setWorkflowDialogOpen(true)}
								>
									{workflowName || "Select a workflow"}
								</Button>
								{component.workflow_id && (
									<Button
										variant="ghost"
										size="icon"
										onClick={() =>
											onChange({
												workflow_id: undefined,
												action_params: {},
											})
										}
									>
										<Trash2 className="h-4 w-4" />
									</Button>
								)}
							</div>
							<WorkflowSelectorDialog
								open={workflowDialogOpen}
								onOpenChange={setWorkflowDialogOpen}
								entityRoles={entityRoles}
								workflowType="workflow"
								mode="single"
								selectedWorkflowIds={
									component.workflow_id ? [component.workflow_id] : []
								}
								onSelect={handleWorkflowSelect}
							/>
						</FormField>

						{component.workflow_id && (
							<FormField
								label="Parameters"
								description="Values to pass to the workflow"
							>
								<WorkflowParameterEditor
									workflowId={component.workflow_id}
									value={component.action_params ?? {}}
									onChange={(actionParams) =>
										onChange({ action_params: actionParams })
									}
									isRowAction={false}
								/>
							</FormField>
						)}
					</>
				)}

				{component.action_type === "submit" && (
					<>
						<FormField
							label="Workflow"
							description="All form field values will be passed automatically"
						>
							<div className="flex gap-2">
								<Button
									variant="outline"
									className="flex-1 justify-start font-normal"
									onClick={() => setSubmitWorkflowDialogOpen(true)}
								>
									{workflowName || "Select a workflow"}
								</Button>
								{component.workflow_id && (
									<Button
										variant="ghost"
										size="icon"
										onClick={() =>
											onChange({
												workflow_id: undefined,
												action_params: {},
											})
										}
									>
										<Trash2 className="h-4 w-4" />
									</Button>
								)}
							</div>
							<WorkflowSelectorDialog
								open={submitWorkflowDialogOpen}
								onOpenChange={setSubmitWorkflowDialogOpen}
								entityRoles={entityRoles}
								workflowType="workflow"
								mode="single"
								selectedWorkflowIds={
									component.workflow_id ? [component.workflow_id] : []
								}
								onSelect={handleWorkflowSelect}
							/>
						</FormField>

						{component.workflow_id && (
							<FormField
								label="Additional Parameters"
								description="Extra values to include (form fields auto-included)"
							>
								<WorkflowParameterEditor
									workflowId={component.workflow_id}
									value={component.action_params ?? {}}
									onChange={(actionParams) =>
										onChange({ action_params: actionParams })
									}
									isRowAction={false}
								/>
							</FormField>
						)}
					</>
				)}

				{component.action_type === "custom" && (
					<>
						<FormField label="Custom Action ID">
							<Input
								value={component.custom_action_id ?? ""}
								onChange={(e) =>
									onChange({
										custom_action_id: e.target.value,
									})
								}
								placeholder="action-id"
							/>
						</FormField>

						<FormField label="Parameters">
							<KeyValueEditor
								value={component.action_params ?? {}}
								onChange={(actionParams) =>
									onChange({ action_params: actionParams })
								}
							/>
						</FormField>
					</>
				)}
			</AccordionContent>
		</AccordionItem>
	);
}

/** Image component properties */
function ImagePropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "image") return null;

	return (
		<AccordionItem value="image">
			<AccordionTrigger>Image</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Source URL"
					description="Image URL or expression"
				>
					<Input
						value={component.src}
						onChange={(e) =>
							onChange({ src: e.target.value })
						}
						placeholder="https://example.com/image.png"
					/>
				</FormField>

				<FormField
					label="Alt Text"
					description="Accessibility description"
				>
					<Input
						value={component.alt ?? ""}
						onChange={(e) =>
							onChange({
								alt: e.target.value || undefined,
							})
						}
						placeholder="Image description"
					/>
				</FormField>

				<FormField
					label="Max Width"
					description="Maximum width (e.g., 200 or 100%)"
				>
					<Input
						value={component.max_width ?? ""}
						onChange={(e) => {
							const val = e.target.value;
							const numVal = Number(val);
							onChange({
								max_width: val
									? isNaN(numVal)
										? val
										: numVal
									: undefined,
							});
						}}
						placeholder="auto"
					/>
				</FormField>

				<FormField
					label="Max Height"
					description="Maximum height (e.g., 200 or 100%)"
				>
					<Input
						value={component.max_height ?? ""}
						onChange={(e) => {
							const val = e.target.value;
							const numVal = Number(val);
							onChange({
								max_height: val
									? isNaN(numVal)
										? val
										: numVal
									: undefined,
							});
						}}
						placeholder="auto"
					/>
				</FormField>

				<FormField
					label="Object Fit"
					description="How the image scales within its container"
				>
					<Select
						value={component.object_fit ?? "contain"}
						onValueChange={(value) =>
							onChange({
								object_fit: value as typeof component.object_fit,
							})
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="contain">Contain</SelectItem>
							<SelectItem value="cover">Cover</SelectItem>
							<SelectItem value="fill">Fill</SelectItem>
							<SelectItem value="none">None</SelectItem>
						</SelectContent>
					</Select>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** Card component properties */
function CardPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "card") return null;

	return (
		<AccordionItem value="card">
			<AccordionTrigger>Card</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Title" description="Optional card title">
					<Input
						value={component.title ?? ""}
						onChange={(e) =>
							onChange({
								title: e.target.value || undefined,
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField
					label="Description"
					description="Optional card description"
				>
					<Textarea
						value={component.description ?? ""}
						onChange={(e) =>
							onChange({
								description: e.target.value || undefined,
							})
						}
						rows={2}
						placeholder="None"
					/>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** StatCard component properties */
function StatCardPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "stat-card") return null;

	return (
		<AccordionItem value="stat-card">
			<AccordionTrigger>Stat Card</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Title">
					<Input
						value={component.title}
						onChange={(e) =>
							onChange({ title: e.target.value })
						}
					/>
				</FormField>

				<FormField
					label="Value"
					description="Supports expressions like {{ data.count }}"
				>
					<Input
						value={component.value}
						onChange={(e) =>
							onChange({ value: e.target.value })
						}
					/>
				</FormField>

				<FormField label="Description" description="Additional context">
					<Input
						value={component.description ?? ""}
						onChange={(e) =>
							onChange({
								description: e.target.value || undefined,
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField
					label="Icon"
					description="Icon name (e.g., users, chart)"
				>
					<Input
						value={component.icon ?? ""}
						onChange={(e) =>
							onChange({
								icon: e.target.value || undefined,
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField label="Trend" description="Optional trend indicator">
					<JsonEditor
						value={
							component.trend ?? { value: "", direction: "neutral" }
						}
						onChange={(value) =>
							onChange({
								trend: value as typeof component.trend,
							})
						}
						rows={4}
					/>
				</FormField>

				<FormField
					label="Click Action"
					description="Optional click behavior"
				>
					<JsonEditor
						value={
							component.on_click ?? {
								type: "navigate",
								navigate_to: "",
							}
						}
						onChange={(value) =>
							onChange({
								on_click: value as typeof component.on_click,
							})
						}
						rows={4}
					/>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** DataTable component properties */
function DataTablePropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "data-table") return null;

	return (
		<AccordionItem value="data-table">
			<AccordionTrigger>Data Table</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Data Source"
					description="ID of a page data source (e.g., clientsList)"
				>
					<Input
						value={component.data_source}
						onChange={(e) =>
							onChange({ data_source: e.target.value })
						}
						placeholder="dataSourceId"
					/>
				</FormField>

				<FormField
					label="Data Path"
					description="Path to array in result (e.g., 'clients' if workflow returns { clients: [...] })"
				>
					<Input
						value={component.data_path ?? ""}
						onChange={(e) =>
							onChange({ data_path: e.target.value || undefined })
						}
						placeholder="Leave empty if result is already an array"
					/>
				</FormField>

				<FormField label="Searchable">
					<div className="flex items-center gap-2">
						<Switch
							checked={component.searchable ?? false}
							onCheckedChange={(checked) =>
								onChange({ searchable: checked })
							}
						/>
						<span className="text-sm text-muted-foreground">
							{component.searchable ? "Yes" : "No"}
						</span>
					</div>
				</FormField>

				<FormField label="Selectable">
					<div className="flex items-center gap-2">
						<Switch
							checked={component.selectable ?? false}
							onCheckedChange={(checked) =>
								onChange({ selectable: checked })
							}
						/>
						<span className="text-sm text-muted-foreground">
							{component.selectable ? "Yes" : "No"}
						</span>
					</div>
				</FormField>

				<FormField label="Paginated">
					<div className="flex items-center gap-2">
						<Switch
							checked={component.paginated ?? false}
							onCheckedChange={(checked) =>
								onChange({ paginated: checked })
							}
						/>
						<span className="text-sm text-muted-foreground">
							{component.paginated ? "Yes" : "No"}
						</span>
					</div>
				</FormField>

				{component.paginated && (
					<FormField label="Page Size" description="Rows per page">
						<Input
							type="number"
							value={component.page_size ?? 10}
							onChange={(e) =>
								onChange({
									page_size: Number(e.target.value) || 10,
								})
							}
							min={1}
							max={100}
						/>
					</FormField>
				)}

				<FormField
					label="Empty Message"
					description="Message when no data"
				>
					<Input
						value={component.empty_message ?? ""}
						onChange={(e) =>
							onChange({
								empty_message: e.target.value || undefined,
							})
						}
						placeholder="No data available"
					/>
				</FormField>

				<FormField label="Columns" description="Define table columns">
					<ColumnBuilder
						value={component.columns}
						onChange={(columns) =>
							onChange({ columns })
						}
					/>
				</FormField>

				<FormField
					label="Row Actions"
					description="Actions available for each row"
				>
					<TableActionBuilder
						value={component.row_actions ?? []}
						onChange={(rowActions) =>
							onChange({ row_actions: rowActions })
						}
						isRowAction={true}
					/>
				</FormField>

				<FormField
					label="Header Actions"
					description="Actions in table header"
				>
					<TableActionBuilder
						value={component.header_actions ?? []}
						onChange={(headerActions) =>
							onChange({ header_actions: headerActions })
						}
						isRowAction={false}
					/>
				</FormField>

				<FormField
					label="Row Click Behavior"
					description="What happens when a row is clicked"
				>
					<Select
						value={component.on_row_click?.type ?? "none"}
						onValueChange={(type) => {
							if (type === "none") {
								onChange({ on_row_click: undefined });
							} else {
								onChange({
									on_row_click: {
										type: type as
											| "navigate"
											| "select"
											| "set-variable",
										navigate_to:
											type === "navigate"
												? "/details/{{ row.id }}"
												: undefined,
										variable_name:
											type === "set-variable"
												? "selectedRow"
												: undefined,
									},
								});
							}
						}}
					>
						<SelectTrigger>
							<SelectValue placeholder="No action" />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="none">No action</SelectItem>
							<SelectItem value="navigate">
								Navigate to page
							</SelectItem>
							<SelectItem value="select">Select row</SelectItem>
							<SelectItem value="set-variable">
								Set variable
							</SelectItem>
						</SelectContent>
					</Select>
				</FormField>

				{component.on_row_click?.type === "navigate" && (
					<FormField
						label="Navigate To"
						description="Path with {{ row.* }} expressions"
					>
						<Input
							value={component.on_row_click.navigate_to ?? ""}
							onChange={(e) =>
								onChange({
									on_row_click: {
										...component.on_row_click!,
										navigate_to: e.target.value,
									},
								})
							}
							placeholder="/details/{{ row.id }}"
						/>
					</FormField>
				)}

				{component.on_row_click?.type === "set-variable" && (
					<FormField
						label="Variable Name"
						description="Store the row in this variable"
					>
						<Input
							value={component.on_row_click.variable_name ?? ""}
							onChange={(e) =>
								onChange({
									on_row_click: {
										...component.on_row_click!,
										variable_name: e.target.value,
									},
								})
							}
							placeholder="selectedRow"
						/>
					</FormField>
				)}
			</AccordionContent>
		</AccordionItem>
	);
}

/** Tabs component properties */
function TabsPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "tabs") return null;

	return (
		<AccordionItem value="tabs">
			<AccordionTrigger>Tabs</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Orientation">
					<Select
						value={component.orientation ?? "horizontal"}
						onValueChange={(value) =>
							onChange({
								orientation: value as
									| "horizontal"
									| "vertical",
							})
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="horizontal">
								Horizontal
							</SelectItem>
							<SelectItem value="vertical">Vertical</SelectItem>
						</SelectContent>
					</Select>
				</FormField>

				<FormField
					label="Default Tab"
					description="ID of initially active tab"
				>
					<Input
						value={component.default_tab ?? ""}
						onChange={(e) =>
							onChange({
								default_tab: e.target.value || undefined,
							})
						}
						placeholder="First tab"
					/>
				</FormField>

				<FormField
					label="Tab Items"
					description="Tab definitions (JSON array)"
				>
					<JsonEditor
						value={component.children}
						onChange={(value) =>
							onChange({
								children: value as typeof component.children,
							})
						}
						rows={10}
					/>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** Badge component properties */
function BadgePropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "badge") return null;

	return (
		<AccordionItem value="badge">
			<AccordionTrigger>Badge</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Text">
					<Input
						value={component.text}
						onChange={(e) =>
							onChange({ text: e.target.value })
						}
					/>
				</FormField>

				<FormField label="Variant">
					<Select
						value={component.variant ?? "default"}
						onValueChange={(value) =>
							onChange({
								variant: value as typeof component.variant,
							})
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="default">Default</SelectItem>
							<SelectItem value="secondary">Secondary</SelectItem>
							<SelectItem value="destructive">
								Destructive
							</SelectItem>
							<SelectItem value="outline">Outline</SelectItem>
						</SelectContent>
					</Select>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** Progress component properties */
function ProgressPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "progress") return null;

	return (
		<AccordionItem value="progress">
			<AccordionTrigger>Progress</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Value" description="0-100 or expression">
					<Input
						value={String(component.value)}
						onChange={(e) => {
							const val = e.target.value;
							const numVal = Number(val);
							onChange({
								value: isNaN(numVal) ? val : numVal,
							});
						}}
					/>
				</FormField>

				<FormField label="Show Label">
					<div className="flex items-center gap-2">
						<Switch
							checked={component.show_label ?? false}
							onCheckedChange={(checked) =>
								onChange({ show_label: checked })
							}
						/>
						<span className="text-sm text-muted-foreground">
							{component.show_label ? "Yes" : "No"}
						</span>
					</div>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** Divider component properties */
function DividerPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "divider") return null;

	return (
		<AccordionItem value="divider">
			<AccordionTrigger>Divider</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Orientation">
					<Select
						value={component.orientation ?? "horizontal"}
						onValueChange={(value) =>
							onChange({
								orientation: value as
									| "horizontal"
									| "vertical",
							})
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="horizontal">
								Horizontal
							</SelectItem>
							<SelectItem value="vertical">Vertical</SelectItem>
						</SelectContent>
					</Select>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** Spacer component properties */
function SpacerPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "spacer") return null;

	return (
		<AccordionItem value="spacer">
			<AccordionTrigger>Spacer</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Size"
					description="Size in pixels or Tailwind units"
				>
					<Input
						value={String(component.size ?? "")}
						onChange={(e) => {
							const val = e.target.value;
							const numVal = Number(val);
							onChange({
								size: val
									? isNaN(numVal)
										? val
										: numVal
									: undefined,
							});
						}}
						placeholder="16"
					/>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** Text Input component properties */
function TextInputPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "text-input") return null;

	return (
		<AccordionItem value="text-input">
			<AccordionTrigger>Text Input</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Field ID"
					description="ID for accessing value via {{ field.fieldId }}"
				>
					<Input
						value={component.field_id}
						onChange={(e) =>
							onChange({ field_id: e.target.value })
						}
						placeholder="fieldName"
					/>
				</FormField>

				<FormField label="Label">
					<Input
						value={component.label ?? ""}
						onChange={(e) =>
							onChange({
								label: e.target.value || undefined,
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField label="Placeholder">
					<Input
						value={component.placeholder ?? ""}
						onChange={(e) =>
							onChange({
								placeholder: e.target.value || undefined,
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField
					label="Default Value"
					description="Supports expressions"
				>
					<Input
						value={component.default_value ?? ""}
						onChange={(e) =>
							onChange({
								default_value: e.target.value || undefined,
							})
						}
					/>
				</FormField>

				<FormField label="Input Type">
					<Select
						value={component.input_type ?? "text"}
						onValueChange={(value) =>
							onChange({
								input_type: value as typeof component.input_type,
							})
						}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="text">Text</SelectItem>
							<SelectItem value="email">Email</SelectItem>
							<SelectItem value="password">Password</SelectItem>
							<SelectItem value="url">URL</SelectItem>
							<SelectItem value="tel">Phone</SelectItem>
						</SelectContent>
					</Select>
				</FormField>

				<FormField label="Required">
					<div className="flex items-center gap-2">
						<Switch
							checked={component.required ?? false}
							onCheckedChange={(checked) =>
								onChange({ required: checked })
							}
						/>
						<span className="text-sm text-muted-foreground">
							{component.required ? "Yes" : "No"}
						</span>
					</div>
				</FormField>

				<FormField
					label="Disabled"
					description="Boolean or expression (e.g., {{ status == 'locked' }})"
				>
					<Input
						value={
							typeof component.disabled === "boolean"
								? component.disabled
									? "true"
									: ""
								: (component.disabled ?? "")
						}
						onChange={(e) => {
							const value = e.target.value;
							if (!value || value === "false") {
								onChange({ disabled: false });
							} else if (value === "true") {
								onChange({ disabled: true });
							} else {
								onChange({ disabled: value });
							}
						}}
						placeholder="false, true, or {{ expression }}"
					/>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** Number Input component properties */
function NumberInputPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "number-input") return null;

	return (
		<AccordionItem value="number-input">
			<AccordionTrigger>Number Input</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Field ID"
					description="ID for accessing value via {{ field.fieldId }}"
				>
					<Input
						value={component.field_id}
						onChange={(e) =>
							onChange({ field_id: e.target.value })
						}
						placeholder="fieldName"
					/>
				</FormField>

				<FormField label="Label">
					<Input
						value={component.label ?? ""}
						onChange={(e) =>
							onChange({
								label: e.target.value || undefined,
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField label="Placeholder">
					<Input
						value={component.placeholder ?? ""}
						onChange={(e) =>
							onChange({
								placeholder: e.target.value || undefined,
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField
					label="Default Value"
					description="Number or expression"
				>
					<Input
						value={String(component.default_value ?? "")}
						onChange={(e) => {
							const val = e.target.value;
							const numVal = Number(val);
							onChange({
								default_value: val
									? isNaN(numVal)
										? val
										: numVal
									: undefined,
							});
						}}
					/>
				</FormField>

				<FormField label="Min">
					<Input
						type="number"
						value={component.min ?? ""}
						onChange={(e) =>
							onChange({
								min: e.target.value
									? Number(e.target.value)
									: undefined,
							})
						}
					/>
				</FormField>

				<FormField label="Max">
					<Input
						type="number"
						value={component.max ?? ""}
						onChange={(e) =>
							onChange({
								max: e.target.value
									? Number(e.target.value)
									: undefined,
							})
						}
					/>
				</FormField>

				<FormField label="Step">
					<Input
						type="number"
						value={component.step ?? ""}
						onChange={(e) =>
							onChange({
								step: e.target.value
									? Number(e.target.value)
									: undefined,
							})
						}
						placeholder="1"
					/>
				</FormField>

				<FormField label="Required">
					<div className="flex items-center gap-2">
						<Switch
							checked={component.required ?? false}
							onCheckedChange={(checked) =>
								onChange({ required: checked })
							}
						/>
						<span className="text-sm text-muted-foreground">
							{component.required ? "Yes" : "No"}
						</span>
					</div>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** Select component properties */
function SelectPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "select") return null;

	return (
		<AccordionItem value="select">
			<AccordionTrigger>Select</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Field ID"
					description="ID for accessing value via {{ field.fieldId }}"
				>
					<Input
						value={component.field_id}
						onChange={(e) =>
							onChange({ field_id: e.target.value })
						}
						placeholder="fieldName"
					/>
				</FormField>

				<FormField label="Label">
					<Input
						value={component.label ?? ""}
						onChange={(e) =>
							onChange({
								label: e.target.value || undefined,
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField label="Placeholder">
					<Input
						value={component.placeholder ?? ""}
						onChange={(e) =>
							onChange({
								placeholder: e.target.value || undefined,
							})
						}
						placeholder="Select an option"
					/>
				</FormField>

				<FormField label="Default Value">
					<Input
						value={component.default_value ?? ""}
						onChange={(e) =>
							onChange({
								default_value: e.target.value || undefined,
							})
						}
					/>
				</FormField>

				<FormField
					label="Options"
					description="Static options for the dropdown"
				>
					<OptionBuilder
						value={
							Array.isArray(component.options) ? component.options : []
						}
						onChange={(options) =>
							onChange({ options })
						}
					/>
				</FormField>

				<FormField
					label="Options Data Source"
					description="Data source name for dynamic options"
				>
					<Input
						value={component.options_source ?? ""}
						onChange={(e) =>
							onChange({
								options_source: e.target.value || undefined,
							})
						}
						placeholder="None (use static options)"
					/>
				</FormField>

				{component.options_source && (
					<>
						<FormField
							label="Value Field"
							description="Field in data source for option value"
						>
							<Input
								value={component.value_field ?? ""}
								onChange={(e) =>
									onChange({
										value_field:
											e.target.value || undefined,
									})
								}
								placeholder="value"
							/>
						</FormField>

						<FormField
							label="Label Field"
							description="Field in data source for option label"
						>
							<Input
								value={component.label_field ?? ""}
								onChange={(e) =>
									onChange({
										label_field:
											e.target.value || undefined,
									})
								}
								placeholder="label"
							/>
						</FormField>
					</>
				)}

				<FormField label="Required">
					<div className="flex items-center gap-2">
						<Switch
							checked={component.required ?? false}
							onCheckedChange={(checked) =>
								onChange({ required: checked })
							}
						/>
						<span className="text-sm text-muted-foreground">
							{component.required ? "Yes" : "No"}
						</span>
					</div>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** Checkbox component properties */
function CheckboxPropertiesSection({
	component,
	onChange,
}: {
	component: AppComponent;
	onChange: (updates: Partial<AppComponent>) => void;
}) {
	if (component.type !== "checkbox") return null;

	return (
		<AccordionItem value="checkbox">
			<AccordionTrigger>Checkbox</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Field ID"
					description="ID for accessing value via {{ field.fieldId }}"
				>
					<Input
						value={component.field_id}
						onChange={(e) =>
							onChange({ field_id: e.target.value })
						}
						placeholder="fieldName"
					/>
				</FormField>

				<FormField label="Label">
					<Input
						value={component.label}
						onChange={(e) =>
							onChange({ label: e.target.value })
						}
					/>
				</FormField>

				<FormField
					label="Description"
					description="Help text below the checkbox"
				>
					<Input
						value={component.description ?? ""}
						onChange={(e) =>
							onChange({
								description: e.target.value || undefined,
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField label="Default Checked">
					<div className="flex items-center gap-2">
						<Switch
							checked={component.default_checked ?? false}
							onCheckedChange={(checked) =>
								onChange({
									default_checked: checked,
								})
							}
						/>
						<span className="text-sm text-muted-foreground">
							{component.default_checked ? "Yes" : "No"}
						</span>
					</div>
				</FormField>

				<FormField label="Required">
					<div className="flex items-center gap-2">
						<Switch
							checked={component.required ?? false}
							onCheckedChange={(checked) =>
								onChange({ required: checked })
							}
						/>
						<span className="text-sm text-muted-foreground">
							{component.required ? "Yes" : "No"}
						</span>
					</div>
				</FormField>
			</AccordionContent>
		</AccordionItem>
	);
}

/** Get component type sections based on component type */
function getComponentTypeSections(
	component: AppComponent,
	onChange: (updates: Partial<AppComponent>) => void,
	appRoleIds?: string[],
): React.ReactNode {
	const componentType = component.type as ComponentType;

	switch (componentType) {
		case "heading":
			return (
				<HeadingPropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "text":
			return (
				<TextPropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "html":
			return (
				<HtmlPropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "button":
			return (
				<ButtonPropertiesSection
					component={component}
					onChange={onChange}
					appRoleIds={appRoleIds}
				/>
			);
		case "image":
			return (
				<ImagePropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "card":
			return (
				<CardPropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "stat-card":
			return (
				<StatCardPropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "data-table":
			return (
				<DataTablePropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "tabs":
			return (
				<TabsPropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "badge":
			return (
				<BadgePropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "progress":
			return (
				<ProgressPropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "divider":
			return (
				<DividerPropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "spacer":
			return (
				<SpacerPropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "text-input":
			return (
				<TextInputPropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "number-input":
			return (
				<NumberInputPropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "select":
			return (
				<SelectPropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		case "checkbox":
			return (
				<CheckboxPropertiesSection
					component={component}
					onChange={onChange}
				/>
			);
		default:
			return null;
	}
}

/** Get display name for component type */
function getComponentDisplayName(
	component: AppComponent | LayoutContainer,
): string {
	if (isLayoutContainer(component)) {
		switch (component.type) {
			case "row":
				return "Row Layout";
			case "column":
				return "Column Layout";
			case "grid":
				return "Grid Layout";
			default:
				return "Layout";
		}
	}

	const typeNames: Record<ComponentType, string> = {
		heading: "Heading",
		text: "Text",
		html: "HTML/JSX",
		card: "Card",
		divider: "Divider",
		spacer: "Spacer",
		button: "Button",
		"stat-card": "Stat Card",
		image: "Image",
		badge: "Badge",
		progress: "Progress",
		"data-table": "Data Table",
		tabs: "Tabs",
		"tab-item": "Tab Item",
		"file-viewer": "File Viewer",
		modal: "Modal",
		"text-input": "Text Input",
		"number-input": "Number Input",
		select: "Select",
		checkbox: "Checkbox",
		"form-embed": "Form Embed",
		"form-group": "Form Group",
	};

	return typeNames[component.type as ComponentType] ?? "Component";
}

/**
 * Property Editor Panel
 *
 * Displays and allows editing of properties for the selected component
 * in the app builder visual editor.
 */
export function PropertyEditor({
	component,
	onChange,
	onDelete,
	page,
	onPageChange,
	appAccessLevel,
	appRoleIds,
	className,
}: PropertyEditorProps) {
	if (!component) {
		return (
			<div
				className={cn(
					"flex items-center justify-center h-full text-muted-foreground text-sm p-4",
					className,
				)}
			>
				Select a component to edit its properties
			</div>
		);
	}

	const isLayout = isLayoutContainer(component);
	const displayName = getComponentDisplayName(component);

	// Check if this is the root layout (show page settings)
	const isRootLayout = isLayout && page && onPageChange;

	// Determine which accordion sections to open by default
	const defaultOpenSections = isRootLayout
		? ["page", "common", "layout"]
		: isLayout
			? ["common", "layout"]
			: ["common", component.type];

	return (
		<div className={cn("flex flex-col h-full", className)}>
			{/* Header */}
			<div className="px-4 py-3 border-b bg-muted/30">
				<h3 className="font-semibold text-sm">
					{isRootLayout ? page.title : displayName}
				</h3>
				<p className="text-xs text-muted-foreground mt-0.5">
					{isRootLayout
						? "Page settings and root layout"
						: isLayout
							? "Layout container"
							: `Component type: ${component.type}`}
				</p>
			</div>

			{/* Properties */}
			<div className="flex-1 overflow-y-auto px-4 py-2">
				<Accordion
					type="multiple"
					defaultValue={defaultOpenSections}
					className="w-full"
				>
					{/* Page settings for root layout */}
					{isRootLayout && (
						<PagePropertiesSection
							page={page}
							onChange={onPageChange}
							appAccessLevel={appAccessLevel}
							appRoleIds={appRoleIds}
						/>
					)}

					<CommonPropertiesSection
						component={component}
						onChange={onChange}
					/>

					{isLayout ? (
						<LayoutPropertiesSection
							component={component}
							onChange={
								onChange as (
									updates: Partial<LayoutContainer>,
								) => void
							}
						/>
					) : (
						getComponentTypeSections(
							component,
							onChange as (
								updates: Partial<AppComponent>,
							) => void,
							appRoleIds,
						)
					)}
				</Accordion>
			</div>

			{/* Delete button */}
			{onDelete && (
				<div className="px-4 py-3 border-t mt-auto">
					<Button
						variant="destructive"
						size="sm"
						className="w-full"
						onClick={onDelete}
					>
						<Trash2 className="h-4 w-4 mr-2" />
						Delete Component
					</Button>
				</div>
			)}
		</div>
	);
}

export default PropertyEditor;
