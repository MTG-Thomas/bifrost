# App Viewer Window Management Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add minimize/maximize/restore controls to apps in preview and live mode, with a unified dock bar shared between the code editor and app viewer.

**Architecture:** Create a new `appViewerStore` for app viewer state, a shared `window-management` component library for the unified dock and animated overlays, and integrate with existing `JsxAppShell`. The app viewer overlay mounts at the root level (like `EditorOverlay`) so it persists across navigation.

**Tech Stack:** React, Zustand (with persistence), Framer Motion, React Router, TypeScript

---

## Task 1: Create Window Management Types

**Files:**
- Create: `client/src/components/window-management/types.ts`

**Step 1: Create the types file**

```typescript
// client/src/components/window-management/types.ts

/**
 * Shared types for window management (dock, overlays)
 */

export interface DockItem {
  /** Unique identifier for the dock item */
  id: string;
  /** Icon to display (React node) */
  icon: React.ReactNode;
  /** Label to display */
  label: string;
  /** Whether this item has activity in progress (shows spinner) */
  isLoading?: boolean;
  /** Callback when item is clicked to restore */
  onRestore: () => void;
}

export type OverlayLayoutMode = "maximized" | "minimized" | null;
```

**Step 2: Verify file created correctly**

Run: `cat client/src/components/window-management/types.ts`
Expected: File contents shown

**Step 3: Commit**

```bash
git add client/src/components/window-management/types.ts
git commit -m "feat(window-management): add shared types for dock and overlay"
```

---

## Task 2: Create Animation Variants

**Files:**
- Create: `client/src/components/window-management/animations.ts`

**Step 1: Create the animations file**

```typescript
// client/src/components/window-management/animations.ts

/**
 * Framer Motion animation variants for window management
 */

import type { Variants, Transition } from "framer-motion";

/** Standard easing for window animations */
export const windowTransition: Transition = {
  duration: 0.2,
  ease: "easeOut",
};

/** Overlay animation variants (maximize/minimize) */
export const overlayVariants: Variants = {
  hidden: {
    opacity: 0,
    scale: 0.95,
  },
  visible: {
    opacity: 1,
    scale: 1,
  },
};

/** Dock bar animation variants (slide up from bottom) */
export const dockVariants: Variants = {
  hidden: {
    opacity: 0,
    y: 20,
  },
  visible: {
    opacity: 1,
    y: 0,
  },
};

/** Dock item animation variants (for AnimatePresence) */
export const dockItemVariants: Variants = {
  hidden: {
    opacity: 0,
    scale: 0.8,
    x: 20,
  },
  visible: {
    opacity: 1,
    scale: 1,
    x: 0,
  },
  exit: {
    opacity: 0,
    scale: 0.8,
    x: -20,
  },
};
```

**Step 2: Verify file created correctly**

Run: `cat client/src/components/window-management/animations.ts`
Expected: File contents shown

**Step 3: Commit**

```bash
git add client/src/components/window-management/animations.ts
git commit -m "feat(window-management): add Framer Motion animation variants"
```

---

## Task 3: Create WindowDockItem Component

**Files:**
- Create: `client/src/components/window-management/WindowDockItem.tsx`

**Step 1: Create the dock item component**

```typescript
// client/src/components/window-management/WindowDockItem.tsx

import { motion } from "framer-motion";
import { Loader2 } from "lucide-react";
import { dockItemVariants, windowTransition } from "./animations";
import type { DockItem } from "./types";

interface WindowDockItemProps extends DockItem {}

/**
 * Individual item in the window dock bar.
 * Shows icon, label, and loading state.
 */
export function WindowDockItem({
  id,
  icon,
  label,
  isLoading,
  onRestore,
}: WindowDockItemProps) {
  return (
    <motion.button
      layout
      variants={dockItemVariants}
      initial="hidden"
      animate="visible"
      exit="exit"
      transition={windowTransition}
      onClick={onRestore}
      className="flex items-center gap-2 rounded-lg border bg-background px-3 py-2 shadow-lg hover:bg-muted hover:scale-[1.02] transition-all duration-150"
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.98 }}
    >
      {isLoading ? (
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      ) : (
        <span className="h-4 w-4 flex items-center justify-center text-muted-foreground">
          {icon}
        </span>
      )}
      <span className="text-sm font-medium truncate max-w-[150px]">
        {label}
      </span>
    </motion.button>
  );
}
```

