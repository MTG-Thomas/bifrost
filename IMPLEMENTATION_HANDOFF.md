# App Builder Layout System - Implementation Handoff

## STATUS: 60% Complete - Need PropertyEditor UI + RepeatFor Logic

---

## ✅ COMPLETED WORK

### 1. Frontend TypeScript Types
**File**: `client/src/lib/app-builder-types.ts`

**Changes Made**:
- Lines 803-813: Added new types `LayoutDistribute`, `LayoutOverflow`, `LayoutSticky`
- Lines 161-168: Added `RepeatFor` interface
- Lines 184-191: Added to `BaseComponentProps`: `gridSpan`, `repeatFor`, `className`, `style`
- Lines 839-869: Updated `LayoutContainer`: replaced `autoSize` with `distribute`, added `maxHeight`, `overflow`, `sticky`, `stickyOffset`, `className`, `style`
- Line 923: Added `styles` to `PageDefinition`
- Line 1021: Added `styles` to `ApplicationDefinition`

**Result**: All types now match the comprehensive design. ✅

### 2. Frontend Rendering - LayoutRenderer
**File**: `client/src/components/app-builder/LayoutRenderer.tsx`

**Changes Made**:
- Lines 170-206: Updated `getLayoutStyles()` to handle `maxHeight`, `overflow`, `sticky`, `stickyOffset`, inline `style`
- Lines 364-438: Replaced `autoSize` logic with `distribute` behavior:
  - `"natural"` (default): Children keep natural size
  - `"equal"`: Children wrapped in `flex-1 min-w-0`
  - `"fit"`: Children wrapped in `w-fit`
- Lines 373-386: Added grid spanning support (wraps in `<div style={{ gridColumn: 'span N' }}>`)
- Lines 461, 473, 489: Added `layout.className` to layout container classes
- Lines 543-552: Updated `renderComponent()` to apply `component.className` and `component.style`

**Result**: All layout/component properties render correctly. ✅

### 3. CSS Injection - AppRenderer
**File**: `client/src/components/app-builder/AppRenderer.tsx`

**Changes Made**:
- Lines 65-70: Page-level CSS injection in `PageRenderer`
- Lines 291-311: App-level CSS injection in main `AppRenderer`

**Result**: Custom CSS can be injected at page and app levels. ✅

### 4. Backend Python Schemas
**File**: `api/src/models/contracts/applications.py`

**Changes Made**:
- Lines 430-435: Added `RepeatFor` model
- Lines 452-455: Updated `AppComponentNode`: added `grid_span`, `repeat_for`, `class_name`, `style`
- Lines 479-487: Updated `LayoutContainer`: replaced `auto_size` with `distribute`, added `max_height`, `overflow`, `sticky`, `sticky_offset`, `style`
- Line 516: Updated `PageDefinition`: added `styles`
- Line 580: Updated `ApplicationExport`: added `styles`

**Result**: Backend validates all new properties. ✅

---

## 🔄 IN PROGRESS: PropertyEditor UI

**File**: `client/src/components/app-builder/editor/PropertyEditor.tsx`

**Current State**: File has existing structure for layout/component properties but missing all new fields.

**What Exists**:
- Line 390-545: `LayoutPropertiesSection` function (handles layout container props)
- Line 548-605: `HeadingPropertiesSection` function (example of component-specific props)
- Line 73-92: `FormField` helper component (use this for consistent styling)
- Line 95-136: `JsonEditor` component (use for `style` object editing)

**Architecture Note**: The `onChange` callback is already generic and saves ALL props automatically via EditorShell's `onComponentUpdate`. No special save logic needed - just call `onChange({ newProp: value })`.

---

## 📋 TASK 1: Add Layout Properties UI

**Location**: In `LayoutPropertiesSection` function (starts ~line 390)

**Strategy**: Add these fields AFTER the existing `maxWidth` field (line 520). Use the same pattern as existing fields.

### Add These Fields (copy-paste ready):

