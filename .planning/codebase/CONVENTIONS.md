# Coding Conventions

**Analysis Date:** 2026-01-30

## Python (Backend)

### Naming Patterns

**Files:**
- Module files use `snake_case`: `auth.py`, `user_provisioning.py`, `data_provider_cache.py`
- Router files follow pattern `{resource}_router.py` or just placed in `routers/` directory with resource name: `api/src/routers/auth.py`, `api/src/routers/users.py`
- Service files use `{service_name}_service.py`: `mfa_service.py`, `passkey_service.py`

**Classes and Functions:**
- Classes use `PascalCase`: `UserRepository`, `MFAService`, `DataProviderCache`
- Functions use `snake_case`: `create_access_token`, `verify_password`, `get_mfa_status`
- Constants use `UPPER_SNAKE_CASE`: `TTL_DEVICE_CODE`, `TTL_REFRESH_TOKEN`, `TEST_SECRET_KEY`

**Variables:**
- Local variables use `snake_case`: `user_id`, `mfa_token`, `access_token`
- Private attributes use leading underscore: `_generate_user_code`, `_internal_counter`

**Types:**
- Pydantic models use `PascalCase`: `UserCreate`, `LoginResponse`, `MFAVerifyRequest`
- Optional types use `|` syntax (Python 3.10+): `str | None`, `list[str] | None`

### Code Style

**Formatting:**
- Line length: Follows PEP 8 (79-100 character soft limit, pragmatic approach)
- Indentation: 4 spaces
- No tabs

**Linting:**
- Uses `pyright` for type checking with `basic` mode
- Type checking config: `api/pyrightconfig.json`
- Uses pytest for testing with async support

**Imports Organization:**
Order strictly:
1. Standard library: `import os`, `import sys`, `from datetime import datetime`
2. Third-party: `from fastapi import APIRouter`, `from sqlalchemy import select`
3. Local: `from src.core.auth import CurrentActiveUser`, `from src.models import User`

Imports are organized alphabetically within each group.

**Example from auth.py:**
```python
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr

from src.core.cache import get_shared_redis
from src.core.cache.keys import device_code_key, refresh_token_jti_key
from src.models import AuthStatusResponse, DeviceAuthorizeRequest
from src.config import get_settings
from src.core.auth import CurrentActiveUser
from src.repositories.users import UserRepository
```

### Docstrings

**Module-level docstrings:**
```python
"""
Authentication Router

Provides endpoints for user authentication:
- Login (JWT token generation with user provisioning)
- Token refresh with rotation
- Token revocation (logout, revoke-all)
- Current user info

Key Features:
- First user login auto-promotes to PlatformAdmin
- Subsequent users auto-join organizations by email domain
- JWT tokens include is_superuser, org_id, and roles
- Refresh tokens use JTI for revocation support
"""
```

**Function docstrings (Google style):**
```python
def create_test_jwt(
    user_id: str | None = None,
    email: str = "test@example.com",
    name: str = "Test User",
    is_superuser: bool = False,
    organization_id: str | None = None,
) -> str:
    """
    Create test JWT token for authentication.

    Uses the same secret key, issuer, and audience as the test environment
    configured in tests/conftest.py and src/config.py.

    Args:
        user_id: User OID (object ID) - typically a UUID
        email: User email address
        name: User display name
        is_superuser: Whether user should have superuser/platform admin privileges
        organization_id: Organization ID for ORG users

    Returns:
        str: JWT token signed with test secret

    Example:
        >>> token = create_test_jwt(email="john@acme.com", name="John Doe")
        >>> headers = auth_headers(token)
        >>> response = requests.get("/api/organizations", headers=headers)
    """
```

**Inline comments:**
```python
# Atomically check and delete (prevent race conditions)
result = await r.delete(key)

# For non-superusers, org_id is required by auth middleware
# Generate a default org_id if not provided
if not is_superuser and organization_id is None:
    organization_id = "00000000-0000-4000-8000-000000000100"
```

### Error Handling