**Step 2: Verify file created correctly**

Run: `cat client/src/components/window-management/WindowDockItem.tsx`
Expected: File contents shown

**Step 3: Commit**

```bash
git add client/src/components/window-management/WindowDockItem.tsx
git commit -m "feat(window-management): add WindowDockItem component"
```

---

## Task 4: Create WindowDock Component

**Files:**
- Create: `client/src/components/window-management/WindowDock.tsx`

**Step 1: Create the dock component**

```typescript
// client/src/components/window-management/WindowDock.tsx

import { AnimatePresence, motion } from "framer-motion";
import { dockVariants, windowTransition } from "./animations";
import { WindowDockItem } from "./WindowDockItem";
import type { DockItem } from "./types";

interface WindowDockProps {
  /** Items to display in the dock */
  items: DockItem[];
}

/**
 * Unified dock bar for all minimized windows.
 * Appears fixed at bottom-right when any items are present.
 */
export function WindowDock({ items }: WindowDockProps) {
  if (items.length === 0) {
    return null;
  }

  return (
    <motion.div
      className="fixed bottom-4 right-4 z-50 flex gap-2"
      variants={dockVariants}
      initial="hidden"
      animate="visible"
      transition={windowTransition}
    >
      <AnimatePresence mode="popLayout">
        {items.map((item) => (
          <WindowDockItem key={item.id} {...item} />
        ))}
      </AnimatePresence>
    </motion.div>
  );
}
```

**Step 2: Verify file created correctly**

Run: `cat client/src/components/window-management/WindowDock.tsx`
Expected: File contents shown

**Step 3: Commit**

```bash
git add client/src/components/window-management/WindowDock.tsx
git commit -m "feat(window-management): add WindowDock component"
```

---

## Task 5: Create WindowOverlay Component

**Files:**
- Create: `client/src/components/window-management/WindowOverlay.tsx`

**Step 1: Create the overlay component**

```typescript
// client/src/components/window-management/WindowOverlay.tsx

import { motion } from "framer-motion";
import { overlayVariants, windowTransition } from "./animations";

interface WindowOverlayProps {
  children: React.ReactNode;
}

/**
 * Animated fullscreen overlay wrapper for maximized windows.
 * Provides consistent enter/exit animations.
 */
export function WindowOverlay({ children }: WindowOverlayProps) {
  return (
    <motion.div
      className="fixed inset-0 z-50 bg-background"
      variants={overlayVariants}
      initial="hidden"
      animate="visible"
      exit="hidden"
      transition={windowTransition}
      style={{ originX: 1, originY: 1 }}
    >
      {children}
    </motion.div>
  );
}
```

**Step 2: Verify file created correctly**

Run: `cat client/src/components/window-management/WindowOverlay.tsx`
Expected: File contents shown

**Step 3: Commit**

```bash
git add client/src/components/window-management/WindowOverlay.tsx
git commit -m "feat(window-management): add WindowOverlay component"
```

---

## Task 6: Create Window Management Index

**Files:**
- Create: `client/src/components/window-management/index.ts`

**Step 1: Create the index file**

```typescript
// client/src/components/window-management/index.ts

export { WindowDock } from "./WindowDock";
export { WindowDockItem } from "./WindowDockItem";
export { WindowOverlay } from "./WindowOverlay";
export * from "./animations";
export * from "./types";
```

**Step 2: Verify file created correctly**

Run: `cat client/src/components/window-management/index.ts`
Expected: File contents shown

**Step 3: Commit**

```bash
git add client/src/components/window-management/index.ts
git commit -m "feat(window-management): add index exports"
```

---

## Task 7: Create App Viewer Store

**Files:**
- Create: `client/src/stores/appViewerStore.ts`

**Step 1: Create the store**

