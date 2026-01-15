/**
 * WorkflowEditDialog Component
 *
 * Dialog for editing workflow settings including organization scope,
 * access level, and role assignments.
 * Platform admin only.
 */

import { useEffect, useState } from "react";
import { Loader2, Check, ChevronsUpDown, X, Shield, Users } from "lucide-react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@/components/ui/command";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { useRoles } from "@/hooks/useRoles";
import { useUpdateWorkflow } from "@/hooks/useWorkflows";
import {
	useWorkflowRoles,
	useAssignRolesToWorkflow,
	useRemoveRoleFromWorkflow,
} from "@/hooks/useWorkflowRoles";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import type { components } from "@/lib/v1";

type RolePublic = components["schemas"]["RolePublic"];

type WorkflowAccessLevel = "authenticated" | "role_based";

const ACCESS_LEVELS: {
	value: WorkflowAccessLevel;
	label: string;
	description: string;
	icon: React.ReactNode;
}[] = [
	{
		value: "authenticated",
		label: "Authenticated",
		description: "Any logged-in user can execute",
		icon: <Users className="h-4 w-4" />,
	},
	{
		value: "role_based",
		label: "Role-Based",
		description: "Only users with assigned roles can execute",
		icon: <Shield className="h-4 w-4" />,
	},
];

interface WorkflowEditDialogProps {
	workflow: {
		id?: string;
		name?: string;
		organization_id?: string | null;
		access_level?: string;
	} | null;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onSuccess?: () => void;
}