**Pattern:** Raise `HTTPException` with appropriate status codes
```python
if not user:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )

if not user.is_active:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Account is inactive",
    )
```

**Business logic errors:** Use `ValueError` for validation, catch in handler
```python
try:
    result = await ensure_user_provisioned(db=db, email=user_data.email)
except ValueError as e:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(e),
    )
```

### Logging

**Framework:** Python's built-in `logging` module

**Pattern:** Get logger at module level
```python
logger = logging.getLogger(__name__)
```

**Usage:** Log significant events with context
```python
logger.info(
    f"User logged in: {user.email}",
    extra={
        "user_id": str(user.id),
        "is_superuser": user.is_superuser,
        "org_id": str(user.organization_id) if user.organization_id else None,
    }
)

logger.warning(
    f"Refresh token reuse attempt for user {user_id}, JTI {jti}"
)

logger.error(
    "Device code authorized but missing user_id",
    extra={"device_code": token_request.device_code[:8] + "..."}
)
```

### Pydantic Models

**Location:** All Pydantic models defined in central location (see architecture)

**Pattern:**
```python
class LoginResponse(BaseModel):
    """Unified login response that can be Token or MFA response."""
    # Token fields (when MFA not required or after MFA verification)
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    # MFA fields (when MFA required)
    mfa_required: bool = False
    mfa_setup_required: bool = False
    mfa_token: str | None = None
    available_methods: list[str] | None = None
    expires_in: int | None = None
```

**Validation:** Use Pydantic validators for complex logic
```python
from pydantic import BaseModel, EmailStr, field_validator

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v
```

### Function Design

**Size:** Keep functions focused and under 50-100 lines where possible

**Parameters:**
- Explicit positional parameters before optional parameters
- Use type hints on all parameters
- Document non-obvious parameters in docstring

**Return Values:**
- Always return typed values
- Use `None` explicitly, not implicit returns
- For async functions, return awaited values

**Example:**
```python
async def store_refresh_token_jti(user_id: str, jti: str) -> None:
    """
    Store a refresh token JTI in Redis for validation/revocation.

    Args:
        user_id: User ID the token belongs to
        jti: JWT ID to store
    """
    r = await get_shared_redis()
    key = refresh_token_jti_key(user_id, jti)
    await r.setex(key, TTL_REFRESH_TOKEN, "1")
```

### Module Organization

**Router structure:** One `APIRouter` per file with all related endpoints
```python
router = APIRouter(prefix="/auth", tags=["auth"])

# Related helper functions grouped together
def set_auth_cookies(response: Response, access_token: str, refresh_token: str):
    ...

def clear_auth_cookies(response: Response):
    ...

# Endpoints grouped by functionality
@router.post("/login", response_model=LoginResponse)
async def login(...):
    ...

@router.post("/refresh", response_model=Token)
async def refresh_token(...):
    ...
```

**Markers in code:** Use comments to separate sections
```python
# =============================================================================
# Cookie Configuration
# =============================================================================

def set_auth_cookies(...):
    ...

# =============================================================================
# Endpoints
# =============================================================================

@router.post("/login", response_model=LoginResponse)
async def login(...):
    ...
```

## TypeScript/React (Frontend)

### Naming Patterns

**Files:**
- React components: `PascalCase.tsx`: `PageLoader.tsx`, `WorkflowEditDialog.tsx`, `NavigationLink.tsx`
- Custom hooks: Prefix with `use`, camelCase: `useRoles.ts`, `useWorkflows.ts`, `useWorkflowKeys.ts`
- Utilities and libraries: `camelCase.ts`: `api-client.ts`, `utils.ts`, `app-code-compiler.ts`
- Types/interfaces: Defined in component or type files as needed

**Functions and Variables:**
- Functions: `camelCase`: `transformPath()`, `handleApiError()`, `authFetch()`
- Variables: `camelCase`: `userId`, `csrfToken`, `baseUrl`
- Constants: `UPPER_SNAKE_CASE`: `ACCESS_TOKEN_KEY`, `TOKEN_REFRESH_BUFFER_SECONDS`, `AUTH_ENDPOINTS`
- React component props: `PascalCase` for components, properties are `camelCase`

