/**
 * LogsView Component
 *
 * Main orchestrating component for the admin logs view.
 * Combines filters, the logs table, and the execution drawer.
 */

import { useState, useEffect, useMemo } from "react";
import { ChevronDown, Filter, X } from "lucide-react";
import type { DateRange } from "react-day-picker";

import { useLogs, type LogFilters, type LogListEntry } from "@/hooks/useLogs";
import { LogsTable } from "./LogsTable";
import { ExecutionDrawer } from "./ExecutionDrawer";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import { SearchBox } from "@/components/search/SearchBox";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { Badge } from "@/components/ui/badge";

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
type LogLevel = (typeof LOG_LEVELS)[number];

export function LogsView() {
    // Filter state
    const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
        undefined,
    );
    const [workflowName, setWorkflowName] = useState("");
    const [selectedLevels, setSelectedLevels] = useState<LogLevel[]>([]);
    const [messageSearch, setMessageSearch] = useState("");
    const [dateRange, setDateRange] = useState<DateRange | undefined>();

    // Pagination state
    const [currentToken, setCurrentToken] = useState<string | undefined>();
    const [pageStack, setPageStack] = useState<string[]>([]);

    // Drawer state
    const [selectedExecutionId, setSelectedExecutionId] = useState<
        string | null
    >(null);
    const [drawerOpen, setDrawerOpen] = useState(false);

    // Build filters object
    const filters: LogFilters = useMemo(() => {
        const result: LogFilters = {};

        if (filterOrgId !== undefined) {
            // When undefined, show all orgs. When null or string, use the value.
            result.organization_id = filterOrgId ?? undefined;
        }

        if (workflowName.trim()) {
            result.workflow_name = workflowName.trim();
        }

        if (selectedLevels.length > 0) {
            result.levels = selectedLevels.join(",");
        }

        if (messageSearch.trim()) {
            result.message_search = messageSearch.trim();
        }

        if (dateRange?.from) {
            // Set start to beginning of day
            const startDate = new Date(dateRange.from);
            startDate.setHours(0, 0, 0, 0);
            result.start_date = startDate.toISOString();

            // Set end to end of day (use from date if no end date)
            const endDate = new Date(dateRange.to || dateRange.from);
            endDate.setHours(23, 59, 59, 999);
            result.end_date = endDate.toISOString();
        }

        return result;
    }, [filterOrgId, workflowName, selectedLevels, messageSearch, dateRange]);

    // Fetch logs
    const { data, isLoading } = useLogs(filters, currentToken);

    const logs = data?.logs ?? [];
    const continuationToken = data?.continuation_token;

    // Reset pagination when filters change
    useEffect(() => {
        setPageStack([]);
        setCurrentToken(undefined);
    }, [filterOrgId, workflowName, selectedLevels, messageSearch, dateRange]);

    // Pagination handlers
    const handleNextPage = () => {
        if (continuationToken) {
            // Push current token to stack for "back" navigation
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

    // Log click handler - opens drawer with execution details
    const handleLogClick = (log: LogListEntry) => {
        setSelectedExecutionId(log.execution_id);
        setDrawerOpen(true);
    };

    // Level toggle handler
    const toggleLevel = (level: LogLevel) => {
        setSelectedLevels((prev) =>
            prev.includes(level)
                ? prev.filter((l) => l !== level)
                : [...prev, level],
        );
    };

    // Clear all levels
    const clearLevels = () => {
        setSelectedLevels([]);
    };

    // Get display text for level filter button
    const levelFilterText = useMemo(() => {
        if (selectedLevels.length === 0) return "All levels";
        if (selectedLevels.length === 1) return selectedLevels[0];
        return `${selectedLevels.length} levels`;
    }, [selectedLevels]);

    return (
        <div className="space-y-4">
            {/* Filters row */}
            <div className="flex flex-wrap items-center gap-4">
                {/* Organization select */}
                <div className="w-56">
                    <OrganizationSelect
                        value={filterOrgId}
                        onChange={setFilterOrgId}
                        showAll={true}
                        showGlobal={true}
                        placeholder="All organizations"
                    />
                </div>

                {/* Workflow name input */}
                <div className="w-48">
                    <Input
                        placeholder="Workflow name..."
                        value={workflowName}
                        onChange={(e) => setWorkflowName(e.target.value)}
                    />
                </div>

                {/* Level dropdown with checkboxes */}
                <Popover>
                    <PopoverTrigger asChild>
                        <Button
                            variant="outline"
                            className="w-40 justify-between"
                        >
                            <div className="flex items-center gap-2">
                                <Filter className="h-4 w-4" />
                                <span>{levelFilterText}</span>
                            </div>
                            <ChevronDown className="h-4 w-4 opacity-50" />
                        </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-56 p-3" align="start">
                        <div className="space-y-3">
                            <div className="flex items-center justify-between">
                                <span className="text-sm font-medium">
                                    Log Levels
                                </span>
                                {selectedLevels.length > 0 && (
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={clearLevels}
                                        className="h-6 px-2 text-xs"
                                    >
                                        Clear
                                    </Button>
                                )}
                            </div>
                            <div className="space-y-2">
                                {LOG_LEVELS.map((level) => (
                                    <div
                                        key={level}
                                        className="flex items-center gap-2"
                                    >
                                        <Checkbox
                                            id={`level-${level}`}
                                            checked={selectedLevels.includes(
                                                level,
                                            )}
                                            onCheckedChange={() =>
                                                toggleLevel(level)
                                            }
                                        />
                                        <Label
                                            htmlFor={`level-${level}`}
                                            className="text-sm font-normal cursor-pointer flex-1"
                                        >
                                            {level}
                                        </Label>
                                        <LevelBadge level={level} />
                                    </div>
                                ))}
                            </div>
                        </div>
                    </PopoverContent>
                </Popover>

                {/* Date range picker */}
                <DateRangePicker
                    dateRange={dateRange}
                    onDateRangeChange={setDateRange}
                />

                {/* Message search box */}
                <SearchBox
                    value={messageSearch}
                    onChange={setMessageSearch}
                    placeholder="Search messages..."
                    className="w-64"
                />
            </div>

            {/* Active filters display */}
            {(selectedLevels.length > 0 ||
                workflowName ||
                messageSearch ||
                dateRange?.from ||
                filterOrgId !== undefined) && (
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm text-muted-foreground">
                        Active filters:
                    </span>
                    {selectedLevels.map((level) => (
                        <Badge
                            key={level}
                            variant="secondary"
                            className="gap-1"
                        >
                            {level}
                            <button
                                onClick={() => toggleLevel(level)}
                                className="ml-1 hover:text-destructive"
                            >
                                <X className="h-3 w-3" />
                            </button>
                        </Badge>
                    ))}
                    {workflowName && (
                        <Badge variant="secondary" className="gap-1">
                            Workflow: {workflowName}
                            <button
                                onClick={() => setWorkflowName("")}
                                className="ml-1 hover:text-destructive"
                            >
                                <X className="h-3 w-3" />
                            </button>
                        </Badge>
                    )}
                    {messageSearch && (
                        <Badge variant="secondary" className="gap-1">
                            Message: {messageSearch}
                            <button
                                onClick={() => setMessageSearch("")}
                                className="ml-1 hover:text-destructive"
                            >
                                <X className="h-3 w-3" />
                            </button>
                        </Badge>
                    )}
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                            setSelectedLevels([]);
                            setWorkflowName("");
                            setMessageSearch("");
                            setDateRange(undefined);
                            setFilterOrgId(undefined);
                        }}
                        className="h-6 px-2 text-xs text-muted-foreground"
                    >
                        Clear all
                    </Button>
                </div>
            )}

            {/* Table */}
            <LogsTable
                logs={logs}
                isLoading={isLoading}
                continuationToken={continuationToken}
                onNextPage={handleNextPage}
                onPrevPage={handlePrevPage}
                canGoBack={pageStack.length > 0}
                onLogClick={handleLogClick}
            />

            {/* Execution Drawer */}
            <ExecutionDrawer
                executionId={selectedExecutionId}
                open={drawerOpen}
                onOpenChange={setDrawerOpen}
            />
        </div>
    );
}

/**
 * Helper component to show colored badge for log level
 */
function LevelBadge({ level }: { level: LogLevel }) {
    const variant = getLevelVariant(level);
    return (
        <Badge variant={variant} className="text-xs px-1.5 py-0">
            {level.charAt(0)}
        </Badge>
    );
}

function getLevelVariant(
    level: LogLevel,
): "default" | "secondary" | "destructive" | "outline" | "warning" {
    switch (level) {
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
