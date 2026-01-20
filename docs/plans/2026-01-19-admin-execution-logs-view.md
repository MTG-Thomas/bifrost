# Admin Execution Logs View - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an admin-only "Logs" view mode to the Execution History page that shows individual log entries across all executions with server-side filtering, pagination, and a side drawer for viewing full execution details.

**Architecture:** New `GET /api/executions/logs` endpoint returns paginated log entries joined with execution and organization data. Frontend adds a toggle switch to switch between Executions and Logs views, reusing existing filter components. Clicking a log row opens a Sheet drawer containing the execution result view.

**Tech Stack:** FastAPI, SQLAlchemy, React, shadcn/ui Sheet, openapi-react-query

---

## Task 1: Backend - Add Pydantic Models for Logs List

**Files:**
- Modify: `api/src/models/contracts/executions.py`

**Step 1: Add LogEntry and LogsListResponse models**

Add after the existing `ExecutionLogPublic` model (around line 85):

```python
class LogListEntry(BaseModel):
    """Single log entry for the logs list view."""
    id: int
    execution_id: str
    organization_name: str | None
    workflow_name: str
    level: str
    message: str
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)


class LogsListResponse(BaseModel):
    """Response for paginated logs list."""
    logs: list[LogListEntry]
    continuation_token: str | None = None
```

**Step 2: Commit**

```bash
git add api/src/models/contracts/executions.py
git commit -m "feat(api): add LogListEntry and LogsListResponse models"
```

---

## Task 2: Backend - Add Repository Method for Logs List

**Files:**
- Modify: `api/src/repositories/execution_logs.py`

**Step 1: Write the failing test**

Create test file `api/tests/unit/repositories/test_execution_logs_list.py`:

```python
import pytest
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

from src.repositories.execution_logs import ExecutionLogsRepository


@pytest.mark.asyncio
async def test_list_logs_returns_paginated_results():
    """Test that list_logs returns logs with execution and org data."""
    # Arrange
    mock_session = AsyncMock()
    repo = ExecutionLogsRepository(mock_session)

    # Create mock log with joined data
    mock_log = MagicMock()
    mock_log.id = 1
    mock_log.execution_id = uuid4()
    mock_log.level = "ERROR"
    mock_log.message = "Connection failed"
    mock_log.timestamp = datetime.now(timezone.utc)
    mock_log.execution = MagicMock()
    mock_log.execution.workflow_name = "test-workflow"
    mock_log.execution.organization = MagicMock()
    mock_log.execution.organization.name = "Test Org"

    # Mock the query result - return limit+1 to indicate more pages
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_log, mock_log]
    mock_session.execute.return_value = mock_result

    # Act
    logs, next_token = await repo.list_logs(limit=1, offset=0)

    # Assert
    assert len(logs) == 1  # Should only return limit, not limit+1
    assert next_token == "1"  # Next offset
    assert logs[0]["workflow_name"] == "test-workflow"
    assert logs[0]["organization_name"] == "Test Org"
    assert logs[0]["level"] == "ERROR"
```

**Step 2: Run test to verify it fails**

```bash
./test.sh api/tests/unit/repositories/test_execution_logs_list.py -v
```

Expected: FAIL with `AttributeError: 'ExecutionLogsRepository' object has no attribute 'list_logs'`

**Step 3: Implement list_logs method**

Add to `api/src/repositories/execution_logs.py` after the existing methods:

```python
from sqlalchemy import func
from sqlalchemy.orm import selectinload, joinedload
from src.models.orm.executions import Execution
from src.models.orm.organizations import Organization


async def list_logs(
    self,
    organization_id: UUID | None = None,
    workflow_name: str | None = None,
    levels: list[str] | None = None,
    message_search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    List logs across all executions with filtering and pagination.

    Returns tuple of (logs_list, next_continuation_token).
    """
    # Build query with joins
    query = (
        select(ExecutionLog)
        .join(Execution, ExecutionLog.execution_id == Execution.id)
        .outerjoin(Organization, Execution.organization_id == Organization.id)
        .options(
            joinedload(ExecutionLog.execution).joinedload(Execution.organization)
        )
        .order_by(ExecutionLog.timestamp.desc())
    )

    # Apply filters
    if organization_id:
        query = query.where(Execution.organization_id == organization_id)

    if workflow_name:
        query = query.where(Execution.workflow_name.ilike(f"%{workflow_name}%"))

    if levels:
        query = query.where(ExecutionLog.level.in_([lvl.upper() for lvl in levels]))

    if message_search:
        query = query.where(ExecutionLog.message.ilike(f"%{message_search}%"))

    if start_date:
        query = query.where(ExecutionLog.timestamp >= start_date)

    if end_date:
        query = query.where(ExecutionLog.timestamp <= end_date)

    # Fetch limit+1 to check if there are more results
    query = query.offset(offset).limit(limit + 1)

    result = await self.session.execute(query)
    logs = result.scalars().unique().all()

    # Check if there are more results
    has_more = len(logs) > limit
    if has_more:
        logs = logs[:limit]

    # Calculate next token
    next_token = str(offset + limit) if has_more else None

    # Convert to dicts with joined data
    return [
        {
            "id": log.id,
            "execution_id": str(log.execution_id),
            "organization_name": log.execution.organization.name if log.execution.organization else None,
            "workflow_name": log.execution.workflow_name,
            "level": log.level,
            "message": log.message,
            "timestamp": log.timestamp,
        }
        for log in logs
    ], next_token
```

**Step 4: Run test to verify it passes**

```bash
./test.sh api/tests/unit/repositories/test_execution_logs_list.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add api/src/repositories/execution_logs.py api/tests/unit/repositories/test_execution_logs_list.py
git commit -m "feat(api): add list_logs repository method with filtering"
```

---

## Task 3: Backend - Add API Endpoint for Logs List

**Files:**
- Modify: `api/src/routers/executions.py`

**Step 1: Write the failing test**

Create test file `api/tests/integration/test_execution_logs_list_endpoint.py`:

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_logs_requires_admin(
    client: AsyncClient,
    regular_user_token: str,
):
    """Non-admin users should get 403."""
    response = await client.get(
        "/api/executions/logs",
        headers={"Authorization": f"Bearer {regular_user_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_logs_returns_paginated_results(
    client: AsyncClient,
    admin_token: str,
    sample_execution_with_logs,
):
    """Admin can list logs with pagination."""
    response = await client.get(
        "/api/executions/logs",
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"limit": 10},
    )
    assert response.status_code == 200
    data = response.json()
    assert "logs" in data
    assert isinstance(data["logs"], list)
    # continuation_token may or may not be present


@pytest.mark.asyncio
async def test_list_logs_filters_by_level(
    client: AsyncClient,
    admin_token: str,
    sample_execution_with_logs,
):
    """Can filter logs by level."""
    response = await client.get(
        "/api/executions/logs",
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"levels": "ERROR,WARNING"},
    )
    assert response.status_code == 200
    data = response.json()
    for log in data["logs"]:
        assert log["level"] in ["ERROR", "WARNING"]
```

**Step 2: Run test to verify it fails**

```bash
./test.sh api/tests/integration/test_execution_logs_list_endpoint.py -v
```

Expected: FAIL with 404 (endpoint doesn't exist)

**Step 3: Add the endpoint**

Add to `api/src/routers/executions.py` after the imports:

```python
from src.models.contracts.executions import LogsListResponse, LogListEntry
```

Add the endpoint after the existing `list_executions` endpoint (around line 520):

```python
@router.get(
    "/logs",
    response_model=LogsListResponse,
    summary="List execution logs (admin only)",
    description="List logs across all executions with filtering and pagination. Admin only.",
)
async def list_logs(
    ctx: Context,
    organization_id: UUID | None = Query(None, description="Filter by organization"),
    workflow_name: str | None = Query(None, description="Filter by workflow name (partial match)"),
    levels: str | None = Query(None, description="Comma-separated log levels (e.g., ERROR,WARNING)"),
    message_search: str | None = Query(None, description="Search in log message content"),
    start_date: str | None = Query(None, description="Filter logs after this date (ISO format)"),
    end_date: str | None = Query(None, description="Filter logs before this date (ISO format)"),
    limit: int = Query(50, ge=1, le=500, description="Number of logs per page"),
    continuation_token: str | None = Query(None, description="Pagination token"),
) -> LogsListResponse:
    """List logs across all executions (admin only)."""
    # Admin only
    if not ctx.user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")

    # Parse continuation token as offset
    offset = 0
    if continuation_token:
        try:
            offset = int(continuation_token)
        except ValueError:
            pass

    # Parse levels
    level_list = None
    if levels:
        level_list = [lvl.strip().upper() for lvl in levels.split(",")]

    # Parse dates
    parsed_start = None
    parsed_end = None
    if start_date:
        parsed_start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
    if end_date:
        parsed_end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))

    # Get logs from repository
    logs_repo = ExecutionLogsRepository(ctx.db)
    logs, next_token = await logs_repo.list_logs(
        organization_id=organization_id,
        workflow_name=workflow_name,
        levels=level_list,
        message_search=message_search,
        start_date=parsed_start,
        end_date=parsed_end,
        limit=limit,
        offset=offset,
    )

    return LogsListResponse(
        logs=[LogListEntry(**log) for log in logs],
        continuation_token=next_token,
    )
