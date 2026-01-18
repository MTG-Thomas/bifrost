# JSX App Builder Design

## Overview

Replace the current JSON-based component tree with a JSX-first approach where pages and components are authored as React code. This brings the App Builder closer to traditional web development, making it more intuitive for LLMs and developers while maintaining platform integration (workflows, state, navigation).

## Goals

1. **LLM-friendly** — JSX is React; LLMs understand it natively
2. **Minimal limitations** — If you can do it in React, you can do it here
3. **Platform integration** — Easy access to workflows, state, user context
4. **Compiled output** — Compile in editor and store compiled code for production

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Editor UI                                │
│  ┌─────────────┐  ┌──────────────────────┐  ┌────────────────┐  │
│  │  File Tree  │  │    Monaco Editor     │  │  Live Preview  │  │
│  │  (paths)    │  │    (JSX/TS source)   │  │  (Rendered)    │  │
│  └─────────────┘  └──────────────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Compiler (Browser)                          │
│  ┌─────────────┐  ┌──────────────────────┐  ┌────────────────┐  │
│  │   Babel     │  │   File Resolver      │  │  Scope         │  │
│  │  Standalone │  │  (path → compiled)   │  │  Injection     │  │
│  └─────────────┘  └──────────────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Database Storage                           │
│                                                                   │
│   app_jsx_files (one table, path-based organization)             │
│   - pages, components, modules, layouts all in one table         │
│   - path conventions determine type                               │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Runtime (Browser)                         │
│  ┌─────────────┐  ┌──────────────────────┐  ┌────────────────┐  │
│  │   Router    │  │   Platform APIs      │  │  UI Components │  │
│  │  (nested)   │  │  (runWorkflow, etc)  │  │  (shadcn)      │  │
│  └─────────────┘  └──────────────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## File Organization

Everything lives in one table with path-based conventions:

```
_providers                    ← App-level providers (auth, theme, global state)
_layout                       ← Root layout (sidebar, nav, wraps all pages)

pages/
  index                       ← /
  clients/
    _layout                   ← Layout for /clients/* (optional subnav)
    index                     ← /clients
    [id]/
      index                   ← /clients/:id
      contacts                ← /clients/:id/contacts
  settings/
    _layout                   ← Settings-specific layout
    index                     ← /settings
    billing                   ← /settings/billing

components/
  ClientCard
  ContactForm
  DataGrid

modules/
  useClients                  ← Custom hooks
  formatCurrency              ← Utilities
  constants                   ← Shared constants
```

**Path conventions:**
| Pattern | Type |
|---------|------|
| `_providers` | App-level providers wrapper |
| `_layout` | Root layout |
| `pages/**/_layout` | Nested layout for subroutes |
| `pages/**/index` | Page at that route |
| `pages/**/[param]` | Dynamic route segment |
| `components/*` | Reusable UI components |
| `modules/*` | Hooks, utils, non-JSX code |

## Database Schema

### Existing Structure (Unchanged)

```
applications
    └── app_versions (draft, published)
            └── (pages, components via new table)
```

### Applications Table (Add Engine Type)

```sql
-- Add engine column to distinguish app types
ALTER TABLE app_applications ADD COLUMN engine VARCHAR(20) NOT NULL DEFAULT 'components';
-- 'components' = current JSON component tree (v1)
-- 'jsx' = new JSX-based engine (v2)
```

### New JSX Files Table

```sql
CREATE TABLE app_jsx_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_version_id UUID NOT NULL REFERENCES app_versions(id) ON DELETE CASCADE,

    -- Identity (path is the key)
    path VARCHAR(500) NOT NULL,       -- "pages/clients/[id]" or "components/ClientCard"

    -- Content
    source TEXT NOT NULL,             -- Original JSX/TS source
    compiled TEXT,                    -- Babel output (stored on save)

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(app_version_id, path)
);

CREATE INDEX idx_jsx_files_version ON app_jsx_files(app_version_id);
CREATE INDEX idx_jsx_files_path ON app_jsx_files(app_version_id, path);
```

**Notes:**
- Fits into existing app_versions model (draft/published workflow unchanged)
- When publishing, files are copied with the new version like other entities
- Path determines type via convention — no separate `type` column needed
- Git sync: files serialize to `{path}.jsx` in workspace

## Platform Scope (Injected APIs)

The platform injects a minimal set of APIs. Everything else is just React.

### React (Full Access)

```typescript
// All standard React hooks available
useState, useEffect, useMemo, useCallback, useRef, useContext, useReducer
```

### Platform APIs