```typescript
// client/src/stores/appViewerStore.ts

import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * App Viewer state store using Zustand with persistence.
 * Manages the app viewer overlay state for minimize/maximize/restore.
 */

export type AppViewerLayoutMode = "maximized" | "minimized" | null;

interface AppViewerState {
  // App identity
  appId: string | null;
  appSlug: string | null;
  appName: string | null;
  versionId: string | null;
  isPreview: boolean;

  // Display mode (null = windowed/not in overlay mode)
  layoutMode: AppViewerLayoutMode;

  // Navigation memory
  returnToPath: string | null;

  // Internal app route (e.g., "/dashboard")
  internalRoute: string;

  // Actions
  openApp: (params: {
    appId: string;
    appSlug: string;
    appName: string;
    versionId: string;
    isPreview: boolean;
  }) => void;
  maximize: (currentPath: string) => void;
  minimize: () => void;
  restoreToWindowed: () => string; // Returns the app route to navigate to
  closeApp: () => string | null; // Returns returnToPath if was in overlay
  setInternalRoute: (route: string) => void;
  hydrateFromRoute: (params: {
    appId: string;
    appSlug: string;
    appName: string;
    versionId: string;
    isPreview: boolean;
  }) => void;
}

export const useAppViewerStore = create<AppViewerState>()(
  persist(
    (set, get) => ({
      // Initial state
      appId: null,
      appSlug: null,
      appName: null,
      versionId: null,
      isPreview: false,
      layoutMode: null,
      returnToPath: null,
      internalRoute: "/",

      // Open an app (called when navigating to app route)
      openApp: ({ appId, appSlug, appName, versionId, isPreview }) =>
        set({
          appId,
          appSlug,
          appName,
          versionId,
          isPreview,
          layoutMode: null, // Start in windowed mode
          returnToPath: null,
          internalRoute: "/",
        }),

      // Maximize the app (overlay mode)
      maximize: (currentPath) =>
        set({
          layoutMode: "maximized",
          returnToPath: currentPath,
        }),

      // Minimize to dock
      minimize: () =>
        set({
          layoutMode: "minimized",
        }),

      // Restore to windowed mode (navigate to app route)
      restoreToWindowed: () => {
        const { appSlug, isPreview } = get();
        set({
          layoutMode: null,
          returnToPath: null,
        });
        return isPreview ? `/apps/${appSlug}/preview` : `/apps/${appSlug}`;
      },

      // Close the app entirely
      closeApp: () => {
        const { returnToPath, layoutMode } = get();
        const pathToReturn = layoutMode ? returnToPath : null;
        set({
          appId: null,
          appSlug: null,
          appName: null,
          versionId: null,
          isPreview: false,
          layoutMode: null,
          returnToPath: null,
          internalRoute: "/",
        });
        return pathToReturn;
      },

      // Update internal route (for display in header)
      setInternalRoute: (route) =>
        set({
          internalRoute: route,
        }),

      // Hydrate store when landing directly on app route
      hydrateFromRoute: ({ appId, appSlug, appName, versionId, isPreview }) => {
        const state = get();
        // Only hydrate if not already tracking this app
        if (state.appId !== appId) {
          set({
            appId,
            appSlug,
            appName,
            versionId,
            isPreview,
            layoutMode: null,
            returnToPath: null,
            internalRoute: "/",
          });
        }
      },
    }),
    {
      name: "app-viewer-storage",
      partialize: (state) => ({
        appId: state.appId,
        appSlug: state.appSlug,
        appName: state.appName,
        versionId: state.versionId,
        isPreview: state.isPreview,
        layoutMode: state.layoutMode,
        returnToPath: state.returnToPath,
        internalRoute: state.internalRoute,
      }),
    }
  )
);
```

**Step 2: Verify file created correctly**

Run: `cat client/src/stores/appViewerStore.ts`
Expected: File contents shown

**Step 3: Commit**

```bash
git add client/src/stores/appViewerStore.ts
git commit -m "feat(app-viewer): add appViewerStore for window state management"
```

---

## Task 8: Create useAppViewer Hook

**Files:**
- Create: `client/src/hooks/useAppViewer.ts`

**Step 1: Create the hook**

