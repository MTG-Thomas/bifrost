import { useState } from "react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useScopeStore } from "@/stores/scopeStore";

interface RegisterWorkflowDialogProps {
	open: boolean;
	functionName: string;
	onConfirm: (orgId: string | null) => void;
	onCancel: () => void;
}

export function RegisterWorkflowDialog({
	open,
	functionName,
	onConfirm,
	onCancel,
}: RegisterWorkflowDialogProps) {
	const scope = useScopeStore((s) => s.scope);
	const [selectedOrgId, setSelectedOrgId] = useState<string | null | undefined>(
		scope.orgId ?? null,
	);

	return (
		<Dialog open={open} onOpenChange={(isOpen) => !isOpen && onCancel()}>
			<DialogContent className="z-100 sm:max-w-md">
				<DialogHeader>
					<DialogTitle>Register Workflow</DialogTitle>
					<DialogDescription>
						Register <code className="font-mono text-sm font-semibold">{functionName}</code> to
						which organization?
					</DialogDescription>
				</DialogHeader>
				<div className="py-2">
					<OrganizationSelect
						value={selectedOrgId}
						onChange={(val) => setSelectedOrgId(val ?? null)}
						showGlobal={true}
						showAll={false}
						contentClassName="z-[101]"
					/>
				</div>
				<DialogFooter>
					<Button variant="outline" onClick={onCancel}>
						Cancel
					</Button>
					<Button
						onClick={() => {
							onConfirm(selectedOrgId ?? null);
						}}
					>
						Register
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