```typescript
/**
 * Run a workflow and get result
 * Use with useState/useEffect for data fetching, or call directly for mutations
 */
async function runWorkflow<T>(
  workflowId: string,
  params?: Record<string, unknown>
): Promise<T>

/**
 * Convenience hook for data fetching (optional - developers can use useEffect + runWorkflow)
 */
function useWorkflow<T>(
  workflowId: string,
  params?: Record<string, unknown>,
  options?: { enabled?: boolean }
): {
  data: T | undefined;
  isLoading: boolean;
  error: string | undefined;
  refresh: () => void;
}

/**
 * Get URL path parameters (e.g., /clients/:id → { id: "123" })
 */
function useParams(): Record<string, string>

/**
 * Get query string parameters
 */
function useSearchParams(): URLSearchParams

/**
 * Navigate to a page
 */
function navigate(path: string): void

/**
 * Current authenticated user
 */
function useUser(): {
  id: string;
  email: string;
  name: string;
  role: string;
  organizationId: string;
}

/**
 * App-level state (persists across pages within session)
 */
function useAppState<T>(key: string, initialValue: T): [T, (value: T) => void]
```

### What's NOT Special

These are just normal React patterns — no platform API needed:

```typescript
// Modals — just use state
const [isOpen, setIsOpen] = useState(false);
<Modal open={isOpen} onClose={() => setIsOpen(false)}>...</Modal>

// Toasts — just use sonner (already in scope)
import { toast } from 'sonner';
toast.success('Saved!');

// Forms — just use state
const [form, setForm] = useState({ name: '', email: '' });

// Loading states — just use state
const [isLoading, setIsLoading] = useState(false);
```

### UI Components (shadcn-based)

All existing shadcn components available, plus platform conveniences:

```typescript
// Layout
Column, Row, Grid      // Flex/grid containers with gap, align, justify props
Card                   // Card with optional header
Tabs, TabsList, TabsTrigger, TabsContent

// Typography
Heading                // level={1-6}
Text                   // Paragraph/span with muted, size variants

// Data Display
DataTable              // Full-featured table with sorting, pagination, row actions
Badge
Avatar
Progress
Skeleton

// Forms (standard shadcn)
Input, Textarea, Select, Checkbox, Switch, RadioGroup, Label
DatePicker, FileUpload (if we add them)

// Actions
Button, Link

// Feedback
Dialog (Modal), Alert, AlertDialog
// toast() from sonner

// Navigation
Breadcrumb

// All other shadcn primitives available as-is
```

Developers can use these directly or build their own components in `components/`.

## Compiler Implementation

### Core Compiler

```typescript
// client/src/lib/jsx-compiler.ts

import { transform } from '@babel/standalone';

interface CompileResult {
  success: boolean;
  compiled?: string;
  error?: string;
}

/**
 * Compile JSX source to executable JavaScript
 */
export function compileJsx(source: string): CompileResult {
  try {
    const { code } = transform(source, {
      presets: ['react'],
      plugins: [
        // Support optional chaining, nullish coalescing, etc.
        'proposal-optional-chaining',
        'proposal-nullish-coalescing-operator',
      ],
    });

    return { success: true, compiled: code };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Compilation failed'
    };
  }
}

/**
 * Wrap compiled code in a component factory
 */
export function wrapAsComponent(compiled: string): string {
  return `
    return function DynamicComponent(props) {
      ${compiled}
    }
  `;
}
```

### Component Factory

```typescript
// client/src/lib/jsx-runtime.ts

import React from 'react';
import { compileJsx, wrapAsComponent } from './jsx-compiler';

// Platform hooks (implementations)
import { useWorkflow, useWorkflowMutation } from '@/hooks/jsx/useWorkflow';
import { usePageState, useAppState } from '@/hooks/jsx/useState';
import { useModal } from '@/hooks/jsx/useModal';
import { useUser } from '@/hooks/jsx/useUser';
import { useParams, useQuery } from '@/hooks/jsx/useRouter';

// Platform functions
import { navigate, runWorkflow, toast, openModal, closeModal } from '@/lib/jsx/platform';

// UI Components
import * as UIComponents from '@/components/jsx-ui';

/**
 * The scope available to all JSX code
 */
const PLATFORM_SCOPE = {
  // React
  React,
  useState: React.useState,
  useEffect: React.useEffect,
  useMemo: React.useMemo,
  useCallback: React.useCallback,
  useRef: React.useRef,

  // Platform hooks
  useWorkflow,
  useWorkflowMutation,
  usePageState,
  useAppState,
  useModal,
  useUser,
  useParams,
  useQuery,

  // Platform functions
  navigate,
  runWorkflow,
  toast,
  openModal,
  closeModal,

  // UI Components
  ...UIComponents,
};

/**
 * Create a React component from JSX source or compiled code
 */
export function createComponent(
  source: string,
  customComponents: Record<string, React.ComponentType> = {},
  useCompiled: boolean = false
): React.ComponentType {
  // Compile if needed
  let compiled: string;
  if (useCompiled) {
    compiled = source; // Already compiled
  } else {
    const result = compileJsx(source);
    if (!result.success) {
      // Return an error component
      return () => (
        <div className="p-4 bg-red-50 border border-red-200 rounded text-red-700">
          <strong>Compilation Error:</strong> {result.error}
        </div>
      );
    }
    compiled = result.compiled!;
  }

  // Build the full scope
  const scope = {
    ...PLATFORM_SCOPE,
    ...customComponents,
  };

  // Create argument names and values
  const argNames = Object.keys(scope);
  const argValues = Object.values(scope);

  // Create the component factory
  const wrapped = wrapAsComponent(compiled);
  const factory = new Function(...argNames, wrapped);

  // Execute with scope
  try {
    return factory(...argValues);
  } catch (err) {
    // Return runtime error component
    return () => (
      <div className="p-4 bg-red-50 border border-red-200 rounded text-red-700">
        <strong>Runtime Error:</strong> {err instanceof Error ? err.message : 'Unknown error'}
      </div>
    );
  }
}
```

