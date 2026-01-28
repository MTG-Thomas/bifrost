# Per-Agent Model Override Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow agents to override the global LLM model, max_tokens, and temperature while using the platform's API key.

**Architecture:** Add three nullable columns to agents table. When an agent has override values set, pass them to the LLM client's `stream()` and `complete()` methods which already support per-call overrides. Agents cannot switch providers (must use same provider as global config).

**Tech Stack:** Python/FastAPI, SQLAlchemy, Alembic, TypeScript/React, React Hook Form, Zod

---

## Task 1: Create Database Migration

**Files:**
- Create: `api/alembic/versions/20260126_add_llm_override_to_agents.py`

**Step 1: Create migration file**

```bash
cd /Users/jack/GitHub/bifrost/api && alembic revision -m "add_llm_override_to_agents"
```

**Step 2: Edit the migration file**

```python
"""add_llm_override_to_agents

Revision ID: [auto-generated]
Revises: [auto-generated]
Create Date: [auto-generated]
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "[auto-generated]"
down_revision = "[auto-generated]"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('llm_model', sa.String(100), nullable=True))
    op.add_column('agents', sa.Column('llm_max_tokens', sa.Integer(), nullable=True))
    op.add_column('agents', sa.Column('llm_temperature', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('agents', 'llm_temperature')
    op.drop_column('agents', 'llm_max_tokens')
    op.drop_column('agents', 'llm_model')
```

**Step 3: Commit**

```bash
git add api/alembic/versions/20260126_add_llm_override_to_agents.py
git commit -m "feat: add migration for agent LLM overrides"
```

---

## Task 2: Update Agent ORM Model

**Files:**
- Modify: `api/src/models/orm/agents.py`

**Step 1: Add Float import**

At line 11, add `Float` to the imports:

```python
from sqlalchemy import Boolean, DateTime, Enum as SQLAlchemyEnum, Float, ForeignKey, Index, Integer, String, Text, text
```

**Step 2: Add LLM override columns**

After line 56 (after `system_tools`), add:

```python
    # LLM configuration overrides (null = use global config)
    llm_model: Mapped[str | None] = mapped_column(String(100), default=None)
    llm_max_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    llm_temperature: Mapped[float | None] = mapped_column(Float, default=None)
```

**Step 3: Run pyright to verify**

```bash
cd /Users/jack/GitHub/bifrost/api && pyright src/models/orm/agents.py
```

Expected: 0 errors

**Step 4: Commit**

```bash
git add api/src/models/orm/agents.py
git commit -m "feat: add LLM override fields to Agent ORM model"
```

---

## Task 3: Update Agent Contracts

**Files:**
- Modify: `api/src/models/contracts/agents.py`

**Step 1: Add fields to AgentCreate**

After line 51 (after `system_tools`), add:

```python
    llm_model: str | None = Field(default=None, description="Override model (null=use global config)")
    llm_max_tokens: int | None = Field(default=None, ge=1, le=200000, description="Override max tokens")
    llm_temperature: float | None = Field(default=None, ge=0.0, le=2.0, description="Override temperature")
```

**Step 2: Add fields to AgentUpdate**

After line 70 (after `clear_roles`), add:

```python
    llm_model: str | None = Field(default=None, description="Override model (null=use global config)")
    llm_max_tokens: int | None = Field(default=None, ge=1, le=200000, description="Override max tokens")
    llm_temperature: float | None = Field(default=None, ge=0.0, le=2.0, description="Override temperature")
```

**Step 3: Add fields to AgentPublic**

After line 94 (after `system_tools`), add:

```python
    llm_model: str | None = None
    llm_max_tokens: int | None = None
    llm_temperature: float | None = None
```

**Step 4: Add fields to AgentSummary**

After line 116 (after `created_at`), add:

```python
    llm_model: str | None = None
```

**Step 5: Run pyright to verify**

```bash
cd /Users/jack/GitHub/bifrost/api && pyright src/models/contracts/agents.py
```

Expected: 0 errors

**Step 6: Commit**

```bash
git add api/src/models/contracts/agents.py
git commit -m "feat: add LLM override fields to agent contracts"
```

---

## Task 4: Update Agent Router

**Files:**
- Modify: `api/src/routers/agents.py`

**Step 1: Update _agent_to_public function**

Around line 109, in the `_agent_to_public` function, add the new fields after `system_tools`:

```python
        system_tools=agent.system_tools or [],
        llm_model=agent.llm_model,
        llm_max_tokens=agent.llm_max_tokens,
        llm_temperature=agent.llm_temperature,
```

**Step 2: Update create_agent endpoint**

Find the create_agent endpoint (around line 200) and in the Agent creation, add:

```python
        llm_model=body.llm_model,
        llm_max_tokens=body.llm_max_tokens,
        llm_temperature=body.llm_temperature,
```

**Step 3: Update update_agent endpoint**

Find the update_agent endpoint and add update logic:

```python
    if body.llm_model is not None:
        agent.llm_model = body.llm_model if body.llm_model else None
    if body.llm_max_tokens is not None:
        agent.llm_max_tokens = body.llm_max_tokens if body.llm_max_tokens else None
    if body.llm_temperature is not None:
        agent.llm_temperature = body.llm_temperature if body.llm_temperature else None
```

**Step 4: Run pyright to verify**

```bash
cd /Users/jack/GitHub/bifrost/api && pyright src/routers/agents.py
```

Expected: 0 errors

**Step 5: Commit**

```bash
git add api/src/routers/agents.py
git commit -m "feat: add LLM override handling in agent router"
```

---

## Task 5: Update Agent Executor

**Files:**
- Modify: `api/src/services/agent_executor.py`

**Step 1: Find the stream call location**

Around line 271, locate where `llm_client.stream()` is called.

**Step 2: Add override extraction before the while loop**

Before line 262 (`while iteration < MAX_TOOL_ITERATIONS:`), add:

```python
            # Extract agent LLM overrides
            model_override = agent.llm_model if agent else None
            max_tokens_override = agent.llm_max_tokens if agent else None
            temperature_override = agent.llm_temperature if agent else None
```

**Step 3: Pass overrides to stream call**

Update the stream call around line 271:

```python
                async for chunk in llm_client.stream(
                    messages=messages,
                    tools=tool_definitions if tool_definitions else None,
                    model=model_override,
                    max_tokens=max_tokens_override,
                    temperature=temperature_override,
                ):
```

**Step 4: Find delegation complete call**

Search for the `llm_client.complete()` call used for delegation (around line 1069). Update it similarly:

```python
                    delegate_model = delegate_agent.llm_model
                    delegate_max_tokens = delegate_agent.llm_max_tokens
                    delegate_temp = delegate_agent.llm_temperature

                    response = await llm_client.complete(
                        messages=delegate_messages,
                        tools=delegation_tools,
                        model=delegate_model,
                        max_tokens=delegate_max_tokens,
                        temperature=delegate_temp,
                    )
```

**Step 5: Run pyright to verify**

```bash
cd /Users/jack/GitHub/bifrost/api && pyright src/services/agent_executor.py
```

Expected: 0 errors

**Step 6: Commit**

```bash
git add api/src/services/agent_executor.py
git commit -m "feat: apply agent LLM overrides in executor"
```

---

## Task 6: Update MCP Agent Tools

**Files:**
- Modify: `api/src/services/mcp_server/tools/agents.py`

**Step 1: Update list_agents output**

Find `list_agents` function and add the new field to the return dict:

```python
            "llm_model": agent.llm_model,
```

**Step 2: Update get_agent output**

Find `get_agent` function and add:

```python
            "llm_model": agent.llm_model,
            "llm_max_tokens": agent.llm_max_tokens,
            "llm_temperature": agent.llm_temperature,
```

**Step 3: Update create_agent input schema and function**

Add to the schema:

```python
            "llm_model": {
                "type": "string",
                "description": "Override model (leave empty for global config)",
            },
            "llm_max_tokens": {
                "type": "integer",
                "description": "Override max tokens (leave empty for global config)",
            },
            "llm_temperature": {
                "type": "number",
                "description": "Override temperature 0.0-2.0 (leave empty for global config)",
            },
```

Add to function signature and agent creation.

**Step 4: Update update_agent similarly**

**Step 5: Run pyright to verify**

```bash
cd /Users/jack/GitHub/bifrost/api && pyright src/services/mcp_server/tools/agents.py
```

Expected: 0 errors

**Step 6: Commit**

```bash
git add api/src/services/mcp_server/tools/agents.py
git commit -m "feat: add LLM override fields to MCP agent tools"
```

---

## Task 7: Regenerate Frontend Types

**Step 1: Ensure API is running**

```bash
docker ps --filter "name=bifrost" | grep bifrost-dev-api
```