```typescript
// client/src/hooks/useAppViewer.ts

import { useShallow } from "zustand/react/shallow";
import { useNavigate, useLocation } from "react-router-dom";
import { useCallback } from "react";
import { useAppViewerStore } from "@/stores/appViewerStore";

/**
 * Hook for accessing app viewer state with navigation actions.
 * Wraps store actions with router navigation.
 */
export function useAppViewer() {
  const navigate = useNavigate();
  const location = useLocation();

  const store = useAppViewerStore(
    useShallow((state) => ({
      appId: state.appId,
      appSlug: state.appSlug,
      appName: state.appName,
      versionId: state.versionId,
      isPreview: state.isPreview,
      layoutMode: state.layoutMode,
      returnToPath: state.returnToPath,
      internalRoute: state.internalRoute,
      openApp: state.openApp,
      maximize: state.maximize,
      minimize: state.minimize,
      restoreToWindowed: state.restoreToWindowed,
      closeApp: state.closeApp,
      setInternalRoute: state.setInternalRoute,
      hydrateFromRoute: state.hydrateFromRoute,
    }))
  );

  // Maximize with navigation
  const handleMaximize = useCallback(() => {
    store.maximize(location.pathname);
    if (store.returnToPath) {
      navigate(store.returnToPath);
    }
  }, [store, location.pathname, navigate]);

  // Restore to windowed with navigation
  const handleRestoreToWindowed = useCallback(() => {
    const appRoute = store.restoreToWindowed();
    navigate(appRoute);
  }, [store, navigate]);

  // Close with navigation
  const handleClose = useCallback(() => {
    const returnPath = store.closeApp();
    if (returnPath) {
      navigate(returnPath);
    }
  }, [store, navigate]);

  // Restore from minimized (go to maximized)
  const handleRestoreFromDock = useCallback(() => {
    useAppViewerStore.setState({ layoutMode: "maximized" });
  }, []);

  return {
    // State
    appId: store.appId,
    appSlug: store.appSlug,
    appName: store.appName,
    versionId: store.versionId,
    isPreview: store.isPreview,
    layoutMode: store.layoutMode,
    returnToPath: store.returnToPath,
    internalRoute: store.internalRoute,

    // Actions
    openApp: store.openApp,
    maximize: handleMaximize,
    minimize: store.minimize,
    restoreToWindowed: handleRestoreToWindowed,
    restoreFromDock: handleRestoreFromDock,
    closeApp: handleClose,
    setInternalRoute: store.setInternalRoute,
    hydrateFromRoute: store.hydrateFromRoute,
  };
}

/**
 * Lightweight hook for components that only need visibility state.
 */
export function useAppViewerVisibility() {
  return useAppViewerStore(
    useShallow((state) => ({
      appId: state.appId,
      appName: state.appName,
      layoutMode: state.layoutMode,
      isPreview: state.isPreview,
    }))
  );
}
```

**Step 2: Verify file created correctly**

Run: `cat client/src/hooks/useAppViewer.ts`
Expected: File contents shown

**Step 3: Commit**

```bash
git add client/src/hooks/useAppViewer.ts
git commit -m "feat(app-viewer): add useAppViewer hook with navigation"
```

---

## Task 9: Create AppViewerLayout Component

**Files:**
- Create: `client/src/components/app-viewer/AppViewerLayout.tsx`

**Step 1: Create the layout component**

```typescript
// client/src/components/app-viewer/AppViewerLayout.tsx

import { AppWindow, Minus, PictureInPicture2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { JsxAppShell } from "@/components/jsx-app/JsxAppShell";
import { useAppViewer } from "@/hooks/useAppViewer";

/**
 * App viewer layout with header bar and controls.
 * Rendered inside WindowOverlay when maximized.
 */
export function AppViewerLayout() {
  const {
    appId,
    appSlug,
    appName,
    versionId,
    isPreview,
    internalRoute,
    minimize,
    restoreToWindowed,
    closeApp,
  } = useAppViewer();

  if (!appId || !appSlug || !versionId) {
    return null;
  }

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background">
      {/* Header bar */}
      <div className="flex h-10 items-center justify-between border-b bg-muted/30 px-3 shrink-0">
        {/* Left side: App info */}
        <div className="flex items-center gap-2 min-w-0">
          <AppWindow className="h-4 w-4 text-muted-foreground shrink-0" />
          <span className="text-sm font-medium truncate">{appName}</span>
          <span className="text-sm text-muted-foreground truncate">
            {internalRoute}
          </span>
          {isPreview && (
            <Badge variant="outline" className="bg-amber-500/10 text-amber-600 border-amber-500/30 shrink-0">
              Preview
            </Badge>
          )}
        </div>

        {/* Right side: Controls */}
        <div className="flex gap-1 shrink-0">
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={minimize}
            title="Minimize"
          >
            <Minus className="h-3 w-3" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={restoreToWindowed}
            title="Restore to window"
          >
            <PictureInPicture2 className="h-3 w-3" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={closeApp}
            title="Close"
          >
            <X className="h-3 w-3" />
          </Button>
        </div>
      </div>

      {/* App content */}
      <div className="flex-1 overflow-auto">
        <JsxAppShell
          appId={appId}
          appSlug={appSlug}
          versionId={versionId}
          isPreview={isPreview}
        />
      </div>
    </div>
  );
}
```