### Component Resolution

```typescript
// client/src/lib/jsx-resolver.ts

import { createComponent } from './jsx-runtime';

interface ComponentCache {
  component: React.ComponentType;
  compiledAt: number;
}

// Cache compiled components
const componentCache = new Map<string, ComponentCache>();

/**
 * Resolve custom components for an app
 */
export async function resolveAppComponents(
  appId: string,
  componentNames: string[]
): Promise<Record<string, React.ComponentType>> {
  const components: Record<string, React.ComponentType> = {};

  for (const name of componentNames) {
    // Check cache first
    const cacheKey = `${appId}:${name}`;
    const cached = componentCache.get(cacheKey);

    if (cached) {
      components[name] = cached.component;
      continue;
    }

    // Fetch from API
    const response = await fetch(`/api/apps/${appId}/jsx-components/${name}`);
    if (!response.ok) continue;

    const data = await response.json();

    // Use compiled version if available, otherwise compile
    const source = data.jsx_compiled || data.jsx_source;
    const useCompiled = !!data.jsx_compiled;

    // Recursively resolve dependencies (this component might use other custom components)
    // For now, simple implementation - could detect and resolve recursively
    const component = createComponent(source, {}, useCompiled);

    // Cache it
    componentCache.set(cacheKey, {
      component,
      compiledAt: Date.now(),
    });

    components[name] = component;
  }

  return components;
}

/**
 * Extract component names from JSX source
 * (Simple regex-based, could use AST for accuracy)
 */
export function extractComponentNames(source: string): string[] {
  const matches = source.matchAll(/<([A-Z][a-zA-Z0-9]*)/g);
  const names = new Set<string>();

  for (const match of matches) {
    names.add(match[1]);
  }

  return Array.from(names);
}

/**
 * Clear cache for an app (call after component updates)
 */
export function clearAppCache(appId: string): void {
  for (const key of componentCache.keys()) {
    if (key.startsWith(`${appId}:`)) {
      componentCache.delete(key);
    }
  }
}
```

## Page Renderer

```typescript
// client/src/components/jsx-app/PageRenderer.tsx

import React, { useMemo, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { createComponent } from '@/lib/jsx-runtime';
import { resolveAppComponents, extractComponentNames } from '@/lib/jsx-resolver';
import { Skeleton } from '@/components/ui/skeleton';
import { PageStateProvider } from '@/contexts/PageStateContext';

interface PageRendererProps {
  appId: string;
  page: {
    id: string;
    path: string;
    jsx_source: string;
    jsx_compiled?: string;
  };
}

export function PageRenderer({ appId, page }: PageRendererProps) {
  const [PageComponent, setPageComponent] = useState<React.ComponentType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function loadPage() {
      setIsLoading(true);
      setError(null);

      try {
        // Extract custom component names from source
        const source = page.jsx_source;
        const componentNames = extractComponentNames(source);

        // Filter to only non-built-in components
        const customNames = componentNames.filter(name => !isBuiltInComponent(name));

        // Resolve custom components
        const customComponents = await resolveAppComponents(appId, customNames);

        // Create the page component
        const useCompiled = !!page.jsx_compiled;
        const Component = createComponent(
          useCompiled ? page.jsx_compiled! : source,
          customComponents,
          useCompiled
        );

        setPageComponent(() => Component);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load page');
      } finally {
        setIsLoading(false);
      }
    }

    loadPage();
  }, [appId, page.id, page.jsx_source, page.jsx_compiled]);

  if (isLoading) {
    return (
      <div className="p-6 space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 bg-red-50 border border-red-200 rounded-lg">
        <h2 className="text-lg font-semibold text-red-700">Page Error</h2>
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  if (!PageComponent) {
    return null;
  }

  return (
    <PageStateProvider pageId={page.id}>
      <PageComponent />
    </PageStateProvider>
  );
}

// Check if a component name is built-in
function isBuiltInComponent(name: string): boolean {
  const builtIns = new Set([
    // Layout
    'Column', 'Row', 'Grid', 'Card', 'Tabs', 'TabItem',
    // Typography
    'Heading', 'Text',
    // Data
    'DataTable', 'Badge', 'Stat', 'Progress', 'Skeleton', 'Avatar',
    // Forms
    'Form', 'TextInput', 'TextArea', 'NumberInput', 'Select',
    'Checkbox', 'Switch', 'RadioGroup', 'DatePicker', 'FileUpload',
    // Actions
    'Button', 'Link',
    // Feedback
    'Modal', 'Alert',
    // Navigation
    'Breadcrumb',
    // React built-ins that might appear
    'Fragment', 'Suspense',
  ]);

  return builtIns.has(name);
}
```

