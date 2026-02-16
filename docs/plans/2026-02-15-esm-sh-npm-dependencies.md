# esm.sh npm Dependency Support + Server-Side App Compilation

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** COMPLETED (2026-02-15). All tasks implemented on `feature/server-side-app-compilation`.

**Goal:** Let app authors declare npm dependencies in `app.yaml` and use them via standard `import` syntax. Packages load from esm.sh CDN at runtime. App code compilation moves entirely to the server — the editor preview updates on save, not on keystroke.

**Architecture:** External imports are transformed to `$deps["pkg"]` references by the server-side compiler (`compile.js`). The `/render` endpoint reads `app.yaml` dependencies and returns them in the response. The client loads packages from esm.sh before rendering. The editor preview uses server-compile-on-save.

**Tech Stack:** Node.js (@babel/standalone server-side only), esm.sh CDN, PyYAML (already available), React version pinning

### Babel Scope Note

`@babel/standalone` remains in the **client** bundle for one use case: **form HTML field templates** (`JsxTemplateRenderer`). These are small JSX expressions like `<div>{context.workflow.user_email}</div>` that reference live form state (`context.field`, `context.workflow`, `context.query`). They must compile client-side because:

- Templates re-render reactively as users fill out form fields
- Server round-trips per field change would add visible latency
- The expressions are tiny (single JSX fragment) — Babel compiles them in <1ms
- The scope is intentionally restricted (only `React` and `context` in scope)

This is architecturally distinct from app code (multi-file React apps with imports, routing, etc.) which compiles server-side.

---

## Background: How Things Work Now

### Compilation Pipeline
- **Server-side:** `api/src/services/app_compiler/compile.js` — Node.js script using `@babel/standalone`. Pipeline: `preprocessImports()` (rewrites `import {...} from "bifrost"` → `var {...} = $;`) → `preprocessExternalImports()` (rewrites npm imports → `var X = $deps["pkg"];`) → Babel `transform` → `postprocessExports()` (rewrites `export default` → `__defaultExport__ =`).
- **Client-side (forms only):** `client/src/components/ui/jsx-template-renderer.tsx` — uses `@babel/standalone` to compile small JSX template expressions in form HTML fields. Not used for app code.

### Editor Preview Flow
1. User types → `useAppCodeEditor.setSource()` → updates `state.source` (no compilation)
2. User saves (Ctrl+S) → source sent to server → server compiles → returns compiled JS → `setCompiled(data.compiled)` → preview re-renders
3. `AppCodePreview` receives compiled code → `createComponent(compiled, customComponents, true, externalDeps)` → renders

### Rendered App Flow
1. `JsxAppShell` fetches `/render` → gets pre-compiled JS + `dependencies` map from server
2. `loadDependencies(deps)` fetches npm packages from esm.sh CDN (cached per session)
3. `createComponent(compiled, customComponents, true, externalDeps)` — renders with `$deps` in scope
4. WebSocket updates trigger re-fetch from `/render`

---

### Task 1: Add External Import Transforms to Server Compiler

**Files:**
- Modify: `api/src/services/app_compiler/compile.js`

After bifrost import transforms, add a second pass catching all remaining ES module `import` statements and rewriting them to `$deps["pkg"]` references.

**Step 1: Write failing test for the compiler**

Create test file `api/tests/unit/services/test_app_compiler_deps.py`:

```python
"""Tests for external dependency import transforms in the app compiler."""
import pytest
from src.services.app_compiler import AppCompilerService


@pytest.fixture
def compiler():
    return AppCompilerService()


@pytest.mark.asyncio
async def test_named_import_transforms_to_deps(compiler):
    """import { X, Y } from "recharts" → const { X, Y } = $deps["recharts"];"""
    source = '''
import { LineChart, Line } from "recharts";
export default function Chart() {
    return <LineChart><Line dataKey="value" /></LineChart>;
}
'''
    result = await compiler.compile_file(source, "pages/chart.tsx")
    assert result.success
    assert '$deps["recharts"]' in result.compiled
    assert "import " not in result.compiled  # no raw imports left


@pytest.mark.asyncio
async def test_default_import_transforms_to_deps(compiler):
    """import X from "dayjs" → const X = ($deps["dayjs"].default || $deps["dayjs"]);"""
    source = '''
import dayjs from "dayjs";
export default function Page() {
    return <div>{dayjs().format("MMM D")}</div>;
}
'''
    result = await compiler.compile_file(source, "pages/index.tsx")
    assert result.success
    assert '$deps["dayjs"]' in result.compiled


@pytest.mark.asyncio
async def test_namespace_import_transforms_to_deps(compiler):
    """import * as R from "recharts" → const R = $deps["recharts"];"""
    source = '''
import * as R from "recharts";
export default function Chart() {
    return <R.LineChart><R.Line /></R.LineChart>;
}
'''
    result = await compiler.compile_file(source, "pages/chart.tsx")
    assert result.success
    assert '$deps["recharts"]' in result.compiled


@pytest.mark.asyncio
async def test_mixed_import_transforms_to_deps(compiler):
    """import X, { Y } from "pkg" → default + named destructuring."""
    source = '''
import Pkg, { Helper } from "some-pkg";
export default function Page() {
    return <div><Pkg /><Helper /></div>;
}
'''
    result = await compiler.compile_file(source, "pages/index.tsx")
    assert result.success
    assert '$deps["some-pkg"]' in result.compiled


@pytest.mark.asyncio
async def test_bifrost_imports_unchanged(compiler):
    """Bifrost imports should still use $ scope, not $deps."""
    source = '''
import { Button, Card } from "bifrost";
export default function Page() {
    return <Card><Button>Click</Button></Card>;
}
'''
    result = await compiler.compile_file(source, "pages/index.tsx")
    assert result.success
    assert "const {" in result.compiled
    assert "= $;" in result.compiled or "= $" in result.compiled
    assert "$deps" not in result.compiled


@pytest.mark.asyncio
async def test_mixed_bifrost_and_external_imports(compiler):
    """Bifrost and external imports coexist."""
    source = '''
import { Card, useWorkflowQuery } from "bifrost";
import { LineChart, Line } from "recharts";
import dayjs from "dayjs";

export default function Dashboard() {
    const { data } = useWorkflowQuery("get_metrics");
    return (
        <Card>
            <p>{dayjs().format("MMM D")}</p>
            <LineChart data={data}><Line dataKey="value" /></LineChart>
        </Card>
    );
}
'''
    result = await compiler.compile_file(source, "pages/dashboard.tsx")
    assert result.success
    assert "= $;" in result.compiled  # bifrost imports
    assert '$deps["recharts"]' in result.compiled
    assert '$deps["dayjs"]' in result.compiled


@pytest.mark.asyncio
async def test_no_imports_compiles_normally(compiler):
    """Files with no imports should compile without $deps references."""
    source = '''
export default function Page() {
    return <div>Hello</div>;
}
'''
    result = await compiler.compile_file(source, "pages/index.tsx")
    assert result.success
    assert "$deps" not in result.compiled
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/services/test_app_compiler_deps.py -v`
Expected: Tests that check for `$deps` will FAIL (transforms don't exist yet)

**Step 3: Implement external import transforms in compile.js**

In `api/src/services/app_compiler/compile.js`, add `preprocessExternalImports()` after `preprocessImports()`:

```javascript
function preprocessExternalImports(source) {
  // Named imports: import { X, Y } from "pkg" → const { X, Y } = $deps["pkg"];
  let result = source.replace(
    /^\s*import\s+(\{[^}]*\})\s+from\s+["']([^"']+)["']\s*;?\s*$/gm,
    'const $1 = $$deps["$2"];'
  );

  // Default imports: import X from "pkg" → const X = ($deps["pkg"].default || $deps["pkg"]);
  result = result.replace(
    /^\s*import\s+(\w+)\s+from\s+["']([^"']+)["']\s*;?\s*$/gm,
    'const $1 = ($$deps["$2"].default || $$deps["$2"]);'
  );

  // Namespace imports: import * as X from "pkg" → const X = $deps["pkg"];
  result = result.replace(
    /^\s*import\s+\*\s+as\s+(\w+)\s+from\s+["']([^"']+)["']\s*;?\s*$/gm,
    'const $1 = $$deps["$2"];'
  );

  // Mixed imports: import X, { Y, Z } from "pkg"
  result = result.replace(
    /^\s*import\s+(\w+)\s*,\s*(\{[^}]*\})\s+from\s+["']([^"']+)["']\s*;?\s*$/gm,
    'const $1 = ($$deps["$3"].default || $$deps["$3"]);\nconst $2 = $$deps["$3"];'
  );

  return result;
}
```

**IMPORTANT:** In the regex replacements, `$$deps` is needed because `$` is special in replacement strings. The output will be `$deps["pkg"]`.

Then update `compileFile()`:

```javascript
function compileFile(source, path) {
  try {
    let preprocessed = preprocessImports(source);      // bifrost imports → $
    preprocessed = preprocessExternalImports(preprocessed); // remaining → $deps["pkg"]
    // ... rest unchanged
  }
}
```

**Step 4: Run tests to verify they pass**

Run: `./test.sh tests/unit/services/test_app_compiler_deps.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add api/src/services/app_compiler/compile.js api/tests/unit/services/test_app_compiler_deps.py
git commit -m "feat: add external import → \$deps transform to server compiler"
```

---

### Task 2: Add Dependencies to Render Response

**Files:**
- Modify: `api/src/models/contracts/applications.py`
- Modify: `api/src/routers/app_code_files.py`

**Step 1: Write failing test**

Create `api/tests/unit/services/test_render_dependencies.py`:

```python
"""Tests for dependency reading and validation in the render endpoint."""
import pytest
from src.routers.app_code_files import _parse_dependencies


def test_parse_valid_dependencies():
    """Valid dependencies are returned as dict."""
    yaml_content = """
name: Test App
dependencies:
  recharts: "2.12"
  dayjs: "1.11"
"""
    deps = _parse_dependencies(yaml_content)
    assert deps == {"recharts": "2.12", "dayjs": "1.11"}


def test_parse_no_dependencies():
    """YAML without dependencies returns empty dict."""
    yaml_content = """
name: Test App
description: No deps
"""
    deps = _parse_dependencies(yaml_content)
    assert deps == {}


def test_parse_empty_yaml():
    """Empty YAML returns empty dict."""
    deps = _parse_dependencies("")
    assert deps == {}


def test_parse_none_yaml():
    """None input returns empty dict."""
    deps = _parse_dependencies(None)
    assert deps == {}


def test_parse_invalid_yaml():
    """Malformed YAML returns empty dict (graceful degradation)."""
    deps = _parse_dependencies("{{{{not yaml")
    assert deps == {}


def test_parse_invalid_package_name_skipped():
    """Invalid package names are skipped."""
    yaml_content = """
dependencies:
  recharts: "2.12"
  "../malicious": "1.0"
  "good-pkg": "1.0"
"""
    deps = _parse_dependencies(yaml_content)
    assert "recharts" in deps
    assert "good-pkg" in deps
    assert "../malicious" not in deps


def test_parse_invalid_version_skipped():
    """Invalid version strings are skipped."""
    yaml_content = """
dependencies:
  recharts: "2.12"
  dayjs: "latest"
"""
    deps = _parse_dependencies(yaml_content)
    assert "recharts" in deps
    assert "dayjs" not in deps


def test_parse_max_20_dependencies():
    """More than 20 dependencies are truncated."""
    lines = ["dependencies:"]
    for i in range(25):
        lines.append(f"  pkg-{i}: \"{i}.0\"")
    yaml_content = "\n".join(lines)
    deps = _parse_dependencies(yaml_content)
    assert len(deps) == 20


def test_parse_scoped_package_name():
    """Scoped packages like @scope/pkg are valid."""
    yaml_content = """
dependencies:
  "@tanstack/react-table": "8.20"
"""
    deps = _parse_dependencies(yaml_content)
    assert "@tanstack/react-table" in deps


def test_parse_caret_tilde_versions():
    """Versions with ^ or ~ prefix are valid."""
    yaml_content = """
dependencies:
  recharts: "^2.12"
  dayjs: "~1.11.3"
"""
    deps = _parse_dependencies(yaml_content)
    assert deps == {"recharts": "^2.12", "dayjs": "~1.11.3"}
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/services/test_render_dependencies.py -v`
Expected: FAIL — `_parse_dependencies` doesn't exist yet

**Step 3: Add `dependencies` field to AppRenderResponse**

In `api/src/models/contracts/applications.py`, update `AppRenderResponse`:

```python
class AppRenderResponse(BaseModel):
    """All compiled files needed to render an application."""

    files: list[RenderFileResponse]
    total: int
    dependencies: dict[str, str] = Field(
        default_factory=dict,
        description="npm dependencies from app.yaml {name: version} for esm.sh loading",
    )
```

**Step 4: Implement `_parse_dependencies()` in the router**

In `api/src/routers/app_code_files.py`, add at module level:

```python
import yaml

# Validation patterns for npm dependencies
_PKG_NAME_RE = re.compile(r"^(@[a-z0-9-]+/)?[a-z0-9][a-z0-9._-]*$")
_VERSION_RE = re.compile(r"^\^?~?\d+(\.\d+){0,2}$")
_MAX_DEPENDENCIES = 20


def _parse_dependencies(yaml_content: str | None) -> dict[str, str]:
    """Parse and validate dependencies from app.yaml content.

    Returns validated {name: version} dict. Skips invalid entries,
    never raises.
    """
    if not yaml_content:
        return {}

    try:
        data = yaml.safe_load(yaml_content)
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    raw_deps = data.get("dependencies")
    if not isinstance(raw_deps, dict):
        return {}

    deps: dict[str, str] = {}
    for name, version in raw_deps.items():
        if len(deps) >= _MAX_DEPENDENCIES:
            break

        name_str = str(name)
        version_str = str(version)

        if not _PKG_NAME_RE.match(name_str):
            logger.warning(f"Skipping invalid package name: {name_str}")
            continue
        if not _VERSION_RE.match(version_str):
            logger.warning(f"Skipping invalid version for {name_str}: {version_str}")
            continue

        deps[name_str] = version_str

    return deps
```

**Step 5: Wire into `render_app()`**

In the `render_app()` function, after building `file_contents` (step 2, around line 460), read `app.yaml`:

```python
    # Read dependencies from app.yaml (stored in _repo/, not _apps/)
    dependencies: dict[str, str] = {}
    try:
        file_index = FileIndexService(ctx.db)
        yaml_content = await file_index.read(f"apps/{app.slug}/app.yaml")
        dependencies = _parse_dependencies(yaml_content)
    except Exception:
        pass  # Dependencies are optional
```

And update the response (line ~505):

```python
    return AppRenderResponse(files=files, total=len(files), dependencies=dependencies)
```

**Caching decision:** Dependencies are always read from `file_index` (fast DB read), not cached in Redis. The Redis cache is for heavy S3 file reads — deps are tiny and don't benefit from caching.

Update `render_app()`:

```python
    # Read dependencies from app.yaml in file_index (fast DB read, not cached)
    dependencies: dict[str, str] = {}
    try:
        file_index = FileIndexService(ctx.db)
        yaml_content = await file_index.read(f"apps/{app.slug}/app.yaml")
        dependencies = _parse_dependencies(yaml_content)
    except Exception:
        pass

    # 1. Try Redis cache first
    cached = await app_storage.get_render_cache(app_id_str, storage_mode)
    if cached:
        files = [
            RenderFileResponse(path=p, code=c)
            for p, c in sorted(cached.items())
        ]
        return AppRenderResponse(files=files, total=len(files), dependencies=dependencies)

    # ... rest unchanged until final return ...

    return AppRenderResponse(files=files, total=len(files), dependencies=dependencies)
```

**Step 6: Run tests**

Run: `./test.sh tests/unit/services/test_render_dependencies.py -v`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add api/src/models/contracts/applications.py api/src/routers/app_code_files.py api/tests/unit/services/test_render_dependencies.py
git commit -m "feat: read app.yaml dependencies and include in render response"
```

---

### Task 3: Create ESM Loader Utility

**Files:**
- Create: `client/src/lib/esm-loader.ts`

**Step 1: Create the esm-loader module**

```typescript
/**
 * ESM Loader — loads npm packages from esm.sh CDN at runtime.
 *
 * Packages are loaded with React version pinning to minimize
 * compatibility issues with React-dependent packages.
 */
import React from "react";

const REACT_VERSION = React.version;
const ESM_SH = "https://esm.sh";

/** Module-level cache: loaded once per session */
const moduleCache = new Map<string, Record<string, unknown>>();

/**
 * Build esm.sh URL for a package, pinning React version.
 */
function buildUrl(name: string, version: string): string {
	return `${ESM_SH}/${name}@${version}?deps=react@${REACT_VERSION},react-dom@${REACT_VERSION}`;
}

/**
 * Load all dependencies from esm.sh in parallel.
 *
 * @param deps - Map of {packageName: version} from app.yaml
 * @returns Map of {packageName: moduleExports} for injection as $deps
 */
export async function loadDependencies(
	deps: Record<string, string>,
): Promise<Record<string, Record<string, unknown>>> {
	const result: Record<string, Record<string, unknown>> = {};
	const entries = Object.entries(deps);

	if (entries.length === 0) {
		return result;
	}

	await Promise.all(
		entries.map(async ([name, version]) => {
			const key = `${name}@${version}`;
			if (moduleCache.has(key)) {
				result[name] = moduleCache.get(key)!;
				return;
			}
			try {
				const mod = await import(/* @vite-ignore */ buildUrl(name, version));
				const exports = { ...mod };
				moduleCache.set(key, exports);
				result[name] = exports;
			} catch (err) {
				console.error(`Failed to load dependency ${name}@${version}:`, err);
				// Set empty object so $deps["pkg"] is defined but empty
				// — destructuring will give undefined for individual exports
				result[name] = {};
			}
		}),
	);

	return result;
}
```

**Step 2: Commit**

```bash
git add client/src/lib/esm-loader.ts
git commit -m "feat: add esm.sh dependency loader with caching"
```

---

### Task 4: Wire `$deps` into the Runtime

**Files:**
- Modify: `client/src/lib/app-code-runtime.ts`

**Step 1: Add `externalDeps` parameter to `createComponent()`**

In `client/src/lib/app-code-runtime.ts`, update the function signature and scope building:

```typescript
export function createComponent(
	source: string,
	customComponents: Record<string, React.ComponentType> = {},
	useCompiled: boolean = false,
	externalDeps: Record<string, Record<string, unknown>> = {},
): React.ComponentType {
```

Update the scope building (around line 349):

```typescript
	// Step 2: Build the full scope
	const scope = {
		...$,
		...customComponents,
		$: { ...$, ...customComponents },
		$deps: externalDeps,
	};
```

**Step 2: Commit**

```bash
git add client/src/lib/app-code-runtime.ts
git commit -m "feat: add \$deps parameter to createComponent runtime"
```

---

### Task 5: Thread Dependencies Through JsxAppShell and Renderers

**Files:**
- Modify: `client/src/components/jsx-app/JsxAppShell.tsx`
- Modify: `client/src/components/jsx-app/JsxPageRenderer.tsx`
- Modify: `client/src/lib/app-code-resolver.ts`

**Step 1: Update JsxAppShell to load dependencies**

In `JsxAppShell.tsx`:

1. Update the `AppRenderResponse` interface:
```typescript
interface AppRenderResponse {
	files: Array<{ path: string; code: string }>;
	total: number;
	dependencies?: Record<string, string>;
}
```

2. Update `JsxAppContext` to include `externalDeps`:
```typescript
interface JsxAppContext {
	appId: string;
	userComponentNames: Set<string>;
	allFiles: AppCodeFile[];
	externalDeps: Record<string, Record<string, unknown>>;
}
```

3. Update `fetchAppFiles` to return dependencies:
```typescript
interface FetchResult {
	files: AppCodeFile[];
	dependencies: Record<string, string>;
}

async function fetchAppFiles(
	appId: string,
	mode: "draft" | "live",
	signal?: AbortSignal,
): Promise<FetchResult> {
	const response = await authFetch(
		`/api/applications/${appId}/render?mode=${mode}`,
		{ signal },
	);

	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`Failed to fetch app files: ${errorText}`);
	}

	const data: AppRenderResponse = await response.json();
	return {
		files: data.files.map((f) => ({
			path: f.path,
			source: f.code,
			compiled: f.code,
		})),
		dependencies: data.dependencies ?? {},
	};
}
```

4. In `JsxAppShell` component, add state for deps and load them:
```typescript
import { loadDependencies } from "@/lib/esm-loader";