```tsx
// ADD AFTER LINE 520 (after maxWidth closing tag)

<FormField
  label="Distribute"
  description="How children fill available space"
>
  <Select
    value={component.distribute ?? "natural"}
    onValueChange={(value) =>
      onChange({
        distribute:
          value === "natural"
            ? undefined
            : (value as "natural" | "equal" | "fit"),
      })
    }
  >
    <SelectTrigger>
      <SelectValue />
    </SelectTrigger>
    <SelectContent>
      <SelectItem value="natural">Natural (default)</SelectItem>
      <SelectItem value="equal">Equal (flex-1)</SelectItem>
      <SelectItem value="fit">Fit Content</SelectItem>
    </SelectContent>
  </Select>
</FormField>

<FormField
  label="Max Height"
  description="Container height limit (pixels, enables scrolling)"
>
  <Input
    type="number"
    value={component.maxHeight ?? ""}
    onChange={(e) =>
      onChange({
        maxHeight: e.target.value
          ? Number(e.target.value)
          : undefined,
      })
    }
    placeholder="None"
    min={0}
  />
</FormField>

<FormField
  label="Overflow"
  description="Behavior when content exceeds bounds"
>
  <Select
    value={component.overflow ?? "visible"}
    onValueChange={(value) =>
      onChange({
        overflow:
          value === "visible"
            ? undefined
            : (value as "auto" | "scroll" | "hidden" | "visible"),
      })
    }
  >
    <SelectTrigger>
      <SelectValue />
    </SelectTrigger>
    <SelectContent>
      <SelectItem value="visible">Visible (default)</SelectItem>
      <SelectItem value="auto">Auto (scroll when needed)</SelectItem>
      <SelectItem value="scroll">Always Scroll</SelectItem>
      <SelectItem value="hidden">Hidden (clip)</SelectItem>
    </SelectContent>
  </Select>
</FormField>

<FormField
  label="Sticky Position"
  description="Pin container to edge when scrolling"
>
  <Select
    value={component.sticky ?? "none"}
    onValueChange={(value) =>
      onChange({
        sticky:
          value === "none"
            ? undefined
            : (value as "top" | "bottom"),
      })
    }
  >
    <SelectTrigger>
      <SelectValue />
    </SelectTrigger>
    <SelectContent>
      <SelectItem value="none">None (default)</SelectItem>
      <SelectItem value="top">Sticky Top</SelectItem>
      <SelectItem value="bottom">Sticky Bottom</SelectItem>
    </SelectContent>
  </Select>
</FormField>

{component.sticky && (
  <FormField
    label="Sticky Offset"
    description="Distance from edge (pixels)"
  >
    <Input
      type="number"
      value={component.stickyOffset ?? 0}
      onChange={(e) =>
        onChange({
          stickyOffset: Number(e.target.value),
        })
      }
      min={0}
    />
  </FormField>
)}

<FormField
  label="CSS Classes"
  description="Custom Tailwind or CSS classes"
>
  <Input
    value={component.className ?? ""}
    onChange={(e) =>
      onChange({
        className: e.target.value || undefined,
      })
    }
    placeholder="bg-blue-500 rounded-lg"
  />
</FormField>

<Accordion type="single" collapsible>
  <AccordionItem value="inline-styles">
    <AccordionTrigger>Inline Styles (Advanced)</AccordionTrigger>
    <AccordionContent>
      <JsonEditor
        value={component.style ?? {}}
        onChange={(value) =>
          onChange({ style: value })
        }
        rows={4}
      />
      <p className="text-xs text-muted-foreground mt-2">
        Use camelCase: maxHeight, backgroundColor, etc.
      </p>
    </AccordionContent>
  </AccordionItem>
</Accordion>
```

**Imports Needed** (add at top if not present):
```tsx
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
```

**Testing Steps**:
1. Start: `./debug.sh` from repo root
2. Open: http://localhost:3000/apps/crm/edit (or any app)
3. Click any row/column/grid container in the preview pane
4. Verify RIGHT PANEL shows all new fields under "Layout" accordion
5. Change each value, save (Cmd+S or auto-save)
6. Refresh page, verify values persisted

---

## 📋 TASK 2: Add Component Properties UI

**Location**: Find the `CommonPropertiesSection` function or where `visible`/`width` are rendered for ALL components.

**Strategy**: These properties apply to ALL components (not just layouts), so they go in a shared section.