**Step 2: Verify file created correctly**

Run: `cat client/src/components/app-viewer/AppViewerLayout.tsx`
Expected: File contents shown

**Step 3: Commit**

```bash
git add client/src/components/app-viewer/AppViewerLayout.tsx
git commit -m "feat(app-viewer): add AppViewerLayout with header controls"
```

---

## Task 10: Create AppViewerOverlay Component

**Files:**
- Create: `client/src/components/app-viewer/AppViewerOverlay.tsx`

**Step 1: Create the overlay component**

```typescript
// client/src/components/app-viewer/AppViewerOverlay.tsx

import { AnimatePresence } from "framer-motion";
import { WindowOverlay } from "@/components/window-management";
import { AppViewerLayout } from "./AppViewerLayout";
import { useAppViewerVisibility } from "@/hooks/useAppViewer";

/**
 * App viewer overlay component.
 * Renders the app as a fullscreen overlay when maximized.
 * Mounted at root level in App.tsx.
 */
export function AppViewerOverlay() {
  const { appId, layoutMode } = useAppViewerVisibility();

  // Not active or minimized (dock handles minimized state)
  if (!appId || !layoutMode || layoutMode === "minimized") {
    return null;
  }

  return (
    <AnimatePresence>
      {layoutMode === "maximized" && (
        <WindowOverlay>
          <AppViewerLayout />
        </WindowOverlay>
      )}
    </AnimatePresence>
  );
}
```

**Step 2: Verify file created correctly**

Run: `cat client/src/components/app-viewer/AppViewerOverlay.tsx`
Expected: File contents shown

**Step 3: Commit**

```bash
git add client/src/components/app-viewer/AppViewerOverlay.tsx
git commit -m "feat(app-viewer): add AppViewerOverlay component"
```

---

## Task 11: Create App Viewer Index

**Files:**
- Create: `client/src/components/app-viewer/index.ts`

**Step 1: Create the index file**

```typescript
// client/src/components/app-viewer/index.ts

export { AppViewerOverlay } from "./AppViewerOverlay";
export { AppViewerLayout } from "./AppViewerLayout";
```

**Step 2: Verify file created correctly**

Run: `cat client/src/components/app-viewer/index.ts`
Expected: File contents shown

**Step 3: Commit**

```bash
git add client/src/components/app-viewer/index.ts
git commit -m "feat(app-viewer): add index exports"
```

---

## Task 12: Update EditorLayout to Use Shared Dock

**Files:**
- Modify: `client/src/components/editor/EditorLayout.tsx`

**Step 1: Identify the dock button code to remove**

The current dock button is in lines 201-219. We need to remove it since the unified dock will handle this.

**Step 2: Update the minimized return to return null**

Find and replace the minimized block (lines 201-220):

```typescript
// BEFORE (lines 201-220):
// If minimized, show docked bar
if (layoutMode === "minimized") {
  return (
    <div className="fixed bottom-4 right-4 z-50">
      <button
        onClick={restoreEditor}
        className="flex items-center gap-2 rounded-lg border bg-background px-4 py-2 shadow-lg hover:bg-muted transition-colors"
      >
        {isActivityInProgress ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Maximize2 className="h-4 w-4" />
        )}
        <span className="text-sm font-medium">
          {getMinimizedLabel()}
        </span>
      </button>
    </div>
  );
}

// AFTER:
// If minimized, don't render anything - the unified WindowDock handles this
if (layoutMode === "minimized") {
  return null;
}
```

