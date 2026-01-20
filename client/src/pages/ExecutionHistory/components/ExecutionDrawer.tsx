import {
    Sheet,
    SheetContent,
    SheetHeader,
    SheetTitle,
    SheetDescription,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ExternalLink, Clock, User, Building2, AlertCircle } from "lucide-react";
import { useExecution, useExecutionLogs } from "@/hooks/useExecutions";
import { ExecutionLogsPanel } from "@/components/execution/ExecutionLogsPanel";
import { ExecutionResultPanel } from "@/components/execution/ExecutionResultPanel";
import { ExecutionStatusBadge } from "@/components/execution/ExecutionStatusBadge";
import { useAuth } from "@/contexts/AuthContext";
import { formatDate } from "@/lib/utils";

interface ExecutionDrawerProps {
    executionId: string | null;
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

function formatDuration(ms: number): string {
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    const minutes = Math.floor(ms / 60000);
    const seconds = ((ms % 60000) / 1000).toFixed(0);
    return `${minutes}m ${seconds}s`;
}

export function ExecutionDrawer({
    executionId,
    open,
    onOpenChange,
}: ExecutionDrawerProps) {
    const { isPlatformAdmin } = useAuth();

    // Fetch execution details when drawer is open and we have an ID
    const {
        data: execution,
        isLoading: isLoadingExecution,
        error: executionError,
    } = useExecution(executionId ?? undefined);

    // Fetch logs separately (progressive loading)
    const {
        data: logs,
        isLoading: isLoadingLogs,
    } = useExecutionLogs(executionId ?? undefined, open && !!executionId);

    const handleOpenInNewTab = () => {
        if (executionId) {
            window.open(`/executions/${executionId}`, "_blank");
        }
    };

    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent
                side="right"
                className="w-full sm:max-w-xl md:max-w-2xl overflow-y-auto"
            >
                <SheetHeader>
                    <div className="flex items-center justify-between pr-8">
                        <SheetTitle className="text-lg">
                            Execution Details
                        </SheetTitle>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleOpenInNewTab}
                            disabled={!executionId}
                        >
                            <ExternalLink className="h-4 w-4 mr-2" />
                            Open in new tab
                        </Button>
                    </div>
                    <SheetDescription>
                        View workflow execution details and logs
                    </SheetDescription>
                </SheetHeader>

                <div className="mt-6 space-y-6">
                    {/* Loading State */}
                    {isLoadingExecution && (
                        <div className="space-y-4">
                            <Skeleton className="h-8 w-48" />
                            <Skeleton className="h-6 w-24" />
                            <div className="grid grid-cols-2 gap-4">
                                <Skeleton className="h-16" />
                                <Skeleton className="h-16" />
                            </div>
                            <Skeleton className="h-40" />
                        </div>
                    )}

                    {/* Error State */}
                    {executionError && (
                        <div className="flex items-center gap-2 p-4 bg-destructive/10 text-destructive rounded-lg">
                            <AlertCircle className="h-5 w-5 flex-shrink-0" />
                            <span>
                                Failed to load execution details. Please try again.
                            </span>
                        </div>
                    )}

                    {/* Execution Details */}
                    {execution && !isLoadingExecution && (
                        <>
                            {/* Workflow Name & Status */}
                            <div className="space-y-2">
                                <h3 className="text-xl font-semibold">
                                    {execution.workflow_name}
                                </h3>
                                <ExecutionStatusBadge status={execution.status} />
                            </div>

                            {/* Metadata Grid */}
                            <div className="grid grid-cols-2 gap-4 p-4 bg-muted/50 rounded-lg">
                                {/* Executed By */}
                                <div className="space-y-1">
                                    <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                                        <User className="h-4 w-4" />
                                        Executed by
                                    </div>
                                    <p className="font-medium">
                                        {execution.executed_by_name || "Unknown"}
                                    </p>
                                </div>

                                {/* Organization */}
                                <div className="space-y-1">
                                    <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                                        <Building2 className="h-4 w-4" />
                                        Organization
                                    </div>
                                    <p className="font-medium">
                                        {execution.org_name || "Global"}
                                    </p>
                                </div>

                                {/* Start Time */}
                                <div className="space-y-1">
                                    <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                                        <Clock className="h-4 w-4" />
                                        Started
                                    </div>
                                    <p className="font-medium">
                                        {execution.started_at
                                            ? formatDate(execution.started_at)
                                            : "Not started"}
                                    </p>
                                </div>

                                {/* Duration */}
                                <div className="space-y-1">
                                    <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                                        <Clock className="h-4 w-4" />
                                        Duration
                                    </div>
                                    <p className="font-medium">
                                        {execution.duration_ms != null
                                            ? formatDuration(execution.duration_ms)
                                            : "In progress..."}
                                    </p>
                                </div>
                            </div>

                            {/* Error Message */}
                            {execution.error_message && (
                                <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-lg">
                                    <div className="flex items-start gap-2">
                                        <AlertCircle className="h-5 w-5 text-destructive flex-shrink-0 mt-0.5" />
                                        <div className="space-y-1">
                                            <p className="font-medium text-destructive">
                                                Error
                                            </p>
                                            <pre className="text-sm whitespace-pre-wrap font-mono text-destructive/90">
                                                {execution.error_message}
                                            </pre>
                                        </div>
                                    </div>
                                </div>
                            )}

                            {/* Result Panel */}
                            {execution.result != null && (
                                <ExecutionResultPanel
                                    result={execution.result}
                                    resultType={execution.result_type}
                                    workflowName={execution.workflow_name}
                                    isLoading={false}
                                />
                            )}

                            {/* Logs Panel */}
                            <ExecutionLogsPanel
                                logs={logs ?? []}
                                status={execution.status}
                                isLoading={isLoadingLogs}
                                isPlatformAdmin={isPlatformAdmin}
                                maxHeight="400px"
                            />
                        </>
                    )}
                </div>
            </SheetContent>
        </Sheet>
    );
}
