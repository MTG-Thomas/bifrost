import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Combobox } from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Switch } from "@/components/ui/switch";
import { Shield, AlertCircle, Loader2, AlertTriangle } from "lucide-react";
import { useUpdateUser } from "@/hooks/useUsers";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useAuth } from "@/contexts/AuthContext";
import { toast } from "sonner";
import type { components } from "@/lib/v1";

type User = components["schemas"]["UserPublic"];
type Organization = components["schemas"]["OrganizationPublic"];

interface EditUserDialogProps {
	user: User | undefined;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

// Extract dialog content to separate component for key-based remounting
function EditUserDialogContent({
	user,
	onOpenChange,
}: {
	user: User;
	onOpenChange: (open: boolean) => void;
}) {
	const [displayName, setDisplayName] = useState(user.name || "");
	const [isActive, setIsActive] = useState(user.is_active);
	const [isPlatformAdmin, setIsPlatformAdmin] = useState(
		user.is_superuser,
	);
	const [orgId, setOrgId] = useState<string>(user.organization_id || "");
	const [validationError, setValidationError] = useState<string | null>(null);

	const updateMutation = useUpdateUser();
	const { data: organizations, isLoading: orgsLoading } = useOrganizations();
	const { user: currentUser } = useAuth();

	// Find the provider org (for auto-selecting when promoting to platform admin)
	const providerOrg = organizations?.find((org: Organization) => org.is_provider);

	// Check if editing own account
	const isEditingSelf = !!(currentUser && user.id === currentUser.id);

	const isRoleChanging = user.is_superuser !== isPlatformAdmin;
	const isDemoting = user.is_superuser && !isPlatformAdmin;
	const isPromoting = !user.is_superuser && isPlatformAdmin;

	// Auto-select provider org when promoting to platform admin
	const handleUserTypeChange = (value: string) => {
		const isAdmin = value === "platform";
		setIsPlatformAdmin(isAdmin);
		if (isAdmin && providerOrg) {
			setOrgId(providerOrg.id);
		} else if (!isAdmin && orgId === providerOrg?.id) {
			// Clear provider org if switching to org user
			setOrgId("");
		}
	};

	const validateForm = (): boolean => {
		if (!displayName || displayName.trim().length === 0) {
			setValidationError("Please enter a display name");
			return false;
		}
		if (!orgId) {
			setValidationError("Please select an organization");
			return false;
		}
		setValidationError(null);
		return true;
	};

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();

		if (!validateForm()) {
			return;
		}

		// Build request body - only send changed fields
		const body = {
			name:
				displayName.trim() !== (user.name || "")
					? displayName.trim()
					: null,
			is_active:
				!isEditingSelf && isActive !== user.is_active ? isActive : null,
			is_superuser:
				!isEditingSelf && isRoleChanging
					? isPlatformAdmin
					: null,
			organization_id:
				!isEditingSelf && orgId !== (user.organization_id || "")
					? orgId || null
					: null,
		};

		// If no actual changes, just close
		if (
			body.name === null &&
			body.is_active === null &&
			body.is_superuser === null &&
			body.organization_id === null
		) {
			toast.info("No changes to save");
			onOpenChange(false);
			return;
		}

		try {
			await updateMutation.mutateAsync({
				params: { path: { user_id: user.id } },
				body,
			});

			toast.success("User updated successfully", {
				description: `Changes to ${user.name || user.email} have been saved`,
			});

			onOpenChange(false);
		} catch (error) {
			const errorMessage =
				error instanceof Error
					? error.message
					: "Unknown error occurred";
			toast.error("Failed to update user", {
				description: errorMessage,
			});
		}
	};