**Step 3: Verify changes**

Run: `grep -A 5 'layoutMode === "minimized"' client/src/components/editor/EditorLayout.tsx`
Expected: Shows `return null;`

**Step 4: Commit**

```bash
git add client/src/components/editor/EditorLayout.tsx
git commit -m "refactor(editor): remove inline dock button, use shared WindowDock"
```

---

## Task 13: Create Unified Dock Integration Component

**Files:**
- Create: `client/src/components/layout/UnifiedDock.tsx`

**Step 1: Create the unified dock component**

```typescript
// client/src/components/layout/UnifiedDock.tsx

import { Code, AppWindow } from "lucide-react";
import { WindowDock, type DockItem } from "@/components/window-management";
import { useEditorStore } from "@/stores/editorStore";
import { useAppViewerStore } from "@/stores/appViewerStore";
import { useUploadStore } from "@/stores/uploadStore";
import { useExecutionStreamStore } from "@/stores/executionStreamStore";

/**
 * Unified dock that aggregates all minimized windows.
 * Renders both editor and app viewer dock items.
 */
export function UnifiedDock() {
  // Editor state
  const editorIsOpen = useEditorStore((state) => state.isOpen);
  const editorLayoutMode = useEditorStore((state) => state.layoutMode);
  const editorActiveTab = useEditorStore((state) => {
    const idx = state.activeTabIndex;
    return idx >= 0 && idx < state.tabs.length ? state.tabs[idx] : null;
  });
  const editorSidebarPanel = useEditorStore((state) => state.sidebarPanel);
  const restoreEditor = useEditorStore((state) => state.restoreEditor);

  // Editor activity state
  const isUploading = useUploadStore((state) => state.isUploading);
  const streams = useExecutionStreamStore((state) => state.streams);
  const hasActiveExecution = Object.values(streams).some(
    (s) => s.status === "Running" || s.status === "Pending"
  );
  const editorIsLoading = isUploading || hasActiveExecution;

  // App viewer state
  const appId = useAppViewerStore((state) => state.appId);
  const appName = useAppViewerStore((state) => state.appName);
  const appLayoutMode = useAppViewerStore((state) => state.layoutMode);
  const appIsPreview = useAppViewerStore((state) => state.isPreview);

  // Build dock items
  const items: DockItem[] = [];

  // Add app viewer if minimized
  if (appId && appLayoutMode === "minimized") {
    items.push({
      id: `app-${appId}`,
      icon: <AppWindow className="h-4 w-4" />,
      label: appIsPreview ? `${appName} (Preview)` : appName || "App",
      isLoading: false,
      onRestore: () => {
        useAppViewerStore.setState({ layoutMode: "maximized" });
      },
    });
  }

  // Add editor if minimized
  if (editorIsOpen && editorLayoutMode === "minimized") {
    // Get label for minimized editor
    let editorLabel = "Editor";
    if (editorActiveTab?.file) {
      editorLabel = editorActiveTab.file.name;
    } else if (editorSidebarPanel === "files") {
      editorLabel = "File Browser";
    } else if (editorSidebarPanel === "search") {
      editorLabel = "Search";
    } else if (editorSidebarPanel === "sourceControl") {
      editorLabel = "Source Control";
    } else if (editorSidebarPanel === "run") {
      editorLabel = "Execute";
    } else if (editorSidebarPanel === "packages") {
      editorLabel = "Packages";
    }

    items.push({
      id: "editor",
      icon: <Code className="h-4 w-4" />,
      label: editorLabel,
      isLoading: editorIsLoading,
      onRestore: restoreEditor,
    });
  }

  return <WindowDock items={items} />;
}
```

**Step 2: Verify file created correctly**

Run: `cat client/src/components/layout/UnifiedDock.tsx`
Expected: File contents shown

**Step 3: Commit**

```bash
git add client/src/components/layout/UnifiedDock.tsx
git commit -m "feat(layout): add UnifiedDock component for all minimized windows"
```

---

## Task 14: Update App.tsx to Include Overlays and Dock