**Types:**
- Type/Interface names: `PascalCase`: `PageLoaderProps`, `HttpTriggerDialogProps`, `WorkflowParameter`
- Imported types from OpenAPI: Use direct import: `type { components } from "@/lib/v1"` then `components["schemas"]["WorkflowMetadata"]`

### Code Style

**Formatting:**
- Tabs (4 characters wide) - configured in `.prettierrc`
- Line length: Pragmatic, approximately 100 characters
- Semicolons: Required
- Single quotes in strings where applicable

**Prettier config (.prettierrc):**
```json
{
	"tabWidth": 4,
	"useTabs": true
}
```

**Linting:**
- ESLint with TypeScript support: `eslint.config.js`
- Plugin configs: `@typescript-eslint`, `react-hooks`, `react-refresh`
- Rules:
  - `no-console`: warn for `console.log`, allow `console.warn` and `console.error`
  - `@typescript-eslint/no-unused-vars`: error, but allow underscore-prefixed variables
  - `react-hooks/rules-of-hooks`: enforced from react-hooks plugin
  - `react-refresh/only-export-components`: off (disabled)

**TypeScript config:**
- Strict mode: Enabled (`"strict": true`)
- Path aliases: `@/*` maps to `./src/*`
- No implicit any, unused locals, unused parameters enforced

### Import Organization

**Order:**
1. Third-party React: `import React`, `import { useState }`, `from react-router-dom`
2. Third-party UI: `import { Button } from "@/components/ui/button"`
3. Third-party utilities: `import { toast } from "sonner"`
4. Local components: `import { PageLoader } from "@/components/loaders"`
5. Local hooks: `import { useRoles } from "@/hooks/useRoles"`
6. Local utilities/types: `import { cn } from "@/lib/utils"`, `import type { components }`
7. Icons last: `import { Loader2, Check } from "lucide-react"`

**Example from HttpTriggerDialog.tsx:**
```typescript
import { useState } from "react";
import { Copy, Check, Webhook, RefreshCw, AlertTriangle } from "lucide-react";
import { useNavigate } from "react-router-dom";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useWorkflowKeys, useCreateWorkflowKey } from "@/hooks/useWorkflowKeys";
import type { components } from "@/lib/v1";
```

### JSDoc/TSDoc Comments

**Component-level:**
```typescript
/**
 * WorkflowEditDialog Component
 *
 * Dialog for editing workflow settings including organization scope,
 * access level, and role assignments.
 * Platform admin only.
 */
export function WorkflowEditDialog() {
	// ...
}
```

**Function-level:**
```typescript
/**
 * Transform a path by prepending the app's base path for absolute paths
 *
 * Rules:
 * - Absolute paths starting with "/" are transformed (e.g., "/customers" -> "/apps/my-app/customers")
 * - Paths already starting with "/apps/" are passed through unchanged
 * - External URLs (starting with "http") are passed through unchanged
 * - Relative paths (not starting with "/") are passed through unchanged
 *
 * @param path - The path to transform
 * @param basePath - The app's base path (e.g., "/apps/my-app/preview")
 * @returns The transformed path
 */
export function transformPath(path: string, basePath: string): string {
	// ...
}
```

**Complex logic inline:**
```typescript
// Parse JWT payload without verification (server validates)
function parseJwt(token: string): { exp?: number } | null {
	try {
		const base64Url = token.split(".")[1];
		if (!base64Url) return null;
		const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
		// ...
	} catch {
		return null;
	}
}
```

### Component Patterns

**Functional components:**
```typescript
interface PageLoaderProps {
	message?: string;
	size?: "sm" | "md" | "lg";
	fullScreen?: boolean;
}

export function PageLoader({
	message = "Loading...",
	size = "md",
	fullScreen = false,
}: PageLoaderProps) {
	const sizeClasses = {
		sm: "h-8 w-8",
		md: "h-12 w-12",
		lg: "h-16 w-16",
	};

	return (
		<div className={containerClasses}>
			{/* JSX content */}
		</div>
	);
}
```