	return (
		<DialogContent className="sm:max-w-[500px]">
			<DialogHeader>
				<DialogTitle>Edit User</DialogTitle>
				<DialogDescription>
					Update user details and permissions for {user.email}
				</DialogDescription>
			</DialogHeader>

			<form onSubmit={handleSubmit} className="space-y-4 mt-4">
				{isEditingSelf && (
					<Alert>
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>
							You are editing your own account. You can only
							change your display name. Role and status changes
							must be made by another administrator.
						</AlertDescription>
					</Alert>
				)}

				{validationError && (
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>{validationError}</AlertDescription>
					</Alert>
				)}

				<div className="space-y-2">
					<Label htmlFor="email-display">Email Address</Label>
					<Input
						id="email-display"
						type="email"
						value={user.email}
						disabled
						className="bg-muted"
					/>
					<p className="text-xs text-muted-foreground">
						Email address cannot be changed
					</p>
				</div>

				<div className="space-y-2">
					<Label htmlFor="displayName">Display Name</Label>
					<Input
						id="displayName"
						type="text"
						placeholder="John Doe"
						value={displayName}
						onChange={(e) => setDisplayName(e.target.value)}
						required
					/>
				</div>

				<div className="flex items-center justify-between rounded-lg border p-4">
					<div className="space-y-0.5">
						<Label htmlFor="active">Account Status</Label>
						<p className="text-xs text-muted-foreground">
							{isActive
								? "User can access the platform"
								: "User access is disabled"}
						</p>
					</div>
					<Switch
						id="active"
						checked={isActive}
						onCheckedChange={setIsActive}
						disabled={isEditingSelf}
					/>
				</div>

				<div className="space-y-2">
					<Label htmlFor="userType">User Type</Label>
					<Combobox
						id="userType"
						value={isPlatformAdmin ? "platform" : "org"}
						onValueChange={handleUserTypeChange}
						disabled={isEditingSelf}
						options={[
							{
								value: "platform",
								label: "Platform Administrator",
								description:
									"Full access to all organizations and settings",
							},
							{
								value: "org",
								label: "Organization User",
								description:
									"Access limited to specific organization",
							},
						]}
						placeholder="Select user type"
					/>
				</div>

				<div className="space-y-2">
					<Label htmlFor="organization">Organization</Label>
					<Combobox
						id="organization"
						value={orgId}
						onValueChange={setOrgId}
						disabled={isPlatformAdmin || isEditingSelf}
						options={
							organizations?.map((org: Organization) => {
								const option: {
									value: string;
									label: string;
									description?: string;
								} = {
									value: org.id,
									label: org.is_provider
										? `${org.name} (Provider)`
										: org.name,
								};
								if (org.domain) {
									option.description = `@${org.domain}`;
								}
								return option;
							}) ?? []
						}
						placeholder="Select an organization..."
						searchPlaceholder="Search organizations..."
						emptyText="No organizations found."
						isLoading={orgsLoading}
					/>
					<p className="text-xs text-muted-foreground">
						{isPlatformAdmin
							? "Platform administrators are assigned to the provider organization"
							: "The organization this user belongs to"}
					</p>
				</div>

				{isPlatformAdmin && isPromoting && (
					<Alert>
						<Shield className="h-4 w-4" />
						<AlertDescription>
							You are promoting this user to Platform
							Administrator. They will gain unrestricted access
							to all features, organizations, and settings.
						</AlertDescription>
					</Alert>
				)}

				{isDemoting && (
					<Alert variant="destructive">
						<AlertTriangle className="h-4 w-4" />
						<AlertDescription>
							You are demoting this user from Platform
							Administrator to Organization User. They will
							lose access to all other organizations and
							platform settings.
						</AlertDescription>
					</Alert>
				)}

				<DialogFooter>
					<Button
						type="button"
						variant="outline"
						onClick={() => onOpenChange(false)}
						disabled={updateMutation.isPending}
					>
						Cancel
					</Button>
					<Button type="submit" disabled={updateMutation.isPending}>
						{updateMutation.isPending && (
							<Loader2 className="mr-2 h-4 w-4 animate-spin" />
						)}
						Save Changes
					</Button>
				</DialogFooter>
			</form>
		</DialogContent>
	);
}

export function EditUserDialog({
	user,
	open,
	onOpenChange,
}: EditUserDialogProps) {
	if (!user) return null;

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			{open && (
				<EditUserDialogContent
					user={user}
					onOpenChange={onOpenChange}
				/>
			)}
		</Dialog>
	);
}