```

**Step 4: Run test to verify it passes**

```bash
./test.sh api/tests/integration/test_execution_logs_list_endpoint.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add api/src/routers/executions.py api/tests/integration/test_execution_logs_list_endpoint.py
git commit -m "feat(api): add GET /api/executions/logs endpoint for admin log search"
```

---

## Task 4: Backend - Type Check and Lint

**Step 1: Run pyright**

```bash
cd api && pyright
```

Expected: 0 errors

**Step 2: Run ruff**

```bash
cd api && ruff check .
```

Expected: 0 errors

**Step 3: Fix any issues found, then commit if changes were needed**

```bash
git add -A && git commit -m "fix(api): resolve type and lint issues"
```

---

## Task 5: Frontend - Regenerate TypeScript Types

**Step 1: Ensure dev stack is running**

```bash
docker ps --filter "name=bifrost" | grep -q "bifrost-dev-api" || ./debug.sh
```

**Step 2: Regenerate types**

```bash
cd client && npm run generate:types
```

**Step 3: Verify new types exist**

Check that `LogListEntry` and `LogsListResponse` appear in `client/src/lib/v1.d.ts`.

**Step 4: Commit**

```bash
git add client/src/lib/v1.d.ts
git commit -m "chore(client): regenerate types for logs list endpoint"
```

---

## Task 6: Frontend - Add useLogs Hook

**Files:**
- Create: `client/src/hooks/useLogs.ts`

**Step 1: Create the hook**

```typescript
import { $api } from "@/lib/api-client";

export interface LogFilters {
    organization_id?: string;
    workflow_name?: string;
    levels?: string; // Comma-separated: "ERROR,WARNING"
    message_search?: string;
    start_date?: string;
    end_date?: string;
}

export function useLogs(
    filters?: LogFilters,
    continuationToken?: string,
    enabled: boolean = true,
) {
    const queryParams: Record<string, string> = {};

    if (filters?.organization_id) {
        queryParams["organization_id"] = filters.organization_id;
    }
    if (filters?.workflow_name) {
        queryParams["workflow_name"] = filters.workflow_name;
    }
    if (filters?.levels) {
        queryParams["levels"] = filters.levels;
    }
    if (filters?.message_search) {
        queryParams["message_search"] = filters.message_search;
    }
    if (filters?.start_date) {
        queryParams["start_date"] = filters.start_date;
    }
    if (filters?.end_date) {
        queryParams["end_date"] = filters.end_date;
    }
    if (continuationToken) {
        queryParams["continuation_token"] = continuationToken;
    }

    return $api.useQuery(
        "get",
        "/api/executions/logs",
        { params: { query: queryParams } },
        { enabled },
    );
}
```

**Step 2: Commit**

```bash
git add client/src/hooks/useLogs.ts
git commit -m "feat(client): add useLogs hook for admin logs list"
```

---

## Task 7: Frontend - Add LogsTable Component

**Files:**
- Create: `client/src/pages/ExecutionHistory/components/LogsTable.tsx`

**Step 1: Create the component**

```tsx
import { useState } from "react";
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
import { Button } from "@/components/ui/button";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

type LogListEntry = components["schemas"]["LogListEntry"];

interface LogsTableProps {
    logs: LogListEntry[];
    isLoading: boolean;
    continuationToken?: string | null;
    onNextPage: () => void;
    onPrevPage: () => void;
    canGoBack: boolean;
    onLogClick: (log: LogListEntry) => void;
}

function getLevelBadgeVariant(level: string): "default" | "secondary" | "destructive" | "outline" {
    switch (level.toUpperCase()) {
        case "ERROR":
        case "CRITICAL":
            return "destructive";
        case "WARNING":
            return "secondary";
        case "DEBUG":
            return "outline";
        default:
            return "default";
    }
}