**Step 2: Regenerate types**

```bash
cd /Users/jack/GitHub/bifrost/client && npm run generate:types
```

**Step 3: Verify new types exist**

```bash
grep -A5 "llm_model" /Users/jack/GitHub/bifrost/client/src/lib/v1.d.ts | head -20
```

Expected: See `llm_model`, `llm_max_tokens`, `llm_temperature` in AgentCreate, AgentUpdate, AgentPublic schemas

**Step 4: Commit**

```bash
git add client/src/lib/v1.d.ts
git commit -m "chore: regenerate types with agent LLM overrides"
```

---

## Task 8: Add useLLMModels Hook

**Files:**
- Modify: `client/src/hooks/useLLMConfig.ts`

**Step 1: Add hook for fetching models**

Add after line 49:

```typescript
/**
 * Hook to fetch available models from the configured LLM provider
 */
export function useLLMModels() {
	const { isPlatformAdmin, isLoading: permissionsLoading } =
		useUserPermissions();

	const {
		data,
		isLoading: modelsLoading,
		error,
	} = $api.useQuery("get", "/api/admin/llm/models", undefined, {
		enabled: isPlatformAdmin && !permissionsLoading,
		staleTime: 10 * 60 * 1000, // Cache for 10 minutes
		retry: false,
	});

	return {
		models: data?.models ?? [],
		provider: data?.provider,
		isLoading: permissionsLoading || modelsLoading,
		error,
	};
}
```

**Step 2: Verify TypeScript compiles**

```bash
cd /Users/jack/GitHub/bifrost/client && npm run tsc
```

Expected: 0 errors

**Step 3: Commit**

```bash
git add client/src/hooks/useLLMConfig.ts
git commit -m "feat: add useLLMModels hook"
```

---

## Task 9: Update AgentDialog Form Schema

**Files:**
- Modify: `client/src/components/agents/AgentDialog.tsx`

**Step 1: Add Slider import**

At imports section, add:

```typescript
import { Slider } from "@/components/ui/slider";
```

**Step 2: Import useLLMModels**

After line 65, add:

```typescript
import { useLLMModels } from "@/hooks/useLLMConfig";
```

**Step 3: Update form schema**

After line 112 (after `knowledge_sources`), add:

```typescript
	llm_model: z.string().nullable(),
	llm_max_tokens: z.number().min(1).max(200000).nullable(),
	llm_temperature: z.number().min(0).max(2).nullable(),
```

**Step 4: Update FormValues type usage**

The type is inferred from schema, no changes needed.

**Step 5: Update defaultValues**

Add after line 159 (after `knowledge_sources`):

```typescript
			llm_model: null,
			llm_max_tokens: null,
			llm_temperature: null,
```

**Step 6: Add hook call**

After line 131 (after useRoles), add:

```typescript
	const { models: availableModels, isLoading: modelsLoading } = useLLMModels();
```

**Step 7: Update form.reset for editing**

After line 197 (in the editing useEffect), add:

```typescript
				llm_model: agentWithOrg.llm_model ?? null,
				llm_max_tokens: agentWithOrg.llm_max_tokens ?? null,
				llm_temperature: agentWithOrg.llm_temperature ?? null,
```

**Step 8: Update form.reset for creating**

Add same fields in the create reset section.

**Step 9: Update submit body**

Add to bodyWithOrg:

```typescript
				llm_model: values.llm_model,
				llm_max_tokens: values.llm_max_tokens,
				llm_temperature: values.llm_temperature,
```

**Step 10: Commit partial progress**

```bash
git add client/src/components/agents/AgentDialog.tsx
git commit -m "feat: add LLM override fields to AgentDialog schema"
```

---

## Task 10: Add Model Settings UI Section

**Files:**
- Modify: `client/src/components/agents/AgentDialog.tsx`

**Step 1: Add state for collapsible section**

After line 139, add:

```typescript
	const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
```

**Step 2: Add Model Settings section after Knowledge Sources**

After the Knowledge Sources FormField (around line 1750), add:

```tsx
									{/* Model Settings - Collapsible */}
									<div className="border rounded-md">
										<button
											type="button"
											onClick={() => setModelSettingsOpen(!modelSettingsOpen)}
											className="w-full flex items-center justify-between p-4 text-left"
										>
											<div>
												<span className="font-medium">Model Settings</span>
												<span className="text-xs text-muted-foreground ml-2">
													(Optional)
												</span>
											</div>
											<ChevronsUpDown className={cn(
												"h-4 w-4 transition-transform",
												modelSettingsOpen && "rotate-180"
											)} />
										</button>

										{modelSettingsOpen && (
											<div className="px-4 pb-4 space-y-4 border-t pt-4">
												<p className="text-sm text-muted-foreground">
													Leave empty to use platform default settings.
												</p>

												{/* Model Select */}
												<FormField
													control={form.control}
													name="llm_model"
													render={({ field }) => (
														<FormItem>
															<FormLabel>Model</FormLabel>
															<Select
																onValueChange={(value) =>
																	field.onChange(value === "__default__" ? null : value)
																}
																value={field.value ?? "__default__"}
															>
																<FormControl>
																	<SelectTrigger>
																		<SelectValue placeholder="Use platform default" />
																	</SelectTrigger>
																</FormControl>
																<SelectContent>
																	<SelectItem value="__default__">
																		Use platform default
																	</SelectItem>
																	{availableModels.map((model) => (
																		<SelectItem key={model.id} value={model.id}>
																			{model.display_name}
																		</SelectItem>
																	))}
																</SelectContent>
															</Select>
															<FormMessage />
														</FormItem>
													)}
												/>

												{/* Max Tokens */}
												<FormField
													control={form.control}
													name="llm_max_tokens"
													render={({ field }) => (
														<FormItem>
															<FormLabel>Max Tokens</FormLabel>
															<FormControl>
																<Input
																	type="number"
																	placeholder="Use platform default"
																	value={field.value ?? ""}
																	onChange={(e) => {
																		const val = e.target.value;
																		field.onChange(val ? parseInt(val, 10) : null);
																	}}
																/>
															</FormControl>
															<FormDescription>
																Maximum response length (1-200,000)
															</FormDescription>
															<FormMessage />
														</FormItem>
													)}
												/>

												{/* Temperature */}
												<FormField
													control={form.control}
													name="llm_temperature"
													render={({ field }) => (
														<FormItem>
															<FormLabel>
																Temperature: {field.value?.toFixed(1) ?? "default"}
															</FormLabel>
															<FormControl>
																<div className="flex items-center gap-4">
																	<Slider
																		min={0}
																		max={2}
																		step={0.1}
																		value={[field.value ?? 0.7]}
																		onValueChange={([val]) => field.onChange(val)}
																		className="flex-1"
																	/>
																	<Button
																		type="button"
																		variant="ghost"
																		size="sm"
																		onClick={() => field.onChange(null)}
																	>
																		Reset
																	</Button>
																</div>
															</FormControl>
															<FormDescription>
																0 = deterministic, 2 = creative
															</FormDescription>
															<FormMessage />
														</FormItem>
													)}
												/>
											</div>
										)}
									</div>
```

**Step 3: Verify TypeScript compiles**

```bash
cd /Users/jack/GitHub/bifrost/client && npm run tsc
```

Expected: 0 errors

**Step 4: Verify lint passes**

```bash
cd /Users/jack/GitHub/bifrost/client && npm run lint
```

Expected: 0 errors

**Step 5: Commit**

```bash
git add client/src/components/agents/AgentDialog.tsx
git commit -m "feat: add Model Settings UI section to AgentDialog"
```

---

## Task 11: Run Full Verification

**Step 1: Backend verification**

```bash
cd /Users/jack/GitHub/bifrost/api && pyright
cd /Users/jack/GitHub/bifrost/api && ruff check .
```

Expected: 0 errors (1 pre-existing warning OK)

**Step 2: Frontend verification**

```bash
cd /Users/jack/GitHub/bifrost/client && npm run tsc
cd /Users/jack/GitHub/bifrost/client && npm run lint
```

Expected: 0 errors

**Step 3: Restart API to apply migration**

```bash
docker compose -f docker-compose.dev.yml restart api
```

**Step 4: Watch logs for migration**

```bash
docker compose -f docker-compose.dev.yml logs -f api | head -50
```

Expected: See migration applied successfully

---

## Verification

1. **Create agent without model override** - verify uses global config
2. **Create agent with model override** - verify chat uses specified model
3. **Edit agent to add model override** - verify change takes effect
4. **Edit agent to clear model override** - verify reverts to global
5. **Check AI usage tracking** - verify model name is correctly recorded per conversation
6. **Test delegation** - verify delegated agent uses its own model override if set