## Editor Implementation

```typescript
// client/src/components/jsx-app/JsxEditor.tsx

import React, { useState, useCallback } from 'react';
import Editor from '@monaco-editor/react';
import { compileJsx } from '@/lib/jsx-compiler';
import { createComponent } from '@/lib/jsx-runtime';
import { resolveAppComponents, extractComponentNames } from '@/lib/jsx-resolver';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

interface JsxEditorProps {
  appId: string;
  initialSource: string;
  initialCompiled?: string;
  onSave: (source: string, compiled: string) => void;
}

export function JsxEditor({ appId, initialSource, initialCompiled, onSave }: JsxEditorProps) {
  const [source, setSource] = useState(initialSource);
  const [compiled, setCompiled] = useState(initialCompiled || '');
  const [error, setError] = useState<string | null>(null);
  const [PreviewComponent, setPreviewComponent] = useState<React.ComponentType | null>(null);
  const [activeTab, setActiveTab] = useState<'source' | 'compiled' | 'preview'>('source');

  // Compile on change (debounced in production)
  const handleCompile = useCallback(async () => {
    const result = compileJsx(source);

    if (!result.success) {
      setError(result.error || 'Compilation failed');
      setCompiled('');
      setPreviewComponent(null);
      return;
    }

    setError(null);
    setCompiled(result.compiled!);

    // Create preview component
    try {
      const componentNames = extractComponentNames(source);
      const customNames = componentNames.filter(name => !isBuiltInComponent(name));
      const customComponents = await resolveAppComponents(appId, customNames);

      const Component = createComponent(result.compiled!, customComponents, true);
      setPreviewComponent(() => Component);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Preview failed');
    }
  }, [source, appId]);

  // Auto-compile on source change (debounced)
  React.useEffect(() => {
    const timer = setTimeout(handleCompile, 500);
    return () => clearTimeout(timer);
  }, [handleCompile]);

  const handleSave = () => {
    if (compiled && !error) {
      onSave(source, compiled);
    }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between p-2 border-b">
        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as typeof activeTab)}>
          <TabsList>
            <TabsTrigger value="source">Source</TabsTrigger>
            <TabsTrigger value="compiled">Compiled</TabsTrigger>
            <TabsTrigger value="preview">Preview</TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={handleCompile}>
            Compile
          </Button>
          <Button onClick={handleSave} disabled={!!error}>
            Save
          </Button>
        </div>
      </div>

      {/* Error display */}
      {error && (
        <Alert variant="destructive" className="m-2">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Editor/Preview area */}
      <div className="flex-1 min-h-0">
        {activeTab === 'source' && (
          <Editor
            height="100%"
            language="javascript"
            theme="vs-dark"
            value={source}
            onChange={(value) => setSource(value || '')}
            options={{
              minimap: { enabled: false },
              fontSize: 14,
              lineNumbers: 'on',
              wordWrap: 'on',
              tabSize: 2,
            }}
          />
        )}

        {activeTab === 'compiled' && (
          <Editor
            height="100%"
            language="javascript"
            theme="vs-dark"
            value={compiled}
            options={{
              readOnly: true,
              minimap: { enabled: false },
              fontSize: 14,
              lineNumbers: 'on',
            }}
          />
        )}

        {activeTab === 'preview' && (
          <div className="h-full overflow-auto bg-background p-4">
            {PreviewComponent ? (
              <React.ErrorBoundary
                fallback={<div className="text-red-500">Preview crashed</div>}
              >
                <PreviewComponent />
              </React.ErrorBoundary>
            ) : (
              <div className="text-muted-foreground">No preview available</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
```

## API Endpoints

### JSX Components CRUD