**Files:**
- Modify: `client/src/App.tsx`

**Step 1: Add imports at the top of the file**

Add after the existing imports (around line 11):

```typescript
import { AppViewerOverlay } from "@/components/app-viewer";
import { UnifiedDock } from "@/components/layout/UnifiedDock";
```

**Step 2: Add components after EditorOverlay**

Find the `<EditorOverlay />` line (around line 201) and add after it:

```typescript
{/* Editor Overlay - Rendered globally on top of all pages */}
<EditorOverlay />

{/* App Viewer Overlay - Rendered globally for maximized apps */}
<AppViewerOverlay />

{/* Unified Dock - Shows all minimized windows */}
<UnifiedDock />
```

**Step 3: Run type check**

Run: `cd client && npm run tsc`
Expected: No errors

**Step 4: Commit**

```bash
git add client/src/App.tsx
git commit -m "feat(app): integrate AppViewerOverlay and UnifiedDock"
```

---

## Task 15: Update AppRouter to Support Maximize and Hydrate Store

**Files:**
- Modify: `client/src/pages/AppRouter.tsx`

**Step 1: Add imports**

Add at the top with other imports:

```typescript
import { useEffect } from "react";
import { Minus, Maximize2 } from "lucide-react";
import { useAppViewerStore } from "@/stores/appViewerStore";
```

**Step 2: Add store hydration and maximize button**

Update the `AppRouter` component. After getting the `versionId` (around line 107-109), add:

```typescript
// Get the appropriate version ID
const versionId = preview
  ? application.draft_version_id
  : application.active_version_id;

// Hydrate app viewer store for minimize/maximize support
useEffect(() => {
  if (application && versionId) {
    useAppViewerStore.getState().hydrateFromRoute({
      appId: application.id,
      appSlug: application.slug,
      appName: application.name,
      versionId,
      isPreview: preview,
    });
  }
}, [application, versionId, preview]);

// Get maximize action
const maximize = useAppViewerStore((state) => state.maximize);
const location = useLocation();

const handleMaximize = () => {
  // Store current path and go to a neutral location
  maximize(location.pathname);
  navigate("/"); // or wherever makes sense as "home"
};
```

**Step 3: Add maximize button to preview banner**

Update the preview banner section (around lines 151-175) to include a maximize button:

```typescript
// Render with maximize button and preview banner if in preview mode
if (preview) {
  return (
    <div className="h-full flex flex-col bg-background overflow-hidden">
      {/* Header bar with controls */}
      <div className="z-50 bg-amber-500 text-amber-950 px-4 py-2 text-center text-sm font-medium shrink-0 flex items-center justify-between">
        <div className="flex-1" />
        <span>
          Preview Mode - This is the draft version
          <Button
            variant="link"
            className="ml-2 text-amber-950 underline hover:no-underline p-0 h-auto"
            onClick={() => navigate(`/apps/${slugParam}/code`)}
          >
            Back to Editor
          </Button>
        </span>
        <div className="flex-1 flex justify-end">
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-amber-950 hover:bg-amber-600/20"
            onClick={handleMaximize}
            title="Maximize"
          >
            <Maximize2 className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <div className="flex-1 overflow-auto">
        <JsxAppShell
          appId={application.id}
          appSlug={application.slug}
          versionId={versionId}
          isPreview
        />
      </div>
    </div>
  );
}

// Production mode with maximize button
return (
  <div className="h-full flex flex-col bg-background overflow-hidden">
    {/* Minimal header with maximize */}
    <div className="z-50 border-b bg-muted/30 px-4 py-1 flex items-center justify-end shrink-0">
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6"
        onClick={handleMaximize}
        title="Maximize"
      >
        <Maximize2 className="h-4 w-4" />
      </Button>
    </div>
    <div className="flex-1 overflow-auto">
      <JsxAppShell
        appId={application.id}
        appSlug={application.slug}
        versionId={versionId}
      />
    </div>
  </div>
);
```

**Step 4: Add useLocation import**

Make sure `useLocation` is imported from react-router-dom at the top.

**Step 5: Run type check**

Run: `cd client && npm run tsc`
Expected: No errors

**Step 6: Commit**