### Add These Fields (after existing `width` field):

```tsx
// ADD THESE FIELDS IN THE COMPONENT PROPERTIES SECTION

<FormField
  label="Grid Span"
  description="Columns to span (for components in grid layouts)"
>
  <Input
    type="number"
    value={component.gridSpan ?? 1}
    onChange={(e) =>
      onChange({
        gridSpan: Number(e.target.value),
      })
    }
    min={1}
    max={12}
  />
</FormField>

<FormField
  label="CSS Classes"
  description="Custom Tailwind or CSS classes"
>
  <Input
    value={component.className ?? ""}
    onChange={(e) =>
      onChange({
        className: e.target.value || undefined,
      })
    }
    placeholder="text-blue-500 font-bold"
  />
</FormField>

<Accordion type="single" collapsible>
  <AccordionItem value="inline-styles">
    <AccordionTrigger>Inline Styles</AccordionTrigger>
    <AccordionContent>
      <JsonEditor
        value={component.style ?? {}}
        onChange={(value) =>
          onChange({ style: value })
        }
        rows={4}
      />
      <p className="text-xs text-muted-foreground mt-2">
        Use camelCase: maxHeight, backgroundColor
      </p>
    </AccordionContent>
  </AccordionItem>

  <AccordionItem value="repeat">
    <AccordionTrigger>Repeat Component</AccordionTrigger>
    <AccordionContent className="space-y-4">
      <FormField
        label="Items Expression"
        description="Array to iterate over"
      >
        <Input
          value={component.repeatFor?.items ?? ""}
          onChange={(e) =>
            onChange({
              repeatFor: e.target.value
                ? {
                    items: e.target.value,
                    as: component.repeatFor?.as || "item",
                    itemKey: component.repeatFor?.itemKey || "id",
                  }
                : undefined,
            })
          }
          placeholder="{{ workflow.clients }}"
        />
      </FormField>

      {component.repeatFor && (
        <>
          <FormField
            label="Loop Variable"
            description="Name to access each item"
          >
            <Input
              value={component.repeatFor.as}
              onChange={(e) =>
                onChange({
                  repeatFor: {
                    ...component.repeatFor,
                    as: e.target.value,
                  },
                })
              }
              placeholder="client"
            />
          </FormField>

          <FormField
            label="Item Key Property"
            description="Unique property for React keys"
          >
            <Input
              value={component.repeatFor.itemKey}
              onChange={(e) =>
                onChange({
                  repeatFor: {
                    ...component.repeatFor,
                    itemKey: e.target.value,
                  },
                })
              }
              placeholder="id"
            />
          </FormField>
        </>
      )}
    </AccordionContent>
  </AccordionItem>
</Accordion>
```

**Note**: The `repeatFor` UI is in an accordion since it's advanced. Collapsed by default.

---

## 📋 TASK 3: Implement RepeatFor Rendering

**File**: `client/src/components/app-builder/LayoutRenderer.tsx`

**Location**: In the `renderComponent` function, BEFORE the existing visibility check (~line 513)

**Strategy**: Detect `repeatFor`, evaluate the items expression, map over array with extended context.

### Add This Logic:

```tsx
function renderComponent(
	component: AppComponent,
	context: ExpressionContext,
	className?: string,
	previewContext?: PreviewSelectionContext,
): React.ReactElement | null {
	// ADD THIS BLOCK AT THE START (before visibility check)

	// Handle component repetition
	if (component.repeatFor) {
		const items = evaluateExpression(component.repeatFor.items, context);

		if (!Array.isArray(items)) {
			console.error(
				`repeatFor items must evaluate to an array. Got: ${typeof items}`,
				{ component: component.id, expression: component.repeatFor.items }
			);
			return null;
		}

		return (
			<>
				{items.map((item, index) => {
					// Get unique key from item
					const key = item[component.repeatFor!.itemKey] ?? index;

					// Extend context with loop variable
					const extendedContext: ExpressionContext = {
						...context,
						[component.repeatFor!.as]: item,
					};

					// Render component for this item (without repeatFor to prevent infinite loop)
					return (
						<LayoutRenderer
							key={key}
							layout={{ ...component, repeatFor: undefined }}
							context={extendedContext}
							className={className}
							isPreview={previewContext?.isPreview}
							selectedComponentId={previewContext?.selectedComponentId}
							onSelectComponent={previewContext?.onSelectComponent}
						/>
					);
				})}
			</>
		);
	}

	// EXISTING CODE CONTINUES HERE (visibility check, etc.)
	// Check visibility
	if (!evaluateVisibility(component.visible, context)) {
		return null;
	}
	// ... rest of function
}
```