```python
# api/src/handlers/app_jsx_components.py

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import uuid

router = APIRouter(prefix="/api/apps/{app_id}/jsx-components", tags=["jsx-components"])

class JsxComponentCreate(BaseModel):
    name: str
    description: Optional[str] = None
    jsx_source: str
    props_schema: Optional[dict] = None

class JsxComponentUpdate(BaseModel):
    description: Optional[str] = None
    jsx_source: Optional[str] = None
    jsx_compiled: Optional[str] = None
    props_schema: Optional[dict] = None

class JsxComponentResponse(BaseModel):
    id: uuid.UUID
    app_id: uuid.UUID
    name: str
    description: Optional[str]
    jsx_source: str
    jsx_compiled: Optional[str]
    props_schema: dict
    created_at: datetime
    updated_at: datetime

@router.get("")
async def list_jsx_components(app_id: uuid.UUID) -> list[JsxComponentResponse]:
    """List all JSX components for an app"""
    pass

@router.get("/{name}")
async def get_jsx_component(app_id: uuid.UUID, name: str) -> JsxComponentResponse:
    """Get a JSX component by name"""
    pass

@router.post("")
async def create_jsx_component(
    app_id: uuid.UUID,
    component: JsxComponentCreate
) -> JsxComponentResponse:
    """Create a new JSX component"""
    pass

@router.patch("/{component_id}")
async def update_jsx_component(
    app_id: uuid.UUID,
    component_id: uuid.UUID,
    updates: JsxComponentUpdate
) -> JsxComponentResponse:
    """Update a JSX component"""
    pass

@router.delete("/{component_id}")
async def delete_jsx_component(app_id: uuid.UUID, component_id: uuid.UUID):
    """Delete a JSX component"""
    pass
```

### Page JSX Updates

```python
# Extend existing page endpoints

class PageJsxUpdate(BaseModel):
    jsx_source: str
    jsx_compiled: Optional[str] = None

@router.patch("/{page_id}/jsx")
async def update_page_jsx(
    app_id: uuid.UUID,
    page_id: str,
    updates: PageJsxUpdate
) -> PageResponse:
    """Update page JSX source and compiled output"""
    pass
```

## State Management

### Page State Context

```typescript
// client/src/contexts/PageStateContext.tsx

import React, { createContext, useContext, useState, useCallback } from 'react';

interface PageStateContextValue {
  state: Record<string, unknown>;
  setState: (key: string, value: unknown) => void;
}

const PageStateContext = createContext<PageStateContextValue | null>(null);

export function PageStateProvider({
  pageId,
  children
}: {
  pageId: string;
  children: React.ReactNode;
}) {
  const [state, setStateInternal] = useState<Record<string, unknown>>({});

  const setState = useCallback((key: string, value: unknown) => {
    setStateInternal(prev => ({ ...prev, [key]: value }));
  }, []);

  return (
    <PageStateContext.Provider value={{ state, setState }}>
      {children}
    </PageStateContext.Provider>
  );
}

export function usePageState<T>(key: string, initialValue: T): [T, (value: T) => void] {
  const context = useContext(PageStateContext);
  if (!context) throw new Error('usePageState must be used within PageStateProvider');

  const value = (context.state[key] ?? initialValue) as T;
  const setValue = (newValue: T) => context.setState(key, newValue);

  return [value, setValue];
}
```

### App State (Zustand)

```typescript
// client/src/stores/jsx-app.store.ts

import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';

interface JsxAppState {
  // App-level state (persists across pages)
  appState: Record<string, unknown>;
  setAppState: (key: string, value: unknown) => void;

  // Modal state
  openModals: Map<string, { data: Record<string, unknown> }>;
  openModal: (id: string, data?: Record<string, unknown>) => void;
  closeModal: (id: string) => void;
  isModalOpen: (id: string) => boolean;
  getModalData: (id: string) => Record<string, unknown>;

  // Clear on app change
  reset: () => void;
}

export const useJsxAppStore = create<JsxAppState>()(
  subscribeWithSelector((set, get) => ({
    appState: {},
    setAppState: (key, value) => set(state => ({
      appState: { ...state.appState, [key]: value }
    })),

    openModals: new Map(),
    openModal: (id, data = {}) => set(state => {
      const modals = new Map(state.openModals);
      modals.set(id, { data });
      return { openModals: modals };
    }),
    closeModal: (id) => set(state => {
      const modals = new Map(state.openModals);
      modals.delete(id);
      return { openModals: modals };
    }),
    isModalOpen: (id) => get().openModals.has(id),
    getModalData: (id) => get().openModals.get(id)?.data ?? {},

    reset: () => set({ appState: {}, openModals: new Map() }),
  }))
);

// Hook for JSX components
export function useAppState<T>(key: string, initialValue: T): [T, (value: T) => void] {
  const value = useJsxAppStore(state => (state.appState[key] ?? initialValue) as T);
  const setAppState = useJsxAppStore(state => state.setAppState);

  return [value, (newValue: T) => setAppState(key, newValue)];
}
```

