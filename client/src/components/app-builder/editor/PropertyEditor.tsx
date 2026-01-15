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
						value={component.align ?? "stretch"}
						onValueChange={(value) =>
							onChange({ align: value as LayoutAlign })
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
						value={component.justify ?? "start"}
						onValueChange={(value) =>
							onChange({ justify: value as LayoutJustify })
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
						value={component.maxWidth ?? "none"}
						onValueChange={(value) =>
							onChange({
								maxWidth:
									value === "none"
										? undefined
										: (value as LayoutMaxWidth),
							})
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
						value={component.distribute ?? "natural"}
						onValueChange={(value) =>
							onChange({
								distribute:
									value === "natural"
										? undefined
										: (value as "natural" | "equal" | "fit"),
							})
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
						value={component.maxHeight ?? ""}
						onChange={(e) =>
							onChange({
								maxHeight: e.target.value
									? Number(e.target.value)
									: undefined,
							})
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
						value={component.overflow ?? "visible"}
						onValueChange={(value) =>
							onChange({
								overflow:
									value === "visible"
										? undefined
										: (value as "auto" | "scroll" | "hidden" | "visible"),
							})
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
						value={component.sticky ?? "none"}
						onValueChange={(value) =>
							onChange({
								sticky:
									value === "none"
										? undefined
										: (value as "top" | "bottom"),
							})
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

				{component.sticky && (
					<FormField
						label="Sticky Offset"
						description="Distance from edge (pixels)"
					>
						<Input
							type="number"
							value={component.stickyOffset ?? 0}
							onChange={(e) =>
								onChange({
									stickyOffset: Number(e.target.value),
								})
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
						value={component.className ?? ""}
						onChange={(e) =>
							onChange({
								className: e.target.value || undefined,
							})
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

	const props = component.props;

	return (
		<AccordionItem value="heading">
			<AccordionTrigger>Heading</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Text"
					description="Supports expressions like {{ data.title }}"
				>
					<Input
						value={props.text}
						onChange={(e) =>
							onChange({
								props: { ...props, text: e.target.value },
							})
						}
					/>
				</FormField>

				<FormField
					label="Level"
					description="Heading size (1 = largest)"
				>
					<Select
						value={String(props.level ?? 1)}
						onValueChange={(value) =>
							onChange({
								props: {
									...props,
									level: Number(value) as HeadingLevel,
								},
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

	const props = component.props;

	return (
		<AccordionItem value="text">
			<AccordionTrigger>Text</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Text"
					description="Supports expressions like {{ user.name }}"
				>
					<Textarea
						value={props.text}
						onChange={(e) =>
							onChange({
								props: { ...props, text: e.target.value },
							})
						}
						rows={3}
					/>
				</FormField>

				<FormField
					label="Label"
					description="Optional label above the text"
				>
					<Input
						value={props.label ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									label: e.target.value || undefined,
								},
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

	const props = component.props;

	return (
		<AccordionItem value="html">
			<AccordionTrigger>HTML/JSX Content</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Content"
					description="Plain HTML or JSX with context access (use className= for JSX, class= for HTML)"
				>
					<Textarea
						value={props.content}
						onChange={(e) =>
							onChange({
								props: { ...props, content: e.target.value },
							})
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

	const props = component.props;
	// Support both 'label' and 'text' for button text (matches ButtonComponent behavior)
	const labelValue =
		props.label ?? (props as Record<string, unknown>).text ?? "";

	// Get workflow name for button display
	const workflowName = workflowsData?.find(
		(w) => w.id === props.workflow_id,
	)?.name;

	// Handle workflow selection from dialog
	const handleWorkflowSelect = (
		workflowIds: string[],
		_assignRoles: boolean,
	) => {
		const workflowId = workflowIds[0] || undefined;
		onChange({
			props: {
				...props,
				workflow_id: workflowId,
				action_params: {},
			},
		});
	};

	return (
		<AccordionItem value="button">
			<AccordionTrigger>Button</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Label">
					<Input
						value={String(labelValue)}
						onChange={(e) => {
							// Normalize to use 'label' and remove 'text' if present
							const newProps = {
								...props,
								label: e.target.value,
							};
							if ("text" in newProps) {
								delete (newProps as Record<string, unknown>)
									.text;
							}
							onChange({ props: newProps });
						}}
						placeholder="Button text..."
					/>
				</FormField>

				<FormField label="Variant">
					<Select
						value={props.variant ?? "default"}
						onValueChange={(value) =>
							onChange({
								props: {
									...props,
									variant: value as typeof props.variant,
								},
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
						value={props.size ?? "default"}
						onValueChange={(value) =>
							onChange({
								props: {
									...props,
									size: value as typeof props.size,
								},
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
							typeof props.disabled === "boolean"
								? props.disabled
									? "true"
									: ""
								: (props.disabled ?? "")
						}
						onChange={(e) => {
							const value = e.target.value;
							// Empty = not disabled
							if (!value || value === "false") {
								onChange({
									props: { ...props, disabled: false },
								});
							} else if (value === "true") {
								onChange({
									props: { ...props, disabled: true },
								});
							} else {
								// Expression string
								onChange({
									props: { ...props, disabled: value },
								});
							}
						}}
						placeholder="false, true, or {{ expression }}"
					/>
				</FormField>

				<FormField label="Action Type">
					<Select
						value={props.action_type ?? ""}
						onValueChange={(value) =>
							onChange({
								props: {
									...props,
									action_type: value as ButtonActionType,
								},
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

				{props.action_type === "navigate" && (
					<FormField
						label="Navigate To"
						description="Path to navigate (supports expressions)"
					>
						<Input
							value={props.navigate_to ?? ""}
							onChange={(e) =>
								onChange({
									props: {
										...props,
										navigate_to: e.target.value,
									},
								})
							}
							placeholder="/path/to/page"
						/>
					</FormField>
				)}

				{props.action_type === "workflow" && (
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
								{props.workflow_id && (
									<Button
										variant="ghost"
										size="icon"
										onClick={() =>
											onChange({
												props: {
													...props,
													workflow_id: undefined,
													action_params: {},
												},
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
									props.workflow_id ? [props.workflow_id] : []
								}
								onSelect={handleWorkflowSelect}
							/>
						</FormField>

						{props.workflow_id && (
							<FormField
								label="Parameters"
								description="Values to pass to the workflow"
							>
								<WorkflowParameterEditor
									workflowId={props.workflow_id}
									value={props.action_params ?? {}}
									onChange={(actionParams) =>
										onChange({
											props: { ...props, action_params: actionParams },
										})
									}
									isRowAction={false}
								/>
							</FormField>
						)}
					</>
				)}

				{props.action_type === "submit" && (
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
								{props.workflow_id && (
									<Button
										variant="ghost"
										size="icon"
										onClick={() =>
											onChange({
												props: {
													...props,
													workflow_id: undefined,
													action_params: {},
												},
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
									props.workflow_id ? [props.workflow_id] : []
								}
								onSelect={handleWorkflowSelect}
							/>
						</FormField>

						{props.workflow_id && (
							<FormField
								label="Additional Parameters"
								description="Extra values to include (form fields auto-included)"
							>
								<WorkflowParameterEditor
									workflowId={props.workflow_id}
									value={props.action_params ?? {}}
									onChange={(actionParams) =>
										onChange({
											props: { ...props, action_params: actionParams },
										})
									}
									isRowAction={false}
								/>
							</FormField>
						)}
					</>
				)}

				{props.action_type === "custom" && (
					<>
						<FormField label="Custom Action ID">
							<Input
								value={props.custom_action_id ?? ""}
								onChange={(e) =>
									onChange({
										props: {
											...props,
											custom_action_id: e.target.value,
										},
									})
								}
								placeholder="action-id"
							/>
						</FormField>

						<FormField label="Parameters">
							<KeyValueEditor
								value={props.action_params ?? {}}
								onChange={(actionParams) =>
									onChange({
										props: { ...props, action_params: actionParams },
									})
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

	const props = component.props;

	return (
		<AccordionItem value="image">
			<AccordionTrigger>Image</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Source URL"
					description="Image URL or expression"
				>
					<Input
						value={props.src}
						onChange={(e) =>
							onChange({
								props: { ...props, src: e.target.value },
							})
						}
						placeholder="https://example.com/image.png"
					/>
				</FormField>

				<FormField
					label="Alt Text"
					description="Accessibility description"
				>
					<Input
						value={props.alt ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									alt: e.target.value || undefined,
								},
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
						value={props.max_width ?? ""}
						onChange={(e) => {
							const val = e.target.value;
							const numVal = Number(val);
							onChange({
								props: {
									...props,
									max_width: val
										? isNaN(numVal)
											? val
											: numVal
										: undefined,
								},
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
						value={props.max_height ?? ""}
						onChange={(e) => {
							const val = e.target.value;
							const numVal = Number(val);
							onChange({
								props: {
									...props,
									max_height: val
										? isNaN(numVal)
											? val
											: numVal
										: undefined,
								},
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
						value={props.object_fit ?? "contain"}
						onValueChange={(value) =>
							onChange({
								props: {
									...props,
									object_fit: value as typeof props.object_fit,
								},
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

	const props = component.props;

	return (
		<AccordionItem value="card">
			<AccordionTrigger>Card</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Title" description="Optional card title">
					<Input
						value={props.title ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									title: e.target.value || undefined,
								},
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
						value={props.description ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									description: e.target.value || undefined,
								},
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

	const props = component.props;

	return (
		<AccordionItem value="stat-card">
			<AccordionTrigger>Stat Card</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Title">
					<Input
						value={props.title}
						onChange={(e) =>
							onChange({
								props: { ...props, title: e.target.value },
							})
						}
					/>
				</FormField>

				<FormField
					label="Value"
					description="Supports expressions like {{ data.count }}"
				>
					<Input
						value={props.value}
						onChange={(e) =>
							onChange({
								props: { ...props, value: e.target.value },
							})
						}
					/>
				</FormField>

				<FormField label="Description" description="Additional context">
					<Input
						value={props.description ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									description: e.target.value || undefined,
								},
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
						value={props.icon ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									icon: e.target.value || undefined,
								},
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField label="Trend" description="Optional trend indicator">
					<JsonEditor
						value={
							props.trend ?? { value: "", direction: "neutral" }
						}
						onChange={(value) =>
							onChange({
								props: {
									...props,
									trend: value as typeof props.trend,
								},
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
							props.on_click ?? {
								type: "navigate",
								navigate_to: "",
							}
						}
						onChange={(value) =>
							onChange({
								props: {
									...props,
									on_click: value as typeof props.on_click,
								},
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

	const props = component.props;

	return (
		<AccordionItem value="data-table">
			<AccordionTrigger>Data Table</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Data Source"
					description="ID of a page data source (e.g., clientsList)"
				>
					<Input
						value={props.data_source}
						onChange={(e) =>
							onChange({
								props: { ...props, data_source: e.target.value },
							})
						}
						placeholder="dataSourceId"
					/>
				</FormField>

				<FormField
					label="Data Path"
					description="Path to array in result (e.g., 'clients' if workflow returns { clients: [...] })"
				>
					<Input
						value={props.data_path ?? ""}
						onChange={(e) =>
							onChange({
								props: { ...props, data_path: e.target.value || undefined },
							})
						}
						placeholder="Leave empty if result is already an array"
					/>
				</FormField>

				<FormField label="Searchable">
					<div className="flex items-center gap-2">
						<Switch
							checked={props.searchable ?? false}
							onCheckedChange={(checked) =>
								onChange({
									props: { ...props, searchable: checked },
								})
							}
						/>
						<span className="text-sm text-muted-foreground">
							{props.searchable ? "Yes" : "No"}
						</span>
					</div>
				</FormField>

				<FormField label="Selectable">
					<div className="flex items-center gap-2">
						<Switch
							checked={props.selectable ?? false}
							onCheckedChange={(checked) =>
								onChange({
									props: { ...props, selectable: checked },
								})
							}
						/>
						<span className="text-sm text-muted-foreground">
							{props.selectable ? "Yes" : "No"}
						</span>
					</div>
				</FormField>

				<FormField label="Paginated">
					<div className="flex items-center gap-2">
						<Switch
							checked={props.paginated ?? false}
							onCheckedChange={(checked) =>
								onChange({
									props: { ...props, paginated: checked },
								})
							}
						/>
						<span className="text-sm text-muted-foreground">
							{props.paginated ? "Yes" : "No"}
						</span>
					</div>
				</FormField>

				{props.paginated && (
					<FormField label="Page Size" description="Rows per page">
						<Input
							type="number"
							value={props.page_size ?? 10}
							onChange={(e) =>
								onChange({
									props: {
										...props,
										page_size: Number(e.target.value) || 10,
									},
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
						value={props.empty_message ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									empty_message: e.target.value || undefined,
								},
							})
						}
						placeholder="No data available"
					/>
				</FormField>

				<FormField label="Columns" description="Define table columns">
					<ColumnBuilder
						value={props.columns}
						onChange={(columns) =>
							onChange({
								props: { ...props, columns },
							})
						}
					/>
				</FormField>

				<FormField
					label="Row Actions"
					description="Actions available for each row"
				>
					<TableActionBuilder
						value={props.row_actions ?? []}
						onChange={(rowActions) =>
							onChange({
								props: { ...props, row_actions: rowActions },
							})
						}
						isRowAction={true}
					/>
				</FormField>

				<FormField
					label="Header Actions"
					description="Actions in table header"
				>
					<TableActionBuilder
						value={props.header_actions ?? []}
						onChange={(headerActions) =>
							onChange({
								props: { ...props, header_actions: headerActions },
							})
						}
						isRowAction={false}
					/>
				</FormField>

				<FormField
					label="Row Click Behavior"
					description="What happens when a row is clicked"
				>
					<Select
						value={props.on_row_click?.type ?? "none"}
						onValueChange={(type) => {
							if (type === "none") {
								onChange({
									props: { ...props, on_row_click: undefined },
								});
							} else {
								onChange({
									props: {
										...props,
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

				{props.on_row_click?.type === "navigate" && (
					<FormField
						label="Navigate To"
						description="Path with {{ row.* }} expressions"
					>
						<Input
							value={props.on_row_click.navigate_to ?? ""}
							onChange={(e) =>
								onChange({
									props: {
										...props,
										on_row_click: {
											...props.on_row_click!,
											navigate_to: e.target.value,
										},
									},
								})
							}
							placeholder="/details/{{ row.id }}"
						/>
					</FormField>
				)}

				{props.on_row_click?.type === "set-variable" && (
					<FormField
						label="Variable Name"
						description="Store the row in this variable"
					>
						<Input
							value={props.on_row_click.variable_name ?? ""}
							onChange={(e) =>
								onChange({
									props: {
										...props,
										on_row_click: {
											...props.on_row_click!,
											variable_name: e.target.value,
										},
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

	const props = component.props;

	return (
		<AccordionItem value="tabs">
			<AccordionTrigger>Tabs</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Orientation">
					<Select
						value={props.orientation ?? "horizontal"}
						onValueChange={(value) =>
							onChange({
								props: {
									...props,
									orientation: value as
										| "horizontal"
										| "vertical",
								},
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
						value={props.default_tab ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									default_tab: e.target.value || undefined,
								},
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
						value={props.items}
						onChange={(value) =>
							onChange({
								props: {
									...props,
									items: value as typeof props.items,
								},
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

	const props = component.props;

	return (
		<AccordionItem value="badge">
			<AccordionTrigger>Badge</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Text">
					<Input
						value={props.text}
						onChange={(e) =>
							onChange({
								props: { ...props, text: e.target.value },
							})
						}
					/>
				</FormField>

				<FormField label="Variant">
					<Select
						value={props.variant ?? "default"}
						onValueChange={(value) =>
							onChange({
								props: {
									...props,
									variant: value as typeof props.variant,
								},
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

	const props = component.props;

	return (
		<AccordionItem value="progress">
			<AccordionTrigger>Progress</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Value" description="0-100 or expression">
					<Input
						value={String(props.value)}
						onChange={(e) => {
							const val = e.target.value;
							const numVal = Number(val);
							onChange({
								props: {
									...props,
									value: isNaN(numVal) ? val : numVal,
								},
							});
						}}
					/>
				</FormField>

				<FormField label="Show Label">
					<div className="flex items-center gap-2">
						<Switch
							checked={props.show_label ?? false}
							onCheckedChange={(checked) =>
								onChange({
									props: { ...props, show_label: checked },
								})
							}
						/>
						<span className="text-sm text-muted-foreground">
							{props.show_label ? "Yes" : "No"}
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

	const props = component.props;

	return (
		<AccordionItem value="divider">
			<AccordionTrigger>Divider</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField label="Orientation">
					<Select
						value={props.orientation ?? "horizontal"}
						onValueChange={(value) =>
							onChange({
								props: {
									...props,
									orientation: value as
										| "horizontal"
										| "vertical",
								},
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

	const props = component.props;

	return (
		<AccordionItem value="spacer">
			<AccordionTrigger>Spacer</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Size"
					description="Size in pixels or Tailwind units"
				>
					<Input
						value={String(props.size ?? "")}
						onChange={(e) => {
							const val = e.target.value;
							const numVal = Number(val);
							onChange({
								props: {
									...props,
									size: val
										? isNaN(numVal)
											? val
											: numVal
										: undefined,
								},
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

	const props = component.props;

	return (
		<AccordionItem value="text-input">
			<AccordionTrigger>Text Input</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Field ID"
					description="ID for accessing value via {{ field.fieldId }}"
				>
					<Input
						value={props.field_id}
						onChange={(e) =>
							onChange({
								props: { ...props, field_id: e.target.value },
							})
						}
						placeholder="fieldName"
					/>
				</FormField>

				<FormField label="Label">
					<Input
						value={props.label ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									label: e.target.value || undefined,
								},
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField label="Placeholder">
					<Input
						value={props.placeholder ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									placeholder: e.target.value || undefined,
								},
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
						value={props.default_value ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									default_value: e.target.value || undefined,
								},
							})
						}
					/>
				</FormField>

				<FormField label="Input Type">
					<Select
						value={props.input_type ?? "text"}
						onValueChange={(value) =>
							onChange({
								props: {
									...props,
									input_type: value as typeof props.input_type,
								},
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
							checked={props.required ?? false}
							onCheckedChange={(checked) =>
								onChange({
									props: { ...props, required: checked },
								})
							}
						/>
						<span className="text-sm text-muted-foreground">
							{props.required ? "Yes" : "No"}
						</span>
					</div>
				</FormField>

				<FormField
					label="Disabled"
					description="Boolean or expression (e.g., {{ status == 'locked' }})"
				>
					<Input
						value={
							typeof props.disabled === "boolean"
								? props.disabled
									? "true"
									: ""
								: (props.disabled ?? "")
						}
						onChange={(e) => {
							const value = e.target.value;
							if (!value || value === "false") {
								onChange({
									props: { ...props, disabled: false },
								});
							} else if (value === "true") {
								onChange({
									props: { ...props, disabled: true },
								});
							} else {
								onChange({
									props: { ...props, disabled: value },
								});
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

	const props = component.props;

	return (
		<AccordionItem value="number-input">
			<AccordionTrigger>Number Input</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Field ID"
					description="ID for accessing value via {{ field.fieldId }}"
				>
					<Input
						value={props.field_id}
						onChange={(e) =>
							onChange({
								props: { ...props, field_id: e.target.value },
							})
						}
						placeholder="fieldName"
					/>
				</FormField>

				<FormField label="Label">
					<Input
						value={props.label ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									label: e.target.value || undefined,
								},
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField label="Placeholder">
					<Input
						value={props.placeholder ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									placeholder: e.target.value || undefined,
								},
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
						value={String(props.default_value ?? "")}
						onChange={(e) => {
							const val = e.target.value;
							const numVal = Number(val);
							onChange({
								props: {
									...props,
									default_value: val
										? isNaN(numVal)
											? val
											: numVal
										: undefined,
								},
							});
						}}
					/>
				</FormField>

				<FormField label="Min">
					<Input
						type="number"
						value={props.min ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									min: e.target.value
										? Number(e.target.value)
										: undefined,
								},
							})
						}
					/>
				</FormField>

				<FormField label="Max">
					<Input
						type="number"
						value={props.max ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									max: e.target.value
										? Number(e.target.value)
										: undefined,
								},
							})
						}
					/>
				</FormField>

				<FormField label="Step">
					<Input
						type="number"
						value={props.step ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									step: e.target.value
										? Number(e.target.value)
										: undefined,
								},
							})
						}
						placeholder="1"
					/>
				</FormField>

				<FormField label="Required">
					<div className="flex items-center gap-2">
						<Switch
							checked={props.required ?? false}
							onCheckedChange={(checked) =>
								onChange({
									props: { ...props, required: checked },
								})
							}
						/>
						<span className="text-sm text-muted-foreground">
							{props.required ? "Yes" : "No"}
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

	const props = component.props;

	return (
		<AccordionItem value="select">
			<AccordionTrigger>Select</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Field ID"
					description="ID for accessing value via {{ field.fieldId }}"
				>
					<Input
						value={props.field_id}
						onChange={(e) =>
							onChange({
								props: { ...props, field_id: e.target.value },
							})
						}
						placeholder="fieldName"
					/>
				</FormField>

				<FormField label="Label">
					<Input
						value={props.label ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									label: e.target.value || undefined,
								},
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField label="Placeholder">
					<Input
						value={props.placeholder ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									placeholder: e.target.value || undefined,
								},
							})
						}
						placeholder="Select an option"
					/>
				</FormField>

				<FormField label="Default Value">
					<Input
						value={props.default_value ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									default_value: e.target.value || undefined,
								},
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
							Array.isArray(props.options) ? props.options : []
						}
						onChange={(options) =>
							onChange({
								props: { ...props, options },
							})
						}
					/>
				</FormField>

				<FormField
					label="Options Data Source"
					description="Data source name for dynamic options"
				>
					<Input
						value={props.options_source ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									options_source: e.target.value || undefined,
								},
							})
						}
						placeholder="None (use static options)"
					/>
				</FormField>

				{props.options_source && (
					<>
						<FormField
							label="Value Field"
							description="Field in data source for option value"
						>
							<Input
								value={props.value_field ?? ""}
								onChange={(e) =>
									onChange({
										props: {
											...props,
											value_field:
												e.target.value || undefined,
										},
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
								value={props.label_field ?? ""}
								onChange={(e) =>
									onChange({
										props: {
											...props,
											label_field:
												e.target.value || undefined,
										},
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
							checked={props.required ?? false}
							onCheckedChange={(checked) =>
								onChange({
									props: { ...props, required: checked },
								})
							}
						/>
						<span className="text-sm text-muted-foreground">
							{props.required ? "Yes" : "No"}
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

	const props = component.props;

	return (
		<AccordionItem value="checkbox">
			<AccordionTrigger>Checkbox</AccordionTrigger>
			<AccordionContent className="space-y-4 px-1">
				<FormField
					label="Field ID"
					description="ID for accessing value via {{ field.fieldId }}"
				>
					<Input
						value={props.field_id}
						onChange={(e) =>
							onChange({
								props: { ...props, field_id: e.target.value },
							})
						}
						placeholder="fieldName"
					/>
				</FormField>

				<FormField label="Label">
					<Input
						value={props.label}
						onChange={(e) =>
							onChange({
								props: { ...props, label: e.target.value },
							})
						}
					/>
				</FormField>

				<FormField
					label="Description"
					description="Help text below the checkbox"
				>
					<Input
						value={props.description ?? ""}
						onChange={(e) =>
							onChange({
								props: {
									...props,
									description: e.target.value || undefined,
								},
							})
						}
						placeholder="None"
					/>
				</FormField>

				<FormField label="Default Checked">
					<div className="flex items-center gap-2">
						<Switch
							checked={props.default_checked ?? false}
							onCheckedChange={(checked) =>
								onChange({
									props: {
										...props,
										default_checked: checked,
									},
								})
							}
						/>
						<span className="text-sm text-muted-foreground">
							{props.default_checked ? "Yes" : "No"}
						</span>
					</div>
				</FormField>

				<FormField label="Required">
					<div className="flex items-center gap-2">
						<Switch
							checked={props.required ?? false}
							onCheckedChange={(checked) =>
								onChange({
									props: { ...props, required: checked },
								})
							}
						/>
						<span className="text-sm text-muted-foreground">
							{props.required ? "Yes" : "No"}
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