export function WorkflowEditDialog({
	workflow,
	open,
	onOpenChange,
	onSuccess,
}: WorkflowEditDialogProps) {
	const { data: roles } = useRoles();
	const updateWorkflow = useUpdateWorkflow();
	const assignRoles = useAssignRolesToWorkflow();
	const removeRole = useRemoveRoleFromWorkflow();

	// Local state
	const [organizationId, setOrganizationId] = useState<string | null | undefined>(undefined);
	const [accessLevel, setAccessLevel] = useState<WorkflowAccessLevel>("role_based");
	const [selectedRoleIds, setSelectedRoleIds] = useState<string[]>([]);
	const [rolesOpen, setRolesOpen] = useState(false);
	const [isSaving, setIsSaving] = useState(false);

	// Fetch current workflow roles
	const workflowRolesQuery = useWorkflowRoles(workflow?.id);

	// Load workflow data when dialog opens or workflow changes
	useEffect(() => {
		if (workflow && open) {
			setOrganizationId(workflow.organization_id ?? null);
			setAccessLevel((workflow.access_level as WorkflowAccessLevel) || "role_based");
			// Fetch roles - refetch is stable and intentionally not in deps to avoid loops
			workflowRolesQuery.refetch().then((result) => {
				if (result.data) {
					setSelectedRoleIds(result.data.role_ids || []);
				}
			});
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [workflow, open]);

	const handleClose = () => {
		onOpenChange(false);
	};

	const handleSave = async () => {
		if (!workflow?.id) return;

		setIsSaving(true);
		try {
			// 1. Update workflow settings (org scope and access level)
			await updateWorkflow.mutateAsync(workflow.id, {
				organizationId: organizationId,
				accessLevel: accessLevel,
			});

			// 2. Handle role changes
			const currentRoleIds = workflowRolesQuery.data?.role_ids || [];

			// Find roles to add
			const rolesToAdd = selectedRoleIds.filter(
				(id) => !currentRoleIds.includes(id)
			);

			// Find roles to remove
			const rolesToRemove = currentRoleIds.filter(
				(id) => !selectedRoleIds.includes(id)
			);

			// Add new roles
			if (rolesToAdd.length > 0) {
				await assignRoles.mutateAsync(workflow.id, rolesToAdd);
			}

			// Remove old roles
			for (const roleId of rolesToRemove) {
				await removeRole.mutateAsync(workflow.id, roleId);
			}

			toast.success("Workflow updated", {
				description: `"${workflow.name}" has been updated successfully`,
			});

			onSuccess?.();
			handleClose();
		} catch (error) {
			toast.error(
				error instanceof Error ? error.message : "Failed to update workflow"
			);
		} finally {
			setIsSaving(false);
		}
	};

	const handleRoleToggle = (roleId: string) => {
		setSelectedRoleIds((prev) =>
			prev.includes(roleId)
				? prev.filter((id) => id !== roleId)
				: [...prev, roleId]
		);
	};

	return (
		<Dialog open={open} onOpenChange={handleClose}>
			<DialogContent className="sm:max-w-[500px]">
				<DialogHeader>
					<DialogTitle>Edit Workflow Settings</DialogTitle>
					<DialogDescription>
						Configure organization scope and access control for "
						{workflow?.name}"
					</DialogDescription>
				</DialogHeader>

				<div className="space-y-6 py-4">
					{/* Organization Scope */}
					<div className="space-y-2">
						<Label>Organization Scope</Label>
						<OrganizationSelect
							value={organizationId}
							onChange={setOrganizationId}
							showAll={false}
							showGlobal={true}
							placeholder="Select organization..."
						/>
						<p className="text-xs text-muted-foreground">
							Global workflows are available to all organizations
						</p>
					</div>

					{/* Access Level */}
					<div className="space-y-2">
						<Label>Access Level</Label>
						<Select
							value={accessLevel}
							onValueChange={(value) =>
								setAccessLevel(value as WorkflowAccessLevel)
							}
						>
							<SelectTrigger>
								<SelectValue placeholder="Select access level" />
							</SelectTrigger>
							<SelectContent>
								{ACCESS_LEVELS.map((level) => (
									<SelectItem key={level.value} value={level.value}>
										<div className="flex items-center gap-2">
											{level.icon}
											<div className="flex flex-col">
												<span>{level.label}</span>
												<span className="text-xs text-muted-foreground">
													{level.description}
												</span>
											</div>
										</div>
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</div>

					{/* Roles - Only visible when role_based is selected */}
					{accessLevel === "role_based" && (
						<div className="space-y-2">
							<Label>
								Assigned Roles{" "}
								{selectedRoleIds.length > 0 && `(${selectedRoleIds.length})`}
							</Label>
							<Popover open={rolesOpen} onOpenChange={setRolesOpen}>
								<PopoverTrigger asChild>
									<Button
										variant="outline"
										role="combobox"
										aria-expanded={rolesOpen}
										className="w-full justify-between font-normal"
									>
										<span className="text-muted-foreground">
											Select roles...
										</span>
										<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
									</Button>
								</PopoverTrigger>
								<PopoverContent
									className="w-[var(--radix-popover-trigger-width)] p-0"
									align="start"
								>
									<Command>
										<CommandInput placeholder="Search roles..." />
										<CommandList>
											<CommandEmpty>No roles found.</CommandEmpty>
											<CommandGroup>
												{roles?.map((role: RolePublic) => (
													<CommandItem
														key={role.id}
														value={role.name || ""}
														onSelect={() => handleRoleToggle(role.id)}
													>
														<div className="flex items-center gap-2 flex-1">
															<Checkbox
																checked={selectedRoleIds.includes(role.id)}
															/>
															<div className="flex flex-col">
																<span className="font-medium">
																	{role.name}
																</span>
																{role.description && (
																	<span className="text-xs text-muted-foreground">
																		{role.description}
																	</span>
																)}
															</div>
														</div>
														<Check
															className={cn(
																"ml-auto h-4 w-4",
																selectedRoleIds.includes(role.id)
																	? "opacity-100"
																	: "opacity-0"
															)}
														/>
													</CommandItem>
												))}
											</CommandGroup>
										</CommandList>
									</Command>
								</PopoverContent>
							</Popover>

							{/* Selected roles display */}
							{selectedRoleIds.length > 0 && (
								<div className="flex flex-wrap gap-2 p-2 border rounded-md bg-muted/50">
									{selectedRoleIds.map((roleId) => {
										const role = roles?.find(
											(r: RolePublic) => r.id === roleId
										);
										return (
											<Badge
												key={roleId}
												variant="secondary"
												className="gap-1"
											>
												{role?.name || roleId}
												<X
													className="h-3 w-3 cursor-pointer"
													onClick={() => handleRoleToggle(roleId)}
												/>
											</Badge>
										);
									})}
								</div>
							)}

							<p className="text-xs text-muted-foreground">
								Users must have at least one of these roles to execute this
								workflow
							</p>

							{selectedRoleIds.length === 0 && (
								<p className="text-xs text-yellow-600 dark:text-yellow-500">
									No roles assigned - only platform admins can execute this
									workflow
								</p>
							)}
						</div>
					)}
				</div>

				<DialogFooter>
					<Button variant="outline" onClick={handleClose} disabled={isSaving}>
						Cancel
					</Button>
					<Button onClick={handleSave} disabled={isSaving}>
						{isSaving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
						{isSaving ? "Saving..." : "Save Changes"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