## Example Usage

### Root Layout (`_layout`)

```jsx
const user = useUser();

return (
  <div className="flex h-screen">
    <Sidebar>
      <SidebarHeader>
        <img src="https://cdn.example.com/logo.png" className="h-8" />
      </SidebarHeader>
      <SidebarNav>
        <SidebarLink to="/clients" icon="Users">Clients</SidebarLink>
        <SidebarLink to="/projects" icon="Briefcase">Projects</SidebarLink>
        <SidebarLink to="/settings" icon="Settings">Settings</SidebarLink>
      </SidebarNav>
      <SidebarFooter>
        <Text muted>{user.name}</Text>
      </SidebarFooter>
    </Sidebar>

    <main className="flex-1 overflow-auto">
      <Outlet />
    </main>
  </div>
);
```

### Page with Data (`pages/clients/index`)

```jsx
const { data: clients, isLoading, refresh } = useWorkflow('list_clients');
const [search, setSearch] = useState('');
const [editingClient, setEditingClient] = useState(null);

const filtered = useMemo(() => {
  if (!clients || !search) return clients ?? [];
  return clients.filter(c =>
    c.name.toLowerCase().includes(search.toLowerCase())
  );
}, [clients, search]);

if (isLoading) return <Skeleton className="h-96" />;

return (
  <Column gap={6} className="p-6">
    <Row justify="between" align="center">
      <Heading level={1}>Clients</Heading>
      <Button onClick={() => navigate('/clients/new')}>Add Client</Button>
    </Row>

    <Input
      placeholder="Search clients..."
      value={search}
      onChange={(e) => setSearch(e.target.value)}
    />

    <DataTable
      data={filtered}
      columns={[
        { key: 'name', header: 'Name' },
        { key: 'email', header: 'Email' },
        { key: 'status', header: 'Status', type: 'badge' },
      ]}
      onRowClick={(row) => navigate(`/clients/${row.id}`)}
      rowActions={[
        {
          label: 'Edit',
          icon: 'Pencil',
          onClick: (row) => setEditingClient(row),
        },
      ]}
    />

    {/* Modal is just React state */}
    <Dialog open={!!editingClient} onOpenChange={() => setEditingClient(null)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit Client</DialogTitle>
        </DialogHeader>
        <ClientForm
          initialValues={editingClient}
          onSubmit={async (values) => {
            await runWorkflow('update_client', values);
            setEditingClient(null);
            toast.success('Client updated');
            refresh();
          }}
          onCancel={() => setEditingClient(null)}
        />
      </DialogContent>
    </Dialog>
  </Column>
);
```

### Reusable Component (`components/ClientForm`)

```jsx
const { initialValues, onSubmit, onCancel } = props;
const [values, setValues] = useState(initialValues ?? {});
const [isSubmitting, setIsSubmitting] = useState(false);

const handleSubmit = async () => {
  setIsSubmitting(true);
  try {
    await onSubmit(values);
  } finally {
    setIsSubmitting(false);
  }
};

return (
  <Column gap={4}>
    <div>
      <Label>Name</Label>
      <Input
        value={values.name ?? ''}
        onChange={(e) => setValues({ ...values, name: e.target.value })}
      />
    </div>

    <div>
      <Label>Email</Label>
      <Input
        type="email"
        value={values.email ?? ''}
        onChange={(e) => setValues({ ...values, email: e.target.value })}
      />
    </div>

    <div>
      <Label>Status</Label>
      <Select value={values.status ?? 'active'} onValueChange={(v) => setValues({ ...values, status: v })}>
        <SelectTrigger><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="active">Active</SelectItem>
          <SelectItem value="inactive">Inactive</SelectItem>
        </SelectContent>
      </Select>
    </div>

    <Row justify="end" gap={2}>
      <Button variant="outline" onClick={onCancel}>Cancel</Button>
      <Button onClick={handleSubmit} disabled={isSubmitting}>
        {isSubmitting ? 'Saving...' : 'Save'}
      </Button>
    </Row>
  </Column>
);
```

### Custom Hook (`modules/useClients`)

```jsx
// A custom hook that wraps workflow calls with caching/refresh logic

const [clients, setClients] = useState([]);
const [isLoading, setIsLoading] = useState(true);
const [error, setError] = useState(null);

const fetch = useCallback(async () => {
  setIsLoading(true);
  setError(null);
  try {
    const data = await runWorkflow('list_clients');
    setClients(data);
  } catch (e) {
    setError(e.message);
  } finally {
    setIsLoading(false);
  }
}, []);

useEffect(() => {
  fetch();
}, [fetch]);

return { clients, isLoading, error, refresh: fetch };
```

