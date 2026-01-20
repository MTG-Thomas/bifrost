# App Viewer Window Management

Add minimize/maximize/restore controls to apps in preview and live mode, allowing users to keep apps open while navigating elsewhere.

## Overview

Apps can be minimized to a dock (like the code editor), maximized to overlay the viewport, or viewed in normal windowed mode at their route. The user's previous page stays mounted when maximized/minimized, preserving state.

## Display Modes

| Mode | Visual | URL |
|------|--------|-----|
| **Windowed** | Normal app view, full page | `/apps/{slug}` or `/apps/{slug}/preview` |
| **Maximized** | App overlays viewport, previous page hidden but mounted | Whatever page you were on (e.g., `/chat`) |
| **Minimized** | Dock button visible, app hidden but mounted | Whatever page you're on (e.g., `/chat`) |

## User Flows

### Open → Maximize → Minimize → Restore

1. User is on `/chat` working on something
2. They open an app → navigates to `/apps/my-app` (windowed)
3. They click **maximize** → URL changes back to `/chat`, app overlays viewport, chat stays mounted
4. They click **minimize** → app shrinks to dock, chat visible, still on `/chat`
5. They click the dock → back to maximized overlay
6. They click **restore** → navigates to `/apps/my-app`, normal app view

### Navigation Actions

| Action | From | To | URL Change |
|--------|------|-----|------------|
| Open app | `/chat` | Windowed | → `/apps/my-app` |
| Maximize | Windowed | Maximized | → back to `returnToPath` |
| Minimize | Maximized | Minimized | No change |
| Click dock | Minimized | Maximized | No change |
| Restore | Maximized/Minimized | Windowed | → `/apps/my-app` |
| Close | Any | Closed | → `returnToPath` if overlay, else stay |

## Store Design

### `appViewerStore.ts`

```typescript
interface AppViewerState {
  // App identity
  appId: string | null;
  appSlug: string | null;
  versionId: string | null;
  isPreview: boolean;

  // Display mode (null = windowed/not in overlay)
  layoutMode: "maximized" | "minimized" | null;

  // Navigation memory
  returnToPath: string | null;

  // Internal app state
  internalRoute: string;
}
```

### Actions

| Action | Description |
|--------|-------------|
| `openApp(slug, versionId, isPreview)` | Initialize app state |
| `maximize()` | Set `layoutMode: "maximized"`, capture `returnToPath`, navigate back |
| `minimize()` | Set `layoutMode: "minimized"` |
| `restoreToWindowed()` | Set `layoutMode: null`, navigate to app route |
| `closeApp()` | Clear state, navigate to `returnToPath` |
| `setInternalRoute(path)` | Track current page within app |

## Component Structure

### New Components

```
components/window-management/
├── WindowDock.tsx              # Unified dock bar (all minimized items)
├── WindowDockItem.tsx          # Individual dock item
├── WindowOverlay.tsx           # Animated overlay wrapper
├── animations.ts               # Framer Motion variants
├── types.ts                    # Shared types
└── index.ts

components/app-viewer/
├── AppViewerOverlay.tsx        # Root overlay (mounted in App.tsx)
├── AppViewerLayout.tsx         # Header bar + JsxAppShell
└── index.ts
```

### `AppViewerOverlay.tsx`

```tsx
export function AppViewerOverlay() {
  const { appId, layoutMode } = useAppViewerStore();

  if (!appId || !layoutMode) return null;

  if (layoutMode === "minimized") {
    return null; // Dock handles this
  }

  return (
    <WindowOverlay>
      <AppViewerLayout />
    </WindowOverlay>
  );
}
```

### `AppViewerLayout.tsx`

Header bar with controls + JsxAppShell:

```
┌──────────────────────────────────────────────────────────────┐
│ 📱 My App  /dashboard  [Preview]             [−] [⧉] [×]    │
├──────────────────────────────────────────────────────────────┤
│                     App Content (JsxAppShell)                │
└──────────────────────────────────────────────────────────────┘
```

**Header elements:**
- Left: App icon (`AppWindow`), app name, internal route, preview badge (if applicable)
- Right: Minimize (`Minus`), Restore (`PictureInPicture2`), Close (`X`)

### Unified Dock

One dock bar for all minimized windows:

```
                    ┌─────────────────────────────┐
                    │ 📱 My App  │  📝 main.py    │
                    └─────────────────────────────┘
                                            bottom-4 right-4
```

**Dock items:**
| Item | Icon | Label |
|------|------|-------|
| Code Editor | `Code` | Filename or "Editor" |
| App (preview) | `AppWindow` | App name |
| App (live) | `AppWindow` | App name |

## Animations

Using Framer Motion for all transitions:

| Transition | Animation |
|------------|-----------|
| Maximize (dock → fullscreen) | Scale up from dock + fade in (200ms ease-out) |
| Minimize (fullscreen → dock) | Scale down to dock + fade out (200ms ease-in) |
| Dock appear | Slide up from bottom + fade in (150ms) |
| Dock hover | Subtle scale (1.02) + shadow increase |
| Dock item enter/exit | Slide + fade |

```tsx
// Overlay animation example
<motion.div
  initial={{ opacity: 0, scale: 0.95, originX: 1, originY: 1 }}
  animate={{ opacity: 1, scale: 1 }}
  exit={{ opacity: 0, scale: 0.95 }}
  transition={{ duration: 0.2, ease: "easeOut" }}
/>
```

## Entry Points

### Opening Apps

| Location | Action |
|----------|--------|
| "View" button in code editor | `openApp(slug, versionId, true)` + navigate |
| App card "Open" button | `openApp(slug, versionId, false)` + navigate |
| Direct URL `/apps/{slug}` | Hydrate store on `AppRouter` mount |

### Store Hydration

When `AppRouter` mounts at `/apps/{slug}`, hydrate the store if empty so minimize/maximize controls work even from direct URL access.

## Files to Modify

| File | Changes |
|------|---------|
| `App.tsx` | Add `<AppViewerOverlay />` and `<WindowDock />` |
| `EditorLayout.tsx` | Remove inline dock button, use shared dock |
| `EditorOverlay.tsx` | Use shared `WindowOverlay` for animations |
| `editorStore.ts` | Expose state for dock integration |
| `AppRouter.tsx` | Hydrate store, add maximize button in windowed mode |

## New Files

```
stores/appViewerStore.ts
components/window-management/WindowDock.tsx
components/window-management/WindowDockItem.tsx
components/window-management/WindowOverlay.tsx
components/window-management/animations.ts
components/window-management/types.ts
components/window-management/index.ts
components/app-viewer/AppViewerOverlay.tsx
components/app-viewer/AppViewerLayout.tsx
components/app-viewer/index.ts
```