**Props pattern:** Always define interface, use destructuring
```typescript
export function WorkflowParametersForm({
	parameters,
	onExecute,
	isExecuting = false,
	showExecuteButton = true,
	executeButtonText = "Execute Workflow",
	className,
	values: controlledValues,
	onChange,
}: WorkflowParametersFormProps) {
	// ...
}
```

**State management:** Use `useState` for local state
```typescript
const [copiedCurl, setCopiedCurl] = useState(false);
const [newlyGeneratedKey, setNewlyGeneratedKey] = useState<string | null>(null);
```

**Effects:** Document dependencies
```typescript
useEffect(() => {
	// Fetch data only when dependencies change
	const subscription = subscribe();
	return () => subscription.unsubscribe();
}, [dependency]); // Document why each dependency is needed
```

### Error Handling

**Pattern:** Throw custom error types
```typescript
// lib/api-error.ts
export class ApiError extends Error {
	constructor(public status: number, message: string) {
		super(message);
	}
}

export class RateLimitError extends Error {
	constructor(public retryAfter: number) {
		super(`Rate limited, retry after ${retryAfter}s`);
	}
}
```

**Usage in components:**
```typescript
try {
	await apiClient.POST("/api/endpoint", { body: data });
	toast.success("Operation completed");
} catch (error) {
	if (error instanceof RateLimitError) {
		toast.error(`Too many requests, wait ${error.retryAfter}s`);
	} else {
		toast.error("Operation failed");
	}
}
```

### API Integration

**Pattern:** Use `openapi-fetch` with typed client
```typescript
import createClient from "openapi-fetch";
import type { paths } from "./v1";

const apiClient = createClient<paths>({ baseUrl: "" });

// Middleware for auth, CSRF, error handling
apiClient.use({
	async onRequest({ request }) {
		// Inject auth headers, CSRF token
		return request;
	},
	async onResponse({ response }) {
		// Handle 401, 429, etc.
		return response;
	},
});
```

**React Query integration:**
```typescript
import createQueryClient from "openapi-react-query";

export const $api = createQueryClient(apiClient);

// In components:
const { data, isLoading } = $api.useQuery("get", "/api/organizations");
const mutation = $api.useMutation("post", "/api/organizations");
mutation.mutate({ body: { name: "New Org" } });
```

### Date/Time Handling

**Pattern:** Centralized formatting utilities
```typescript
/**
 * Parse a date string from the backend, handling UTC timestamps properly.
 */
function parseBackendDate(dateString: string): Date {
	if (
		!dateString.endsWith("Z") &&
		!dateString.includes("+") &&
		!dateString.includes("-", 10)
	) {
		return new Date(dateString + "Z");
	}
	return new Date(dateString);
}

/**
 * Format a date/time string in the user's local timezone
 */
export function formatDate(
	dateString: string | Date,
	options?: Intl.DateTimeFormatOptions,
): string {
	const date =
		typeof dateString === "string" ? parseBackendDate(dateString) : dateString;

	const defaultOptions: Intl.DateTimeFormatOptions = {
		year: "numeric",
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
		second: "2-digit",
		...options,
	};

	return date.toLocaleString(undefined, defaultOptions);
}
```

### Styling

**Utility classes:** Use TailwindCSS with `cn()` helper
```typescript
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
	return twMerge(clsx(inputs));
}

// Usage:
const containerClasses = cn(
	fullScreen ? "flex h-screen w-screen" : "flex min-h-[400px]",
	"items-center justify-center bg-background"
);
```

**No inline styles:** Always use TailwindCSS classes
```typescript
// ✅ Good
<div className="flex items-center gap-4">

// ❌ Bad
<div style={{ display: "flex", gap: "1rem" }}>
```

---

*Convention analysis: 2026-01-30*