### Nested Layout (`pages/settings/_layout`)

```jsx
// This layout wraps all /settings/* pages

return (
  <Row className="h-full">
    <nav className="w-48 border-r p-4">
      <Column gap={1}>
        <Link to="/settings" className="p-2 hover:bg-muted rounded">General</Link>
        <Link to="/settings/billing" className="p-2 hover:bg-muted rounded">Billing</Link>
        <Link to="/settings/team" className="p-2 hover:bg-muted rounded">Team</Link>
      </Column>
    </nav>

    <div className="flex-1 p-6">
      <Outlet />
    </div>
  </Row>
);
```

## TypeScript Support

**Editor:** Monaco provides full TypeScript support — autocomplete, type checking, inline errors. We provide `.d.ts` definitions for platform APIs and components.

**Runtime:** Babel compiles TS → JS. Type annotations are stripped but provide DX benefits in editor.

```typescript
// platform.d.ts (provided by platform)
declare function runWorkflow<T>(id: string, params?: Record<string, unknown>): Promise<T>;
declare function useWorkflow<T>(id: string, params?: Record<string, unknown>): {
  data: T | undefined;
  isLoading: boolean;
  error: string | undefined;
  refresh: () => void;
};
declare function useParams(): Record<string, string>;
declare function useUser(): { id: string; email: string; name: string; role: string; };
// ... etc
```

## Assets

Images and other static assets use external URLs (S3). No special asset handling for v1:

```jsx
<img src="https://cdn.example.com/apps/crm/logo.png" />
```

Future: Could add `assets/` folder with upload API that resolves paths to storage URLs.

## Migration Strategy

### Phase 1: Side-by-Side (Experimental)

1. Add `engine` column to apps (`json` = current, `jsx` = new)
2. Create `app_jsx_files` table
3. Build compiler and runtime
4. Build basic editor (Monaco + preview)
5. Allow creating new JSX apps alongside existing JSON apps

### Phase 2: Stabilization

1. Finalize platform APIs and component library
2. TypeScript definitions and Monaco integration
3. Error boundaries and debugging tools
4. Performance optimization (caching, lazy compilation)
5. Git sync for JSX files

### Phase 3: Adoption

1. Build converter from JSON → JSX (optional, for migration)
2. Documentation and examples
3. Evaluate deprecation of JSON engine based on adoption

## Decisions Made

| Topic | Decision |
|-------|----------|
| Expression language | JavaScript (not custom DSL) |
| State management | Standard React + `useAppState` for cross-page |
| Modals/toasts | Standard React patterns (no special API) |
| File organization | Single table, path-based conventions |
| Layouts | `_layout` files, nested automatically |
| Routing | Path-based with `[param]` for dynamic segments |
| Versioning | Via existing app_versions (draft/published) |
| Third-party packages | Not in v1, future via CDN imports |
| Assets | External URLs (S3) |

## Open Questions

1. **Styling** — Tailwind classes via `className`? Inline styles? Both work, just need to document.
2. **Testing** — How do developers test their JSX? (Probably out of scope for v1)
3. **Permissions** — Should some platform APIs be restricted per-app? (Probably not needed)

---

## Implementation Plan

### Phase 1: Foundation

#### 1.1 Database Schema
- [ ] Add `engine` column to `app_applications` table (default: `'components'`)
- [ ] Create `app_jsx_files` table
- [ ] Create migration

#### 1.2 Backend API
- [ ] Create Pydantic models for JSX files (`JsxFileCreate`, `JsxFileUpdate`, `JsxFileResponse`)
- [ ] Create CRUD endpoints for JSX files:
  - `GET /api/apps/{app_id}/versions/{version_id}/files` — List all files
  - `GET /api/apps/{app_id}/versions/{version_id}/files/{path}` — Get file by path
  - `POST /api/apps/{app_id}/versions/{version_id}/files` — Create file
  - `PATCH /api/apps/{app_id}/versions/{version_id}/files/{path}` — Update file
  - `DELETE /api/apps/{app_id}/versions/{version_id}/files/{path}` — Delete file
- [ ] Update app creation endpoint to accept `engine` type
- [ ] Update publish flow to copy JSX files between versions

#### 1.3 Platform Hooks (Client)
- [ ] Create `client/src/lib/jsx-platform/` directory
- [ ] Implement `runWorkflow(id, params)` — calls workflow execution API
- [ ] Implement `useWorkflow(id, params, options)` — hook wrapper around runWorkflow
- [ ] Implement `useParams()` — wraps react-router's useParams
- [ ] Implement `useSearchParams()` — wraps react-router's useSearchParams
- [ ] Implement `navigate(path)` — wraps react-router's useNavigate
- [ ] Implement `useUser()` — returns current user from auth context
- [ ] Implement `useAppState(key, initial)` — Zustand-backed cross-page state

