import { Loader2 } from "lucide-react";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";

interface ExecutionCancelDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	workflowName: string;
	onConfirm: () => void;
}

export function ExecutionCancelDialog({
	open,
	onOpenChange,
	workflowName,
	onConfirm,
}: ExecutionCancelDialogProps) {
	return (
		<AlertDialog open={open} onOpenChange={onOpenChange}>
			<AlertDialogContent>
				<AlertDialogHeader>
					<AlertDialogTitle>Cancel Execution?</AlertDialogTitle>
					<AlertDialogDescription>
						Are you sure you want to cancel the execution of{" "}
						<span className="font-semibold">
							{workflowName}
						</span>
						? This action cannot be undone.
					</AlertDialogDescription>
				</AlertDialogHeader>
				<AlertDialogFooter>
					<AlertDialogCancel>No, keep running</AlertDialogCancel>
					<AlertDialogAction
						onClick={onConfirm}
						className="bg-destructive hover:bg-destructive/90"
					>
						Yes, cancel execution
					</AlertDialogAction>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}

interface ExecutionRerunDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	workflowName: string;
	isRerunning: boolean;
	onConfirm: () => void;
}

export function ExecutionRerunDialog({
	open,
	onOpenChange,
	workflowName,
	isRerunning,
	onConfirm,
}: ExecutionRerunDialogProps) {
	return (
		<AlertDialog open={open} onOpenChange={onOpenChange}>
			<AlertDialogContent>
				<AlertDialogHeader>
					<AlertDialogTitle>Rerun Workflow?</AlertDialogTitle>
					<AlertDialogDescription>
						This will execute{" "}
						<span className="font-semibold">
							{workflowName}
						</span>{" "}
						again with the same input parameters. You will be
						redirected to the new execution.
					</AlertDialogDescription>
				</AlertDialogHeader>
				<AlertDialogFooter>
					<AlertDialogCancel disabled={isRerunning}>
						Cancel
					</AlertDialogCancel>
					<AlertDialogAction
						onClick={onConfirm}
						disabled={isRerunning}
					>
						{isRerunning ? (
							<>
								<Loader2 className="mr-2 h-4 w-4 animate-spin" />
								Rerunning...
							</>
						) : (
							"Yes, rerun workflow"
						)}
					</AlertDialogAction>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}
