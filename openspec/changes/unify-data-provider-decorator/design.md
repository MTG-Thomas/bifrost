# Design: Consolidate Executables into Unified Workflows Table

## Architecture Overview

This design consolidates the `data_providers` table into the `workflows` table using a `type` discriminator field. All executable user code (workflows, tools, data providers) shares the same table and base metadata class.

## Database Schema

### Before
```sql
-- Two separate tables
CREATE TABLE workflows (
    id UUID PRIMARY KEY,
    name VARCHAR,
    is_tool BOOLEAN DEFAULT FALSE,
    -- ... other columns
);

CREATE TABLE data_providers (
    id UUID PRIMARY KEY,
    name VARCHAR,
    -- ... limited columns
);

-- FormField FK to data_providers
ALTER TABLE form_fields ADD CONSTRAINT fk_data_provider
    FOREIGN KEY (data_provider_id) REFERENCES data_providers(id);
```

### After
```sql
-- Single unified table
CREATE TABLE workflows (
    id UUID PRIMARY KEY,
    name VARCHAR,
    type VARCHAR(20) DEFAULT 'workflow',  -- NEW: 'workflow', 'tool', 'data_provider'
    cache_ttl_seconds INTEGER DEFAULT 300, -- NEW: for data providers
    -- is_tool column DROPPED (replaced by type='tool')
    -- ... other columns
);

-- FormField FK now references workflows
ALTER TABLE form_fields ADD CONSTRAINT fk_data_provider
    FOREIGN KEY (data_provider_id) REFERENCES workflows(id);
```

### Migration Script

```python
# api/alembic/versions/YYYYMMDD_consolidate_data_providers.py

def upgrade():
    # 1. Add new columns to workflows
    op.add_column('workflows', sa.Column('type', sa.String(20), server_default='workflow'))
    op.add_column('workflows', sa.Column('cache_ttl_seconds', sa.Integer(), server_default='300'))

    # 2. Migrate is_tool to type
    op.execute("UPDATE workflows SET type = 'tool' WHERE is_tool = true")
    op.execute("UPDATE workflows SET type = 'workflow' WHERE is_tool = false OR is_tool IS NULL")

    # 3. Copy data_providers into workflows
    op.execute("""
        INSERT INTO workflows (
            id, org_id, name, function_name, description, file_path, module_path,
            is_active, last_seen_at, type, cache_ttl_seconds, category,
            parameters_schema, tags, created_at, updated_at
        )
        SELECT
            id, org_id, name, function_name, description, file_path, module_path,
            is_active, last_seen_at, 'data_provider', 300, 'General',
            '[]'::jsonb, '[]'::jsonb, created_at, updated_at
        FROM data_providers
    """)

    # 4. Update FormField FK
    op.drop_constraint('form_fields_data_provider_id_fkey', 'form_fields', type_='foreignkey')
    op.create_foreign_key(
        'form_fields_data_provider_id_fkey', 'form_fields', 'workflows',
        ['data_provider_id'], ['id'], ondelete='SET NULL'
    )

    # 5. Create index on type for performance
    op.create_index('ix_workflows_type', 'workflows', ['type'])

    # 6. Drop is_tool column
    op.drop_index('ix_workflows_is_tool', 'workflows')
    op.drop_column('workflows', 'is_tool')

def downgrade():
    # Reverse migration (complex, may need manual intervention)
    pass
```

## Class Hierarchy

### Runtime Metadata (dataclasses in module_loader.py)

```python
@dataclass
class ExecutableMetadata:
    """Base metadata for all executable user code."""
    id: str | None = None
    name: str = ""
    description: str = ""
    category: str = "General"
    tags: list[str] = field(default_factory=list)
    timeout_seconds: int = 1800
    parameters: list[WorkflowParameter] = field(default_factory=list)
    source_file_path: str | None = None
    function: Callable | None = None
    type: Literal["workflow", "tool", "data_provider"] = "workflow"

@dataclass
class WorkflowMetadata(ExecutableMetadata):
    """Workflow-specific metadata."""
    execution_mode: Literal["sync", "async"] = "async"
    retry_policy: dict | None = None
    schedule: str | None = None
    endpoint_enabled: bool = False
    allowed_methods: list[str] = field(default_factory=lambda: ["POST"])
    disable_global_key: bool = False
    public_endpoint: bool = False
    tool_description: str | None = None  # Used when type='tool'
    time_saved: int = 0
    value: float = 0.0

@dataclass
class DataProviderMetadata(ExecutableMetadata):
    """Data provider-specific metadata."""
    timeout_seconds: int = 300  # Override default
    cache_ttl_seconds: int = 300
    type: Literal["data_provider"] = "data_provider"
```