### Phase 2: Compiler & Runtime

#### 2.1 JSX Compiler
- [ ] Install `@babel/standalone` dependency
- [ ] Create `client/src/lib/jsx-compiler.ts`:
  - `compileJsx(source: string)` — returns `{ success, compiled?, error? }`
  - Configure Babel presets (react, typescript)
- [ ] Create `client/src/lib/jsx-runtime.ts`:
  - Define `PLATFORM_SCOPE` object with all injected APIs
  - `createComponent(compiled, customScope)` — returns React component via `new Function()`

#### 2.2 File Resolver
- [ ] Create `client/src/lib/jsx-resolver.ts`:
  - `resolveFile(appId, versionId, path)` — fetches and compiles a file
  - `resolveImports(source)` — extracts component/module names from JSX
  - Component cache (Map of path → compiled component)
  - `clearCache(appId)` — invalidate on file changes

#### 2.3 Router Builder
- [ ] Create `client/src/lib/jsx-router.ts`:
  - `buildRoutes(files)` — converts file list to react-router route config
  - Handle `_layout` files as parent routes with `<Outlet />`
  - Handle `[param]` segments as `:param`
  - Handle `index` files as index routes

### Phase 3: Runtime App Shell

#### 3.1 JSX App Renderer
- [ ] Create `client/src/components/jsx-app/JsxAppShell.tsx`:
  - Fetches all files for app version
  - Builds router from page files
  - Wraps with `_providers` if exists
  - Renders router with layouts
- [ ] Create `client/src/components/jsx-app/JsxPageRenderer.tsx`:
  - Receives page path
  - Resolves and renders compiled component
  - Error boundary for runtime errors
- [ ] Create `client/src/components/jsx-app/JsxErrorBoundary.tsx`:
  - Catches render errors
  - Shows friendly error with source location

#### 3.2 App Routes
- [ ] Add route for JSX app preview: `/apps/:slug/preview/*`
- [ ] Add route for JSX app published: `/apps/:slug/*`
- [ ] Detect `engine` type and render appropriate shell (components vs jsx)

### Phase 4: Editor UI

#### 4.1 File Tree
- [ ] Create `client/src/components/jsx-editor/FileTree.tsx`:
  - Shows files organized by path
  - Create/rename/delete operations
  - Visual distinction for pages/components/modules

#### 4.2 Monaco Editor
- [ ] Install `@monaco-editor/react` dependency
- [ ] Create `client/src/components/jsx-editor/CodeEditor.tsx`:
  - Monaco with JSX/TSX syntax highlighting
  - TypeScript language service with platform types
  - Auto-save with debounce
  - Compile on save, store compiled output

#### 4.3 Live Preview
- [ ] Create `client/src/components/jsx-editor/LivePreview.tsx`:
  - Renders current file (if page) or test harness (if component)
  - Re-renders on source change
  - Shows compile errors inline

#### 4.4 Editor Layout
- [ ] Create `client/src/pages/apps/jsx-editor/index.tsx`:
  - Three-panel layout: file tree | editor | preview
  - Toolbar: save, publish, view compiled
  - Tabs for multiple open files

### Phase 5: Polish & Integration

#### 5.1 TypeScript Support
- [ ] Create `platform.d.ts` with type definitions for all platform APIs
- [ ] Configure Monaco to use platform types
- [ ] Add types for all shadcn components in scope

#### 5.2 Component Library
- [ ] Create wrapper components in `client/src/components/jsx-ui/`:
  - `Column`, `Row`, `Grid` with convenience props
  - `Heading`, `Text` with level/variant props
  - Re-export shadcn components
- [ ] Export all as single scope object

#### 5.3 Documentation
- [ ] Platform API reference
- [ ] Component library reference
- [ ] Example apps (starter templates)

#### 5.4 Git Sync (if needed)
- [ ] Serialize JSX files to workspace directory
- [ ] File watcher for external changes
- [ ] Conflict resolution

---

## Estimated Effort

| Phase | Effort | Notes |
|-------|--------|-------|
| Phase 1: Foundation | 2-3 days | Schema, API, basic hooks |
| Phase 2: Compiler & Runtime | 2-3 days | Core technical work |
| Phase 3: Runtime App Shell | 2-3 days | Router, rendering, error handling |
| Phase 4: Editor UI | 3-5 days | Most visible work, lots of polish |
| Phase 5: Polish | 2-3 days | TypeScript, docs, cleanup |

**Total: ~2-3 weeks** for a working prototype that can run alongside existing apps.