function getLevelColor(level: string): string {
    switch (level.toUpperCase()) {
        case "ERROR":
        case "CRITICAL":
            return "text-red-600 bg-red-50";
        case "WARNING":
            return "text-yellow-600 bg-yellow-50";
        case "DEBUG":
            return "text-gray-500 bg-gray-50";
        case "INFO":
            return "text-blue-600 bg-blue-50";
        default:
            return "text-gray-600 bg-gray-50";
    }
}

export function LogsTable({
    logs,
    isLoading,
    continuationToken,
    onNextPage,
    onPrevPage,
    canGoBack,
    onLogClick,
}: LogsTableProps) {
    const formatTimestamp = (timestamp: string) => {
        const date = new Date(timestamp);
        return date.toLocaleString();
    };

    return (
        <DataTable>
            <DataTableHeader>
                <DataTableRow>
                    <DataTableHead className="w-[150px]">Organization</DataTableHead>
                    <DataTableHead className="w-[180px]">Workflow</DataTableHead>
                    <DataTableHead className="w-[100px]">Level</DataTableHead>
                    <DataTableHead>Message</DataTableHead>
                    <DataTableHead className="w-[180px]">Timestamp</DataTableHead>
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
                        <DataTableCell colSpan={5} className="text-center py-8 text-muted-foreground">
                            No logs found matching your filters.
                        </DataTableCell>
                    </DataTableRow>
                ) : (
                    logs.map((log) => (
                        <DataTableRow
                            key={log.id}
                            clickable
                            onClick={() => onLogClick(log)}
                            className="cursor-pointer hover:bg-muted/50"
                        >
                            <DataTableCell className="font-medium">
                                {log.organization_name || "—"}
                            </DataTableCell>
                            <DataTableCell>{log.workflow_name}</DataTableCell>
                            <DataTableCell>
                                <Badge
                                    variant="outline"
                                    className={cn("font-mono text-xs", getLevelColor(log.level))}
                                >
                                    {log.level}
                                </Badge>
                            </DataTableCell>
                            <DataTableCell className="whitespace-normal break-words">
                                {log.message}
                            </DataTableCell>
                            <DataTableCell className="text-muted-foreground text-sm">
                                {formatTimestamp(log.timestamp)}
                            </DataTableCell>
                        </DataTableRow>
                    ))
                )}
            </DataTableBody>
            <DataTableFooter>
                <DataTableRow>
                    <DataTableCell colSpan={5}>
                        <div className="flex items-center justify-between">
                            <span className="text-sm text-muted-foreground">
                                {logs.length} logs shown
                            </span>
                            <div className="flex gap-2">
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={onPrevPage}
                                    disabled={!canGoBack}
                                >
                                    <ChevronLeft className="h-4 w-4 mr-1" />
                                    Previous
                                </Button>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={onNextPage}
                                    disabled={!continuationToken}
                                >
                                    Next
                                    <ChevronRight className="h-4 w-4 ml-1" />
                                </Button>
                            </div>
                        </div>
                    </DataTableCell>
                </DataTableRow>
            </DataTableFooter>
        </DataTable>
    );
}
```

**Step 2: Commit**

```bash
git add client/src/pages/ExecutionHistory/components/LogsTable.tsx
git commit -m "feat(client): add LogsTable component for admin logs view"
```

---

## Task 8: Frontend - Add ExecutionDrawer Component

**Files:**
- Create: `client/src/pages/ExecutionHistory/components/ExecutionDrawer.tsx`

**Step 1: Create the drawer component**

```tsx
import {
    Sheet,
    SheetContent,
    SheetHeader,
    SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { ExternalLink, X } from "lucide-react";
import { useExecution, useExecutionLogs } from "@/hooks/useExecutions";
import { ExecutionLogsPanel } from "@/components/execution/ExecutionLogsPanel";
import { ExecutionResultDisplay } from "@/components/execution/ExecutionResultDisplay";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { useAuth } from "@/hooks/useAuth";

interface ExecutionDrawerProps {
    executionId: string | null;
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

export function ExecutionDrawer({
    executionId,
    open,
    onOpenChange,
}: ExecutionDrawerProps) {
    const { user } = useAuth();
    const isPlatformAdmin = user?.is_superuser ?? false;

    const { data: execution, isLoading: isLoadingExecution } = useExecution(
        executionId ?? "",
        true, // disable polling
    );

    const { data: logsData, isLoading: isLoadingLogs } = useExecutionLogs(
        executionId ?? "",
        !!executionId && open,
    );

    const getStatusColor = (status: string) => {
        switch (status) {
            case "Success":
                return "bg-green-100 text-green-800";
            case "Failed":
                return "bg-red-100 text-red-800";
            case "Running":
                return "bg-blue-100 text-blue-800";
            case "Pending":
                return "bg-yellow-100 text-yellow-800";
            default:
                return "bg-gray-100 text-gray-800";
        }
    };

    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent
                side="right"
                className="w-full sm:w-[600px] md:w-[700px] lg:w-[800px] xl:w-[900px] sm:max-w-none overflow-y-auto"
            >
                <SheetHeader className="flex flex-row items-center justify-between pr-8">
                    <div className="flex items-center gap-3">
                        <SheetTitle>Execution Details</SheetTitle>
                        {execution && (
                            <Badge className={getStatusColor(execution.status)}>
                                {execution.status}
                            </Badge>
                        )}
                    </div>
                    {executionId && (
                        <Button
                            variant="ghost"
                            size="sm"
                            asChild
                            className="text-muted-foreground"
                        >
                            <a
                                href={`/executions/${executionId}`}
                                target="_blank"
                                rel="noopener noreferrer"
                            >
                                <ExternalLink className="h-4 w-4 mr-1" />
                                Open in new tab
                            </a>
                        </Button>
                    )}
                </SheetHeader>

                <div className="mt-6 space-y-6">
                    {isLoadingExecution ? (
                        <div className="space-y-4">
                            <Skeleton className="h-8 w-48" />
                            <Skeleton className="h-24 w-full" />
                            <Skeleton className="h-48 w-full" />
                        </div>
                    ) : execution ? (
                        <>
                            {/* Execution Info */}
                            <div className="space-y-2">
                                <h3 className="font-semibold">Workflow</h3>
                                <p className="text-lg">{execution.workflow_name}</p>
                            </div>

                            <div className="grid grid-cols-2 gap-4 text-sm">
                                <div>
                                    <span className="text-muted-foreground">Executed by:</span>
                                    <p>{execution.executed_by_name}</p>
                                </div>
                                <div>
                                    <span className="text-muted-foreground">Organization:</span>
                                    <p>{execution.org_name || "—"}</p>
                                </div>
                                <div>
                                    <span className="text-muted-foreground">Started:</span>
                                    <p>
                                        {execution.started_at
                                            ? new Date(execution.started_at).toLocaleString()
                                            : "—"}
                                    </p>
                                </div>
                                <div>
                                    <span className="text-muted-foreground">Duration:</span>
                                    <p>
                                        {execution.duration_ms
                                            ? `${(execution.duration_ms / 1000).toFixed(2)}s`
                                            : "—"}
                                    </p>
                                </div>
                            </div>

                            {/* Result */}
                            {execution.result && (
                                <div className="space-y-2">
                                    <h3 className="font-semibold">Result</h3>
                                    <ExecutionResultDisplay
                                        result={execution.result}
                                        resultType={execution.result_type}
                                    />
                                </div>
                            )}

                            {/* Error */}
                            {execution.error_message && (
                                <div className="space-y-2">
                                    <h3 className="font-semibold text-red-600">Error</h3>
                                    <pre className="bg-red-50 p-4 rounded-md text-sm text-red-800 whitespace-pre-wrap overflow-x-auto">
                                        {execution.error_message}
                                    </pre>
                                </div>
                            )}

                            {/* Logs */}
                            <div className="space-y-2">
                                <h3 className="font-semibold">Logs</h3>
                                <ExecutionLogsPanel
                                    logs={logsData?.logs}
                                    isLoading={isLoadingLogs}
                                    isPlatformAdmin={isPlatformAdmin}
                                    embedded
                                    maxHeight="400px"
                                />
                            </div>
                        </>
                    ) : (
                        <p className="text-muted-foreground">Execution not found.</p>
                    )}
                </div>
            </SheetContent>
        </Sheet>
    );
}
```

**Step 2: Commit**

```bash
git add client/src/pages/ExecutionHistory/components/ExecutionDrawer.tsx
git commit -m "feat(client): add ExecutionDrawer component for viewing execution details"
```

---

## Task 9: Frontend - Add LogsView Component

**Files:**
- Create: `client/src/pages/ExecutionHistory/components/LogsView.tsx`

**Step 1: Create the logs view component with filters**

```tsx
import { useState, useCallback } from "react";
import { useLogs, type LogFilters } from "@/hooks/useLogs";
import { LogsTable } from "./LogsTable";
import { ExecutionDrawer } from "./ExecutionDrawer";
import { SearchBox } from "@/components/ui/search-box";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import { OrganizationSelect } from "@/components/ui/organization-select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuCheckboxItem,
    DropdownMenuContent,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Filter } from "lucide-react";