**Import Needed** (add at top):
```tsx
import { evaluateExpression } from "@/lib/expression-parser";
```

**Testing**:
1. Create a test workflow that returns an array: `[{id: 1, name: "A"}, {id: 2, name: "B"}]`
2. In app editor, add a card component
3. Set repeatFor:
   - items: `{{ workflow.testData }}`
   - as: `item`
   - itemKey: `id`
4. In card title, use: `{{ item.name }}`
5. Verify TWO cards render with titles "A" and "B"

---

## 📋 TASK 4: Fix CRM App (Remove autoSize)

**Problem**: The existing CRM app has `autoSize: false` in JSON, which is now deprecated.

**File**: This is stored in the database as JSON in the `app_versions` table.

**Solution Options**:

### Option A: Update via MCP Tool (RECOMMENDED)
```typescript
// Get the page
const page = await mcp__bifrost__get_page({
  app_id: "crm-app-uuid",
  page_id: "client-detail"
});

// Update the layout - remove autoSize from content-row
const updatedLayout = page.layout;
// Find the "content-row" and remove autoSize property
// Then update via update_page tool
```

### Option B: Manual Editor Fix
1. Open http://localhost:3000/apps/crm/edit
2. Navigate to Client Details page
3. Select the "content-row" layout
4. Remove the `autoSize` field from JSON (if editor allows raw JSON edit)
5. Save

### Option C: Database Migration Script
Create a script in `api/scripts/migrate_autosize.py` that:
- Queries all app_versions
- Parses JSON
- Removes `autoSize` keys
- Updates rows

**Recommended**: Option A via MCP is cleanest.

---

## 📋 TASK 5: Page & App CSS UI

**Page-Level CSS**:
- File: `PropertyEditor.tsx`
- Location: Add a new accordion section when `page` prop is present
- Add AFTER existing page settings (if any):

```tsx
{page && (
  <AccordionItem value="page-styles">
    <AccordionTrigger>Page CSS</AccordionTrigger>
    <AccordionContent>
      <FormField
        label="Custom Styles"
        description="CSS rules scoped to this page"
      >
        <Textarea
          value={page.styles ?? ""}
          onChange={(e) =>
            onPageChange?.({
              styles: e.target.value || undefined,
            })
          }
          rows={10}
          placeholder=".custom-sidebar { position: sticky; top: 0; }"
          className="font-mono text-sm"
        />
      </FormField>
    </AccordionContent>
  </AccordionItem>
)}
```

**App-Level CSS**:
- File: Need to create or find app settings modal/page
- This is less critical - can be added later
- Same pattern as page CSS but calls an app update endpoint

---

## 📋 TASK 6: Update MCP Schema Documentation

**File**: `api/src/routers/app_builder.py` (or wherever `get_app_schema` is defined)

**What to Update**: The large docstring that gets returned. Add examples for:

1. **Distribute property**:
```markdown
### Layout Distribution

Control how children fill space:
- `distribute: "natural"` (default): Children keep natural size
- `distribute: "equal"`: Children expand equally (flex-1)
- `distribute: "fit"`: Children fit content

Example:
{
  "type": "row",
  "distribute": "equal",
  "children": [
    {"type": "text-input", "props": {"fieldId": "firstName"}},
    {"type": "text-input", "props": {"fieldId": "lastName"}}
  ]
}
```

2. **Scrollable containers**:
```markdown
### Scrollable Containers

{
  "type": "column",
  "maxHeight": 400,
  "overflow": "auto",
  "children": [...]
}
```

3. **RepeatFor**:
```markdown
### Repeating Components

{
  "type": "card",
  "repeatFor": {
    "items": "{{ workflow.clients }}",
    "itemKey": "id",
    "as": "client"
  },
  "props": {
    "title": "{{ client.name }}"
  }
}
```

