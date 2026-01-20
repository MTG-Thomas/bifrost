/**
 * LogsView Component
 *
 * Displays logs table with logs-specific filters.
 * Receives shared filters (org, dateRange, searchTerm) from parent ExecutionHistory.
 */

import { useState, useMemo, useCallback } from "react";
import type { DateRange } from "react-day-picker";

import { useLogs, type LogFilters, type LogListEntry } from "@/hooks/useLogs";
import { LogsTable } from "./LogsTable";
import { ExecutionDrawer } from "./ExecutionDrawer";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
type LogLevel = (typeof LOG_LEVELS)[number];

interface LogsViewProps {
    /** Organization ID filter from parent */
    filterOrgId?: string | null;
    /** Date range filter from parent */
    dateRange?: DateRange;
    /** Search term from parent (used for workflow name search) */
    searchTerm?: string;
}

export function LogsView({
    filterOrgId,
    dateRange,
    searchTerm,
}: LogsViewProps) {
    // Logs-specific filter state
    const [selectedLevels, setSelectedLevels] = useState<LogLevel[]>([]);
    const [messageSearch, setMessageSearch] = useState("");

    // Pagination state
    const [currentToken, setCurrentToken] = useState<string | undefined>();
    const [pageStack, setPageStack] = useState<string[]>([]);

    // Drawer state
    const [selectedExecutionId, setSelectedExecutionId] = useState<
        string | null
    >(null);
    const [drawerOpen, setDrawerOpen] = useState(false);

    // Build filters object combining parent and local filters
    const filters: LogFilters = useMemo(() => {
        const result: LogFilters = {};

        // From parent
        if (filterOrgId !== undefined) {
            result.organization_id = filterOrgId ?? undefined;
        }

        if (searchTerm?.trim()) {
            result.workflow_name = searchTerm.trim();
        }

        if (dateRange?.from) {
            const startDate = new Date(dateRange.from);
            startDate.setHours(0, 0, 0, 0);
            result.start_date = startDate.toISOString();

            const endDate = new Date(dateRange.to || dateRange.from);
            endDate.setHours(23, 59, 59, 999);
            result.end_date = endDate.toISOString();
        }

        // Local logs-specific filters
        if (selectedLevels.length > 0) {
            result.levels = selectedLevels.join(",");
        }

        if (messageSearch.trim()) {
            result.message_search = messageSearch.trim();
        }

        return result;
    }, [filterOrgId, searchTerm, dateRange, selectedLevels, messageSearch]);

    // Fetch logs
    const { data, isLoading } = useLogs(filters, currentToken);

    const logs = data?.logs ?? [];
    const continuationToken = data?.continuation_token;

    // Reset pagination when filters change
    const resetPagination = useCallback(() => {
        setPageStack([]);
        setCurrentToken(undefined);
    }, []);

    // Level toggle handler
    const toggleLevel = useCallback(
        (level: LogLevel) => {
            setSelectedLevels((prev) =>
                prev.includes(level)
                    ? prev.filter((l) => l !== level)
                    : [...prev, level],
            );
            resetPagination();
        },
        [resetPagination],
    );

    // Pagination handlers
    const handleNextPage = () => {
        if (continuationToken) {
            setPageStack((prev) => [...prev, currentToken || ""]);
            setCurrentToken(continuationToken);
        }
    };

    const handlePrevPage = () => {
        if (pageStack.length > 0) {
            const newStack = [...pageStack];
            const previousToken = newStack.pop();
            setPageStack(newStack);
            setCurrentToken(previousToken || undefined);
        }
    };

    // Log click handler
    const handleLogClick = (log: LogListEntry) => {
        setSelectedExecutionId(log.execution_id);
        setDrawerOpen(true);
    };

    // Handle message search change
    const handleMessageSearchChange = useCallback(
        (value: string) => {
            setMessageSearch(value);
            resetPagination();
        },
        [resetPagination],
    );

    return (
        <div className="flex flex-col flex-1 min-h-0 space-y-4">
            {/* Logs-specific filters: Level buttons + Message search */}
            <div className="flex items-center gap-4">
                {/* Level filter buttons - similar to status tabs */}
                <div className="flex items-center gap-1 p-1 bg-muted rounded-lg">
                    <Button
                        variant={selectedLevels.length === 0 ? "secondary" : "ghost"}
                        size="sm"
                        onClick={() => {
                            setSelectedLevels([]);
                            resetPagination();
                        }}
                        className="h-8 px-3"
                    >
                        All
                    </Button>
                    {LOG_LEVELS.map((level) => (
                        <Button
                            key={level}
                            variant={selectedLevels.includes(level) ? "secondary" : "ghost"}
                            size="sm"
                            onClick={() => toggleLevel(level)}
                            className="h-8 px-3"
                        >
                            {level}
                        </Button>
                    ))}
                </div>

                {/* Message search */}
                <Input
                    placeholder="Search in log messages..."
                    value={messageSearch}
                    onChange={(e) => handleMessageSearchChange(e.target.value)}
                    className="max-w-xs"
                />
            </div>

            {/* Table */}
            <div className="flex-1 min-h-0 overflow-auto">
                <LogsTable
                    logs={logs}
                    isLoading={isLoading}
                    continuationToken={continuationToken}
                    onNextPage={handleNextPage}
                    onPrevPage={handlePrevPage}
                    canGoBack={pageStack.length > 0}
                    onLogClick={handleLogClick}
                />
            </div>

            {/* Execution Drawer */}
            <ExecutionDrawer
                executionId={selectedExecutionId}
                open={drawerOpen}
                onOpenChange={setDrawerOpen}
            />
        </div>
    );
}