import type { components } from "@/lib/v1";
import type { DateRange } from "react-day-picker";

type LogListEntry = components["schemas"]["LogListEntry"];

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

export function LogsView() {
    // Filter state
    const [filterOrgId, setFilterOrgId] = useState<string | undefined>();
    const [workflowName, setWorkflowName] = useState("");
    const [selectedLevels, setSelectedLevels] = useState<string[]>([]);
    const [messageSearch, setMessageSearch] = useState("");
    const [dateRange, setDateRange] = useState<DateRange | undefined>();

    // Pagination state
    const [currentToken, setCurrentToken] = useState<string | undefined>();
    const [pageStack, setPageStack] = useState<string[]>([]);

    // Drawer state
    const [selectedExecutionId, setSelectedExecutionId] = useState<string | null>(null);
    const [drawerOpen, setDrawerOpen] = useState(false);

    // Build filters
    const filters: LogFilters = {
        organization_id: filterOrgId,
        workflow_name: workflowName || undefined,
        levels: selectedLevels.length > 0 ? selectedLevels.join(",") : undefined,
        message_search: messageSearch || undefined,
        start_date: dateRange?.from?.toISOString(),
        end_date: dateRange?.to?.toISOString(),
    };

    const { data, isLoading } = useLogs(filters, currentToken);

    // Reset pagination when filters change
    const resetPagination = useCallback(() => {
        setCurrentToken(undefined);
        setPageStack([]);
    }, []);

    const handleFilterOrgChange = (value: string | undefined) => {
        setFilterOrgId(value);
        resetPagination();
    };

    const handleWorkflowChange = (value: string) => {
        setWorkflowName(value);
        resetPagination();
    };

    const handleLevelToggle = (level: string) => {
        setSelectedLevels((prev) =>
            prev.includes(level)
                ? prev.filter((l) => l !== level)
                : [...prev, level]
        );
        resetPagination();
    };

    const handleMessageSearchChange = (value: string) => {
        setMessageSearch(value);
        resetPagination();
    };

    const handleDateRangeChange = (range: DateRange | undefined) => {
        setDateRange(range);
        resetPagination();
    };

    const handleNextPage = () => {
        if (data?.continuation_token) {
            setPageStack((prev) => [...prev, currentToken || ""]);
            setCurrentToken(data.continuation_token);
        }
    };

    const handlePrevPage = () => {
        if (pageStack.length > 0) {
            const newStack = [...pageStack];
            const prevToken = newStack.pop();
            setPageStack(newStack);
            setCurrentToken(prevToken || undefined);
        }
    };

    const handleLogClick = (log: LogListEntry) => {
        setSelectedExecutionId(log.execution_id);
        setDrawerOpen(true);
    };

    return (
        <div className="space-y-4">
            {/* Filters */}
            <div className="flex flex-wrap items-center gap-4">
                <div className="w-64">
                    <OrganizationSelect
                        value={filterOrgId}
                        onChange={handleFilterOrgChange}
                        showAll={true}
                        showGlobal={true}
                        placeholder="All organizations"
                    />
                </div>

                <div className="w-48">
                    <Input
                        placeholder="Workflow name..."
                        value={workflowName}
                        onChange={(e) => handleWorkflowChange(e.target.value)}
                    />
                </div>

                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button variant="outline" size="sm">
                            <Filter className="h-4 w-4 mr-2" />
                            Level
                            {selectedLevels.length > 0 && ` (${selectedLevels.length})`}
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                        {LOG_LEVELS.map((level) => (
                            <DropdownMenuCheckboxItem
                                key={level}
                                checked={selectedLevels.includes(level)}
                                onCheckedChange={() => handleLevelToggle(level)}
                            >
                                {level}
                            </DropdownMenuCheckboxItem>
                        ))}
                    </DropdownMenuContent>
                </DropdownMenu>

                <DateRangePicker
                    dateRange={dateRange}
                    onDateRangeChange={handleDateRangeChange}
                />

                <div className="flex-1 max-w-md">
                    <SearchBox
                        value={messageSearch}
                        onChange={handleMessageSearchChange}
                        placeholder="Search log messages..."
                    />
                </div>
            </div>

            {/* Table */}
            <LogsTable
                logs={data?.logs ?? []}
                isLoading={isLoading}
                continuationToken={data?.continuation_token}
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
```

**Step 2: Commit**

```bash
git add client/src/pages/ExecutionHistory/components/LogsView.tsx
git commit -m "feat(client): add LogsView component with filters and pagination"
```

---

## Task 10: Frontend - Integrate Logs View Toggle into ExecutionHistory Page

**Files:**
- Modify: `client/src/pages/ExecutionHistory.tsx`

**Step 1: Add imports**

Add near the top of the file with other imports:

```tsx
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { LogsView } from "./ExecutionHistory/components/LogsView";
```

**Step 2: Add view mode state**

Add inside the component, with other state declarations:

```tsx
const [viewMode, setViewMode] = useState<"executions" | "logs">("executions");
```

**Step 3: Add toggle switch in the action bar**

Find the action bar area (around line 530, where the filters are). Add the toggle switch at the beginning of the flex container, visible only to platform admins:

```tsx
{isPlatformAdmin && (
    <div className="flex items-center gap-2">
        <Switch
            id="view-mode"
            checked={viewMode === "logs"}
            onCheckedChange={(checked) =>
                setViewMode(checked ? "logs" : "executions")
            }
        />
        <Label
            htmlFor="view-mode"
            className="text-sm font-normal cursor-pointer whitespace-nowrap"
        >
            Logs View
        </Label>
    </div>
)}
```

**Step 4: Conditionally render LogsView or existing executions view**

Wrap the existing content (Tabs and everything inside) in a conditional:

```tsx
{viewMode === "logs" ? (
    <LogsView />
) : (
    // ... existing Tabs and executions content
)}
```

**Step 5: Commit**

```bash
git add client/src/pages/ExecutionHistory.tsx
git commit -m "feat(client): add logs view toggle to ExecutionHistory page"
```

---

## Task 11: Frontend - Type Check and Lint

**Step 1: Run TypeScript check**

```bash
cd client && npm run tsc
```

Expected: 0 errors

**Step 2: Run lint**

```bash
cd client && npm run lint
```

Expected: 0 errors

**Step 3: Fix any issues found, then commit if changes were needed**

```bash
git add -A && git commit -m "fix(client): resolve type and lint issues"
```

---

## Task 12: Create Component Directory Structure

**Note:** If the directory `client/src/pages/ExecutionHistory/` doesn't exist, create it and move the components there. The main page file stays at `client/src/pages/ExecutionHistory.tsx`.

If needed:

```bash
mkdir -p client/src/pages/ExecutionHistory/components
```

---

## Task 13: Manual Testing Checklist

1. **Start dev stack:** `./debug.sh`
2. **Login as platform admin**
3. **Navigate to Execution History page**
4. **Verify toggle switch is visible** in action bar
5. **Toggle to Logs view:**
   - Verify logs table loads with correct columns
   - Verify org, workflow, level, message filters work
   - Verify date range filter works
   - Verify pagination (Next/Previous) works
6. **Click a log row:**
   - Verify drawer opens from right
   - Verify execution details display correctly
   - Verify logs panel shows in drawer
   - Verify "Open in new tab" link works
7. **Close drawer:**
   - Verify pagination state is preserved
   - Verify scroll position is maintained
8. **Test as non-admin:**
   - Verify toggle switch is NOT visible
   - Verify direct API call to `/api/executions/logs` returns 403

---

## Task 14: Final Commit and Cleanup

**Step 1: Run full test suite**

```bash
./test.sh
```

Expected: All tests pass

**Step 2: Final verification**

```bash
cd api && pyright && ruff check .
cd ../client && npm run tsc && npm run lint
```

**Step 3: Squash or organize commits if needed, then push**

```bash
git log --oneline -10  # Review commits
git push origin <branch-name>
```