4. **CSS Support**:
```markdown
### Custom Styling

Component-level:
{
  "type": "card",
  "className": "bg-blue-50 rounded-lg",
  "style": {"maxHeight": "300px", "overflowY": "auto"}
}

Page-level:
{
  "styles": ".custom-class { background: linear-gradient(...); }"
}
```

---

## 📋 TASK 7: Breaking Change - Workflow Result Mapping

**Files to Update**:
1. Expression parser: `client/src/lib/expression-parser.ts`
2. Workflow context builder: Wherever `workflow.<key>.result` structure is created

**Current Behavior**:
```typescript
// Workflow returns: [client1, client2]
// Stored as: workflow.clients.result = [...]
// Accessed as: {{ workflow.clients.result }}
```

**New Behavior**:
```typescript
// Workflow returns: [client1, client2]
// Stored as: workflow.clients = [...]
// Accessed as: {{ workflow.clients }}
```

**Changes Needed**:
1. Find where workflow results are added to context (likely in `AppRenderer` or `usePageData` hook)
2. Remove the `.result` wrapper - directly assign workflow return value
3. Update CRM app expressions (search for `workflow.*.result` and remove `.result`)

**Search Command**:
```bash
grep -r "workflow\\..*\\.result" client/src/components/app-builder
```

---

## 🧪 TESTING CHECKLIST

### Frontend Type Check
```bash
cd client
npm run tsc  # Must pass with 0 errors
```

### Backend Type Check
```bash
cd api
pyright src/models/contracts/applications.py  # Must pass
```

### Type Generation
```bash
# Start dev stack first
./debug.sh

# In another terminal
cd client
npm run generate:types  # Must succeed, no errors
```

### End-to-End Test
1. Start stack: `./debug.sh`
2. Open CRM app editor: http://localhost:3000/apps/crm/edit
3. Test each new property:
   - Select a row layout → verify "Distribute" selector appears
   - Set distribute to "equal" → save → refresh → verify persisted
   - Set maxHeight to 400 → verify scrollbar appears when content overflows
   - Add className "bg-blue-50" → verify background changes
   - Add inline style `{"padding": "20px"}` → verify padding applied
4. Test repeatFor:
   - Create test workflow returning array
   - Add card with repeatFor
   - Verify multiple cards render
5. Test CSS injection:
   - Add page styles: `.test { color: red; }`
   - Add component with `className="test"`
   - Verify red text

---

## 📊 COMPLETION CRITERIA

- [ ] All PropertyEditor fields added and functional
- [ ] RepeatFor rendering works (map over arrays)
- [ ] Type generation runs without errors
- [ ] All existing CRM app pages still work
- [ ] autoSize removed from CRM app
- [ ] MCP schema docs updated with examples
- [ ] All TypeScript/Python type checks pass
- [ ] Manual E2E test of each new feature passes

---

## 🚨 NOTES FOR IMPLEMENTER

1. **Save Queue**: Already works! Just call `onChange({ newProp: value })` - EditorShell handles persistence.

2. **Type Imports**: If you get TypeScript errors about unknown types, add imports at top:
```tsx
import type { LayoutDistribute, LayoutOverflow, LayoutSticky } from "@/lib/app-builder-types";
```

3. **Component vs Layout**: Some properties (distribute, maxHeight) only apply to layouts. Others (gridSpan, repeatFor) apply to all components. Check the type definitions if unsure.

4. **Accordion Component**: Already imported in PropertyEditor. Just use `<Accordion type="single" collapsible>` for collapsible sections.

5. **Testing Without Breaking Prod**: All changes are backwards compatible except `autoSize`. Old apps without new properties continue working (undefined values = defaults).

---

## ESTIMATED EFFORT

- PropertyEditor UI: 2-3 hours (mostly copy-paste + testing)
- RepeatFor logic: 1 hour (straightforward map + context extension)
- MCP docs: 30 minutes (add examples to docstring)
- Testing: 1-2 hours (comprehensive E2E)

**Total**: ~5-7 hours for complete implementation.