// Add state
const [externalDeps, setExternalDeps] = useState<Record<string, Record<string, unknown>>>({});

// In the loadApp effect, after fetching files:
const { files: appFiles, dependencies } = await fetchAppFiles(appId, mode, controller.signal);

if (controller.signal.aborted) return;

// Load external deps from esm.sh (parallel fetch)
let loadedDeps: Record<string, Record<string, unknown>> = {};
if (Object.keys(dependencies).length > 0) {
    loadedDeps = await loadDependencies(dependencies);
    if (controller.signal.aborted) return;
}

setFiles(appFiles);
setExternalDeps(loadedDeps);
```

5. Pass `externalDeps` through all child components: `LayoutWrapper`, `ProvidersWrapper`, `renderRoutes`, `AppContent`, `JsxPageRenderer`. Every place that builds a `JsxAppContext` or calls `createComponent()` or `resolveAppComponentsFromFiles()` needs to forward `externalDeps`.

The `JsxAppContext` interface already includes it. Update all `appContext` constructions:
```typescript
const appContext = useMemo<JsxAppContext>(
    () => ({ appId, userComponentNames, allFiles, externalDeps }),
    [appId, userComponentNames, allFiles, externalDeps],
);
```

And all `createComponent()` calls in `LayoutWrapper`, `ProvidersWrapper`:
```typescript
const Component = createComponent(
    file.compiled || file.source,
    customComponents,
    !!file.compiled,
    externalDeps,  // NEW
);
```

And Outlet context objects:
```typescript
<Outlet context={{ appId, userComponentNames, allFiles, externalDeps }} />
```

**Step 2: Update JsxPageRenderer**

Add `externalDeps` to props and pass through:

```typescript
interface JsxPageRendererProps {
	appId: string;
	file: AppCodeFile;
	userComponentNames: Set<string>;
	allFiles?: AppCodeFile[];
	externalDeps?: Record<string, Record<string, unknown>>;
}
```

In the `loadPage` effect, pass to `createComponent()`:
```typescript
const Component = createComponent(
    file.compiled || source,
    customComponents,
    !!file.compiled,
    externalDeps ?? {},
);
```

And pass to `resolveAppComponentsFromFiles()`:
```typescript
customComponents = await resolveAppComponentsFromFiles(
    appId,
    componentNames,
    userComponentNames,
    allFiles,
    externalDeps ?? {},
);
```

**Step 3: Update app-code-resolver.ts**

Add `externalDeps` parameter to `resolveAppComponentsFromFiles()`:

```typescript
export async function resolveAppComponentsFromFiles(
	appId: string,
	componentNames: string[],
	userComponentNames: Set<string>,
	allFiles?: AppCodeFile[],
	externalDeps: Record<string, Record<string, unknown>> = {},
): Promise<Record<string, React.ComponentType>> {
```

Pass through to `createComponent()`:
```typescript
const component = createComponent(source, {}, isPreCompiled, externalDeps);
```

**Step 4: Commit**

```bash
git add client/src/components/jsx-app/JsxAppShell.tsx client/src/components/jsx-app/JsxPageRenderer.tsx client/src/lib/app-code-resolver.ts
git commit -m "feat: thread external deps through shell, renderer, and resolver"
```

---

### Task 6: Remove Client-Side Babel for App Code

**Scope:** Remove client-side Babel from the **app code** compilation path only. `@babel/standalone` stays in `package.json` because it's still used by `JsxTemplateRenderer` for form HTML field templates (see Babel Scope Note at top of plan).

**Files:**
- Modify: `client/src/lib/app-code-runtime.ts` — inline `wrapAsComponent`, remove Babel fallback
- Modify: `client/src/components/app-code-editor/useAppCodeEditor.ts` — remove client compilation, save-only flow
- Delete: `client/src/lib/app-code-compiler.ts`

**What was done:**
- `createComponent()` now returns an error component if `useCompiled=false` instead of compiling client-side
- `wrapAsComponent()` moved inline into `app-code-runtime.ts`
- `useAppCodeEditor.compile()` is a no-op stub — compilation happens server-side on save
- `app-code-compiler.ts` deleted (no remaining imports)
- `@babel/standalone` **kept** in `package.json` — still imported by `client/src/components/ui/jsx-template-renderer.tsx` for form templates

---

### Task 7: Regenerate Types and Verify

**Step 1: Regenerate TypeScript types**

With the dev stack running (`./debug.sh`):

```bash
cd client && npm run generate:types
```

This will pick up the new `dependencies` field on `AppRenderResponse`.

**Step 2: Run full verification**

```bash
# Backend
cd api && pyright && ruff check .

# Frontend
cd ../client && npm run tsc && npm run lint

# Tests
cd .. && ./test.sh
```

**Step 3: Commit**

```bash
git add client/src/lib/v1.d.ts
git commit -m "chore: regenerate types with dependencies field"
```

---

### Task 8: Manual Integration Test

Not automated — verify manually with a running dev stack.

**Step 1: Add dependencies to an existing app**

Find or create an app. Edit its `app.yaml` in `_repo/apps/{slug}/app.yaml` to include:

```yaml
name: "Test App"
dependencies:
  dayjs: "1.11"
```

**Step 2: Use the dependency in a page**

Edit a page file:

```tsx
import { Card } from "bifrost";
import dayjs from "dayjs";

export default function TestPage() {
    return (
        <Card className="p-6">
            <h1>Dependency Test</h1>
            <p>Today is: {dayjs().format("MMMM D, YYYY")}</p>
        </Card>
    );
}
```

**Step 3: Verify**

1. Save the file in the editor
2. Preview should show the formatted date (after save, not on keystroke)
3. Check browser Network tab: should see esm.sh request for dayjs
4. Reload page: dayjs should load from esm.sh cache (no re-download)
5. Test with no dependencies (backwards compatibility): existing apps should render normally
6. Test with invalid package name in app.yaml: should be skipped, app still renders

**Step 4: Final commit if any fixes needed**

---

## Files Changed Summary

| File | Change |
|------|--------|
| `api/src/services/app_compiler/compile.js` | Add `preprocessExternalImports()` for `$deps["pkg"]` transforms |
| `api/src/models/contracts/applications.py` | Add `dependencies` field to `AppRenderResponse` |
| `api/src/routers/app_code_files.py` | Add `_parse_dependencies()`, read app.yaml, include deps in response |
| `client/src/lib/esm-loader.ts` | **NEW** — esm.sh loader with caching and React version pinning |
| `client/src/lib/app-code-runtime.ts` | Add `$deps` + `externalDeps` param, inline `wrapAsComponent`, remove Babel fallback |
| `client/src/components/jsx-app/JsxAppShell.tsx` | Load deps after fetch, pass through context |
| `client/src/components/jsx-app/JsxPageRenderer.tsx` | Accept and pass `externalDeps` |
| `client/src/lib/app-code-resolver.ts` | Accept and pass `externalDeps` |
| `client/src/components/app-code-editor/useAppCodeEditor.ts` | Remove client compilation, save-only flow |
| `client/src/lib/app-code-compiler.ts` | **DELETED** |
| `api/tests/unit/services/test_app_compiler_deps.py` | **NEW** — compiler transform tests |
| `api/tests/unit/services/test_render_dependencies.py` | **NEW** — dependency parsing tests |

**Not changed (intentionally kept):**

| File | Reason |
|------|--------|
| `client/src/components/ui/jsx-template-renderer.tsx` | Keeps `@babel/standalone` for form JSX templates — client-side compilation needed for reactive form context |
| `client/package.json` (`@babel/standalone`) | Still required by `jsx-template-renderer.tsx` |