### API Models (Pydantic in contracts/)

```python
# api/src/models/contracts/workflows.py

class ExecutableType(str, Enum):
    WORKFLOW = "workflow"
    TOOL = "tool"
    DATA_PROVIDER = "data_provider"

class WorkflowMetadata(BaseModel):
    """Unified API response model for all executable types."""
    id: str | None = None
    name: str
    description: str | None = None
    category: str = "General"
    tags: list[str] = Field(default_factory=list)
    timeout_seconds: int = 1800
    parameters: list[WorkflowParameter] = Field(default_factory=list)
    source_file_path: str | None = None
    relative_file_path: str | None = None

    # Type discriminator
    type: ExecutableType = ExecutableType.WORKFLOW

    # Workflow/Tool specific
    execution_mode: Literal["sync", "async"] = "sync"
    retry_policy: RetryPolicy | None = None
    schedule: str | None = None
    endpoint_enabled: bool = False
    allowed_methods: list[str] = Field(default_factory=lambda: ["POST"])
    disable_global_key: bool = False
    public_endpoint: bool = False
    tool_description: str | None = None
    time_saved: int = 0
    value: float = 0.0

    # Data provider specific
    cache_ttl_seconds: int = 300
```

## ORM Model

```python
# api/src/models/orm/workflows.py

class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(255))
    function_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100), default="General")
    file_path: Mapped[str] = mapped_column(String(500))
    module_path: Mapped[str | None] = mapped_column(String(500))

    # Type discriminator (replaces is_tool)
    type: Mapped[str] = mapped_column(String(20), default="workflow", index=True)

    # Workflow-specific
    schedule: Mapped[str | None] = mapped_column(String(100))
    parameters_schema: Mapped[list] = mapped_column(JSONB, default=[])
    tags: Mapped[list] = mapped_column(JSONB, default=[])
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    endpoint_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_methods: Mapped[list] = mapped_column(JSONB, default=["POST"])
    execution_mode: Mapped[str] = mapped_column(String(10), default="async")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    tool_description: Mapped[str | None] = mapped_column(Text)
    time_saved: Mapped[int] = mapped_column(Integer, default=0)
    value: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)

    # Data provider specific
    cache_ttl_seconds: Mapped[int] = mapped_column(Integer, default=300)

    # API key fields (workflow-specific)
    api_key_hash: Mapped[str | None] = mapped_column(String(255))
    # ... other api_key fields

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, onupdate=datetime.utcnow)

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="workflows")
    agents: Mapped[list["Agent"]] = relationship(secondary=agent_tools)

# DataProvider class REMOVED - no longer needed
```

## Repository Updates

```python
# api/src/repositories/workflows.py

class WorkflowRepository:
    async def get_by_type(
        self,
        type: str,
        active_only: bool = True
    ) -> Sequence[Workflow]:
        """Get workflows filtered by type."""
        query = select(Workflow).where(Workflow.type == type)
        if active_only:
            query = query.where(Workflow.is_active == True)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_data_providers(self) -> Sequence[Workflow]:
        """Get all active data providers."""
        return await self.get_by_type("data_provider")

    async def get_tools(self) -> Sequence[Workflow]:
        """Get all active tools."""
        return await self.get_by_type("tool")

# api/src/repositories/data_providers.py
# Now queries workflows table with type filter

class DataProviderRepository:
    async def get_by_name(self, name: str) -> Workflow | None:
        query = select(Workflow).where(
            Workflow.name == name,
            Workflow.type == "data_provider",
            Workflow.is_active == True
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_all_active(self) -> Sequence[Workflow]:
        query = select(Workflow).where(
            Workflow.type == "data_provider",
            Workflow.is_active == True
        )
        result = await self.db.execute(query)
        return result.scalars().all()
```

## Decorator Implementation

