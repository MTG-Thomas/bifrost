import {
    DataTable,
    DataTableBody,
    DataTableCell,
    DataTableFooter,
    DataTableHead,
    DataTableHeader,
    DataTableRow,
} from "@/components/ui/data-table";
import { Badge } from "@/components/ui/badge";
import {
    Pagination,
    PaginationContent,
    PaginationItem,
    PaginationLink,
    PaginationNext,
    PaginationPrevious,
} from "@/components/ui/pagination";
import type { components } from "@/lib/v1";
import { formatDate } from "@/lib/utils";

type LogListEntry = components["schemas"]["LogListEntry"];

interface LogsTableProps {
    logs: LogListEntry[];
    isLoading: boolean;
    continuationToken?: string | null;
    onNextPage: () => void;
    onPrevPage: () => void;
    canGoBack: boolean;
    currentPage: number;
    onLogClick: (log: LogListEntry) => void;
}

function getLevelBadgeVariant(
    level: string,
): "default" | "secondary" | "destructive" | "outline" | "warning" {
    switch (level.toUpperCase()) {
        case "ERROR":
        case "CRITICAL":
            return "destructive";
        case "WARNING":
            return "warning";
        case "DEBUG":
            return "outline";
        default:
            return "default";
    }
}

export function LogsTable({
    logs,
    isLoading,
    continuationToken,
    onNextPage,
    onPrevPage,
    canGoBack,
    currentPage,
    onLogClick,
}: LogsTableProps) {
    return (
        <DataTable>
            <DataTableHeader>
                <DataTableRow>
                    <DataTableHead className="w-[150px]">
                        Organization
                    </DataTableHead>
                    <DataTableHead className="w-[180px]">
                        Workflow
                    </DataTableHead>
                    <DataTableHead className="w-[100px]">Level</DataTableHead>
                    <DataTableHead>Message</DataTableHead>
                    <DataTableHead className="w-[180px]">
                        Timestamp
                    </DataTableHead>
                </DataTableRow>
            </DataTableHeader>
            <DataTableBody>
                {isLoading ? (
                    <DataTableRow>
                        <DataTableCell colSpan={5} className="text-center py-8">
                            Loading logs...
                        </DataTableCell>
                    </DataTableRow>
                ) : logs.length === 0 ? (
                    <DataTableRow>
                        <DataTableCell
                            colSpan={5}
                            className="text-center py-8 text-muted-foreground"
                        >
                            No logs found matching your filters.
                        </DataTableCell>
                    </DataTableRow>
                ) : (
                    logs.map((log) => (
                        <DataTableRow
                            key={log.id}
                            clickable
                            href={`/history/${log.execution_id}`}
                            onClick={() => onLogClick(log)}
                            className="cursor-pointer"
                        >
                            <DataTableCell className="font-medium">
                                {log.organization_name || "\u2014"}
                            </DataTableCell>
                            <DataTableCell>{log.workflow_name}</DataTableCell>
                            <DataTableCell>
                                <Badge
                                    variant={getLevelBadgeVariant(log.level)}
                                    className="font-mono text-xs uppercase"
                                >
                                    {log.level}
                                </Badge>
                            </DataTableCell>
                            <DataTableCell className="max-w-md truncate">
                                {log.message}
                            </DataTableCell>
                            <DataTableCell className="text-muted-foreground text-sm">
                                {formatDate(log.timestamp)}
                            </DataTableCell>
                        </DataTableRow>
                    ))
                )}
            </DataTableBody>
            <DataTableFooter>
                <DataTableRow>
                    <DataTableCell colSpan={5} className="p-0">
                        <div className="px-6 py-4 flex items-center justify-center">
                            <Pagination>
                                <PaginationContent>
                                    <PaginationItem>
                                        <PaginationPrevious
                                            onClick={(e) => {
                                                e.preventDefault();
                                                onPrevPage();
                                            }}
                                            className={
                                                !canGoBack
                                                    ? "pointer-events-none opacity-50"
                                                    : "cursor-pointer"
                                            }
                                            aria-disabled={!canGoBack}
                                        />
                                    </PaginationItem>
                                    <PaginationItem>
                                        <PaginationLink isActive>
                                            {currentPage}
                                        </PaginationLink>
                                    </PaginationItem>
                                    <PaginationItem>
                                        <PaginationNext
                                            onClick={(e) => {
                                                e.preventDefault();
                                                onNextPage();
                                            }}
                                            className={
                                                !continuationToken
                                                    ? "pointer-events-none opacity-50"
                                                    : "cursor-pointer"
                                            }
                                            aria-disabled={!continuationToken}
                                        />
                                    </PaginationItem>
                                </PaginationContent>
                            </Pagination>
                        </div>
                    </DataTableCell>
                </DataTableRow>
            </DataTableFooter>
        </DataTable>
    );
}