```bash
git add client/src/pages/AppRouter.tsx
git commit -m "feat(app-router): add maximize button and store hydration"
```

---

## Task 16: Add Animations to EditorOverlay

**Files:**
- Modify: `client/src/components/editor/EditorOverlay.tsx`

**Step 1: Update with AnimatePresence and WindowOverlay**

Replace the entire file:

```typescript
// client/src/components/editor/EditorOverlay.tsx

import { AnimatePresence } from "framer-motion";
import { useEditorStore } from "@/stores/editorStore";
import { useAuth } from "@/contexts/AuthContext";
import { EditorLayout } from "./EditorLayout";
import { WindowOverlay } from "@/components/window-management";

/**
 * Editor overlay component
 * Renders the editor as a fullscreen overlay on top of the current page
 * Only visible when isOpen is true and user is a platform admin
 * When minimized, returns null - the unified dock handles the minimized state
 */
export function EditorOverlay() {
  const isOpen = useEditorStore((state) => state.isOpen);
  const layoutMode = useEditorStore((state) => state.layoutMode);
  const { isPlatformAdmin } = useAuth();

  if (!isOpen || !isPlatformAdmin) {
    return null;
  }

  // If minimized, don't render - unified dock handles this
  if (layoutMode === "minimized") {
    return null;
  }

  return (
    <AnimatePresence>
      {layoutMode === "fullscreen" && (
        <WindowOverlay>
          <EditorLayout />
        </WindowOverlay>
      )}
    </AnimatePresence>
  );
}
```

**Step 2: Run type check**

Run: `cd client && npm run tsc`
Expected: No errors

**Step 3: Commit**

```bash
git add client/src/components/editor/EditorOverlay.tsx
git commit -m "feat(editor): add animations using WindowOverlay"
```

---

## Task 17: Run Full Type Check and Lint

**Files:**
- None (verification only)

**Step 1: Run TypeScript type check**

Run: `cd client && npm run tsc`
Expected: No errors

**Step 2: Run linter**

Run: `cd client && npm run lint`
Expected: No errors (or only pre-existing ones)

**Step 3: Fix any issues if needed**

If there are errors, fix them before proceeding.

---

## Task 18: Manual Testing Checklist

**Files:**
- None (testing only)

**Step 1: Start the dev stack**

Run: `./debug.sh`
Expected: All services start

**Step 2: Test code editor dock**

1. Open code editor (Cmd+/)
2. Minimize it (click - button)
3. Verify dock appears at bottom-right with animation
4. Click dock item to restore
5. Verify overlay animates back

**Step 3: Test app viewer (if apps exist)**

1. Navigate to `/apps`
2. Click on an app to open it (windowed mode)
3. Click maximize button
4. Verify URL changes, app shows as overlay
5. Click minimize
6. Verify dock shows app item
7. Click dock to restore to maximized
8. Click restore (PictureInPicture icon) to go back to windowed
9. Verify URL changes back to app route

**Step 4: Test both minimized together**

1. Open code editor and minimize
2. Open an app and minimize
3. Verify both appear in dock
4. Click each to restore independently

---

## Task 19: Final Commit

**Files:**
- None

**Step 1: Create summary commit if needed**

If all tests pass and everything works:

```bash
git add -A
git commit -m "feat: complete app viewer window management with unified dock

- Add window-management component library (dock, overlay, animations)
- Add appViewerStore for app viewer state
- Add AppViewerOverlay and AppViewerLayout components
- Add UnifiedDock that shows both editor and app viewer
- Update EditorOverlay to use shared WindowOverlay with animations
- Update AppRouter with maximize button and store hydration

Closes #XXX (if applicable)"
```

---

## Summary

This implementation plan covers:

1. **Tasks 1-6**: Window management component library (types, animations, dock, overlay)
2. **Tasks 7-8**: App viewer store and hook
3. **Tasks 9-11**: App viewer components (layout, overlay)
4. **Task 12**: Update EditorLayout to remove inline dock
5. **Task 13**: UnifiedDock component
6. **Task 14**: App.tsx integration
7. **Task 15**: AppRouter maximize support
8. **Task 16**: EditorOverlay animations
9. **Tasks 17-19**: Verification and testing

Each task is a small, focused change that can be completed and committed independently.