```python
# api/src/sdk/decorators.py

def _create_executable_metadata(
    func: Callable,
    *,
    type: Literal["workflow", "tool", "data_provider"],
    id: str | None,
    name: str | None,
    description: str | None,
    category: str,
    tags: list[str] | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Extract common metadata from decorated function."""
    return {
        "id": id,
        "name": name or func.__name__,
        "description": description or (func.__doc__ or "").strip(),
        "category": category,
        "tags": tags or [],
        "timeout_seconds": timeout_seconds,
        "parameters": extract_parameters_from_signature(func),
        "function": func,
        "type": type,
    }

def workflow(
    _func: Callable | None = None,
    *,
    id: str | None = None,
    name: str | None = None,
    description: str | None = None,
    category: str = "General",
    tags: list[str] | None = None,
    timeout_seconds: int = 1800,
    execution_mode: Literal["sync", "async"] = "async",
    # ... other workflow fields
):
    def decorator(func: Callable) -> Callable:
        base = _create_executable_metadata(
            func, type="workflow", id=id, name=name, description=description,
            category=category, tags=tags, timeout_seconds=timeout_seconds,
        )
        metadata = WorkflowMetadata(**base, execution_mode=execution_mode, ...)
        func._workflow_metadata = metadata
        return func
    # ...

def tool(
    _func: Callable | None = None,
    *,
    # Same signature as workflow
):
    """Decorator for AI agent tools. Sets type='tool'."""
    def decorator(func: Callable) -> Callable:
        base = _create_executable_metadata(
            func, type="tool", ...
        )
        metadata = WorkflowMetadata(**base, ...)
        func._workflow_metadata = metadata
        return func
    # ...

def data_provider(
    _func: Callable | None = None,
    *,
    id: str | None = None,
    name: str | None = None,
    description: str | None = None,
    category: str = "General",
    tags: list[str] | None = None,
    timeout_seconds: int = 300,
    cache_ttl_seconds: int = 300,
):
    def decorator(func: Callable) -> Callable:
        base = _create_executable_metadata(
            func, type="data_provider", id=id, name=name, description=description,
            category=category, tags=tags, timeout_seconds=timeout_seconds,
        )
        metadata = DataProviderMetadata(**base, cache_ttl_seconds=cache_ttl_seconds)
        func._data_provider_metadata = metadata
        return func
    # ...
```

## Router Updates

### Data Provider Router

```python
# api/src/routers/data_providers.py

@router.get("")
async def list_data_providers(ctx: Context):
    """List all data providers (from workflows table with type filter)."""
    async with get_db_context() as db:
        query = select(Workflow).where(
            Workflow.type == "data_provider",
            Workflow.is_active == True,
            Workflow.org_id == ctx.org_id
        )
        result = await db.execute(query)
        providers = result.scalars().all()

    return [_convert_workflow_to_data_provider_schema(p) for p in providers]

@router.post("/{provider_id}/invoke")
async def invoke_data_provider(provider_id: UUID, request: DataProviderInvokeRequest, ctx: Context):
    """Invoke a data provider (queries workflows table)."""
    async with get_db_context() as db:
        provider = await db.get(Workflow, provider_id)
        if not provider or provider.type != "data_provider":
            raise HTTPException(404, "Data provider not found")

    # ... rest of invoke logic unchanged
```

### Workflows Router

```python
# api/src/routers/workflows.py

@router.get("")
async def list_workflows(
    ctx: Context,
    type: str | None = None,  # NEW: filter by type
):
    """List workflows, optionally filtered by type."""
    query = select(Workflow).where(
        Workflow.is_active == True,
        Workflow.org_id == ctx.org_id
    )
    if type:
        query = query.where(Workflow.type == type)

    # ... execute and return

@router.post("/execute")
async def execute_workflow(request: WorkflowExecutionRequest, ctx: Context):
    """Execute any type of executable (workflow, tool, or data_provider)."""
    workflow = await db.get(Workflow, request.workflow_id)

    if workflow.type == "data_provider":
        # Execute as data provider, normalize result to options format
        result = await run_data_provider(context, workflow.name, request.input_data)
        return WorkflowExecutionResponse(
            result=result,
            result_type="data_provider"
        )
    else:
        # Execute as workflow/tool
        result = await run_workflow(context, workflow.name, request.input_data)
        return WorkflowExecutionResponse(
            result=result,
            result_type=workflow.type
        )
```

## MCP Tool

```python
# api/src/services/mcp/server.py

async def _execute_workflow_impl(
    context: MCPContext,
    workflow_id: str,
    params: dict[str, Any] | None = None,
) -> str:
    """Execute any executable type and return formatted result."""
    workflow = await db.get(Workflow, workflow_id)

    if workflow.type == "data_provider":
        result = await run_data_provider(context, workflow.name, params)
        return json.dumps({
            "type": "data_provider",
            "name": workflow.name,
            "option_count": len(result),
            "options": result[:20],
            "truncated": len(result) > 20,
        }, indent=2)
    else:
        result = await run_workflow(context, workflow.name, params)
        return json.dumps({
            "type": workflow.type,
            "name": workflow.name,
            "result": result,
        }, indent=2)
```

## Testing Strategy

1. **Migration tests**: Verify data migrates correctly
2. **Repository tests**: Query by type works
3. **Decorator tests**: Type field set correctly
4. **Router tests**: Both endpoints work with unified table
5. **E2E tests**: Forms with data provider fields still work
6. **MCP tests**: Execute tool handles all types
