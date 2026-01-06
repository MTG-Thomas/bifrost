/**
 * Layout Renderer for App Builder
 *
 * Recursive renderer for layout containers and components.
 * Handles row/column/grid layouts and delegates component rendering to the registry.
 */

import type React from "react";
import { cn } from "@/lib/utils";
import type {
	LayoutContainer,
	AppComponent,
	ExpressionContext,
	ComponentWidth,
	LayoutType,
	ComponentType,
	LayoutMaxWidth,
} from "@/lib/app-builder-types";
import { isLayoutContainer } from "@/lib/app-builder-types";
import { evaluateVisibility } from "@/lib/expression-parser";
import { renderRegisteredComponent } from "./ComponentRegistry";

/**
 * Preview selection context for editor mode
 */
interface PreviewSelectionContext {
	/** Whether we're in preview/editor mode */
	isPreview?: boolean;
	/** Currently selected component ID */
	selectedComponentId?: string | null;
	/** Callback when a component is clicked */
	onSelectComponent?: (componentId: string | null) => void;
}

interface LayoutRendererProps extends PreviewSelectionContext {
	/** The layout container or component to render */
	layout: LayoutContainer | AppComponent;
	/** Expression context for evaluating expressions and visibility */
	context: ExpressionContext;
	/** Optional additional class names */
	className?: string;
	/** Parent key for generating unique keys for nested layouts */
	parentKey?: string;
}

/**
 * Get Tailwind classes for component width
 */
function getWidthClasses(width?: ComponentWidth): string {
	switch (width) {
		case "full":
			return "w-full";
		case "1/2":
			return "w-1/2";
		case "1/3":
			return "w-1/3";
		case "1/4":
			return "w-1/4";
		case "2/3":
			return "w-2/3";
		case "3/4":
			return "w-3/4";
		case "auto":
		default:
			return "w-auto";
	}
}

/**
 * Get Tailwind classes for layout alignment (cross-axis)
 */
function getAlignClasses(align?: string): string {
	switch (align) {
		case "start":
			return "items-start";
		case "center":
			return "items-center";
		case "end":
			return "items-end";
		case "stretch":
			return "items-stretch";
		default:
			return "";
	}
}

/**
 * Get Tailwind classes for layout justification (main-axis)
 */
function getJustifyClasses(justify?: string): string {
	switch (justify) {
		case "start":
			return "justify-start";
		case "center":
			return "justify-center";
		case "end":
			return "justify-end";
		case "between":
			return "justify-between";
		case "around":
			return "justify-around";
		default:
			return "";
	}
}

/**
 * Get Tailwind classes for max-width constraint
 * Useful for constraining form layouts to readable widths
 */
function getMaxWidthClasses(maxWidth?: LayoutMaxWidth): string {
	switch (maxWidth) {
		case "sm":
			return "max-w-sm mx-auto"; // 384px, centered
		case "md":
			return "max-w-md mx-auto"; // 448px, centered
		case "lg":
			return "max-w-lg mx-auto"; // 512px, centered
		case "xl":
			return "max-w-xl mx-auto"; // 576px, centered
		case "2xl":
			return "max-w-2xl mx-auto"; // 672px, centered
		case "full":
		case "none":
		default:
			return "";
	}
}

/**
 * Get default gap for layout type
 * Column layouts default to 16px (comfortable spacing between sections)
 * Row layouts default to 8px (tighter spacing for inline elements)
 * Grid layouts default to 16px (consistent with column)
 * Set gap: 0 explicitly to remove spacing
 */
function getDefaultGap(layoutType: LayoutType): number {
	switch (layoutType) {
		case "row":
			return 8;
		case "column":
		case "grid":
			return 16;
		default:
			return 16;
	}
}

/**
 * Get inline style for gap (Tailwind JIT can't compile dynamic values)
 * Uses sensible defaults per layout type; set gap: 0 explicitly for no gap
 */
function getGapStyle(gap: number | undefined, layoutType: LayoutType): React.CSSProperties {
	const effectiveGap = gap ?? getDefaultGap(layoutType);
	if (effectiveGap === 0) return {};
	return { gap: `${effectiveGap}px` };
}

/**
 * Get inline style for padding (Tailwind JIT can't compile dynamic values)
 */
function getPaddingStyle(padding?: number): React.CSSProperties {
	if (padding === undefined || padding === 0) return {};
	return { padding: `${padding}px` };
}

/**
 * Get combined layout styles
 */
function getLayoutStyles(layout: {
	type: LayoutType;
	gap?: number;
	padding?: number;
}): React.CSSProperties {
	return {
		...getGapStyle(layout.gap, layout.type),
		...getPaddingStyle(layout.padding),
	};
}

/**
 * Get grid columns class
 */
function getGridColumnsClass(columns?: number): string {
	if (columns === undefined) return "grid-cols-1";
	const colMap: Record<number, string> = {
		1: "grid-cols-1",
		2: "grid-cols-2",
		3: "grid-cols-3",
		4: "grid-cols-4",
		5: "grid-cols-5",
		6: "grid-cols-6",
		12: "grid-cols-12",
	};
	return colMap[columns] || `grid-cols-[repeat(${columns},1fr)]`;
}

/**
 * Get a display label for a component type
 */
function getTypeLabel(type: ComponentType | LayoutType): string {
	const labels: Record<string, string> = {
		row: "Row",
		column: "Column",
		grid: "Grid",
		heading: "Heading",
		text: "Text",
		html: "HTML",
		card: "Card",
		divider: "Divider",
		spacer: "Spacer",
		button: "Button",
		"stat-card": "Stat Card",
		image: "Image",
		badge: "Badge",
		progress: "Progress",
		"data-table": "Data Table",
		tabs: "Tabs",
		"file-viewer": "File Viewer",
		modal: "Modal",
		"text-input": "Text Input",
		"number-input": "Number Input",
		select: "Select",
		checkbox: "Checkbox",
		"form-embed": "Form Embed",
		"form-group": "Form Group",
	};
	return labels[type] || type;
}

/**
 * Wrapper component for selectable elements in preview mode
 */
function SelectableWrapper({
	id,
	type,
	isSelected,
	onSelect,
	children,
	className,
}: {
	id: string;
	type: ComponentType | LayoutType;
	isSelected: boolean;
	onSelect: (id: string) => void;
	children: React.ReactNode;
	className?: string;
}) {
	return (
		<div
			className={cn(
				"relative transition-all duration-150",
				isSelected && "ring-2 ring-primary ring-offset-2 rounded-sm",
				"hover:ring-1 hover:ring-primary/50 hover:ring-offset-1 cursor-pointer",
				className,
			)}
			onClickCapture={(e) => {
				// Check if click target is within a MORE NESTED SelectableWrapper
				// If so, let that wrapper handle it instead
				const target = e.target as HTMLElement;
				const closestWrapper = target.closest("[data-selectable]");
				if (closestWrapper && closestWrapper !== e.currentTarget) {
					// Click is on a nested selectable - let it propagate to that wrapper
					return;
				}
				// This is the innermost selectable - handle selection and stop propagation
				e.stopPropagation();
				e.preventDefault();
				onSelect(id);
			}}
			data-selectable={id}
		>
			{children}
			{isSelected && (
				<div className="absolute -top-5 left-1 z-10 rounded bg-primary px-1.5 py-0.5 text-[10px] font-medium text-primary-foreground shadow-sm">
					{getTypeLabel(type)}
				</div>
			)}
		</div>
	);
}

/**
 * Generate a stable unique key for a layout child
 * For components, use their id. For layout containers, generate a key from
 * the parent's key/type and child index to ensure uniqueness across the tree.
 */
function generateChildKey(
	child: LayoutContainer | AppComponent,
	index: number,
	parentKey: string,
): string {
	if (isLayoutContainer(child)) {
		// For layout containers, combine parent key with index and child type
		return `${parentKey}-${child.type}-${index}`;
	}
	// For components, prefer their unique id but fall back to generated key
	// (some components like spacer, divider may not have explicit ids)
	return child.id || `${parentKey}-${child.type}-${index}`;
}

/**
 * Render a layout container (row, column, or grid)
 */
function renderLayoutContainer(
	layout: LayoutContainer,
	context: ExpressionContext,
	className?: string,
	parentKey = "root",
	previewContext?: PreviewSelectionContext,
): React.ReactElement | null {
	// Check visibility
	if (!evaluateVisibility(layout.visible, context)) {
		return null;
	}

	const { isPreview, selectedComponentId, onSelectComponent } =
		previewContext || {};

	const baseClasses = cn(
		getAlignClasses(layout.align),
		getJustifyClasses(layout.justify),
		getMaxWidthClasses(layout.maxWidth),
		layout.className,
		className,
	);

	const layoutStyles = getLayoutStyles(layout);

	// Generate a unique key for this container based on parent
	const containerKey = parentKey;

	// Row children keep their natural size by default (standard CSS flexbox behavior).
	// Set autoSize: false to make children expand equally to fill available space (flex-1).
	const renderChild = (
		child: LayoutContainer | AppComponent,
		index: number,
		parentType: "row" | "column" | "grid",
		autoSize?: boolean,
	) => {
		const key = generateChildKey(child, index, containerKey);

		// In row layouts, only wrap with flex-1 if autoSize is explicitly false
		// Default behavior (autoSize undefined or true) keeps natural sizes
		if (parentType === "row" && autoSize === false) {
			const hasExplicitWidth =
				!isLayoutContainer(child) &&
				child.width &&
				child.width !== "auto";
			return (
				<div
					key={key}
					className={hasExplicitWidth ? undefined : "flex-1 min-w-0"}
				>
					<LayoutRenderer
						layout={child}
						context={context}
						parentKey={key}
						isPreview={isPreview}
						selectedComponentId={selectedComponentId}
						onSelectComponent={onSelectComponent}
					/>
				</div>
			);
		}

		return (
			<LayoutRenderer
				key={key}
				layout={child}
				context={context}
				parentKey={key}
				isPreview={isPreview}
				selectedComponentId={selectedComponentId}
				onSelectComponent={onSelectComponent}
			/>
		);
	};

	// Wrap layout container in SelectableWrapper when in preview mode
	const wrapWithSelectable = (content: React.ReactElement) => {
		if (isPreview && onSelectComponent) {
			return (
				<SelectableWrapper
					id={containerKey}
					type={layout.type}
					isSelected={selectedComponentId === containerKey}
					onSelect={onSelectComponent}
				>
					{content}
				</SelectableWrapper>
			);
		}
		return content;
	};

	switch (layout.type) {
		case "row":
			return wrapWithSelectable(
				<div
					className={cn("flex flex-row flex-wrap", baseClasses)}
					style={layoutStyles}
				>
					{layout.children.map((child, index) =>
						renderChild(child, index, "row", layout.autoSize),
					)}
				</div>,
			);

		case "column":
			return wrapWithSelectable(
				<div
					className={cn("flex flex-col", baseClasses)}
					style={layoutStyles}
				>
					{layout.children.map((child, index) =>
						renderChild(child, index, "column"),
					)}
				</div>,
			);

		case "grid":
			return wrapWithSelectable(
				<div
					className={cn(
						"grid",
						getGridColumnsClass(layout.columns),
						baseClasses,
					)}
					style={layoutStyles}
				>
					{layout.children.map((child, index) =>
						renderChild(child, index, "grid"),
					)}
				</div>,
			);

		default:
			return null;
	}
}

/**
 * Render an app component with visibility check and width handling
 */
function renderComponent(
	component: AppComponent,
	context: ExpressionContext,
	className?: string,
	previewContext?: PreviewSelectionContext,
): React.ReactElement | null {
	// Check visibility
	if (!evaluateVisibility(component.visible, context)) {
		return null;
	}

	const { isPreview, selectedComponentId, onSelectComponent } =
		previewContext || {};

	const widthClass = getWidthClasses(component.width);
	const wrappedComponent = renderRegisteredComponent(component, context);

	// Wrap in SelectableWrapper when in preview mode
	const wrapWithSelectable = (content: React.ReactElement | null) => {
		if (!content) return null;
		if (isPreview && onSelectComponent && component.id) {
			return (
				<SelectableWrapper
					id={component.id}
					type={component.type}
					isSelected={selectedComponentId === component.id}
					onSelect={onSelectComponent}
				>
					{content}
				</SelectableWrapper>
			);
		}
		return content;
	};

	// If the component has a width constraint, wrap it
	if (component.width && component.width !== "auto") {
		return wrapWithSelectable(
			<div key={component.id} className={cn(widthClass, className)}>
				{wrappedComponent}
			</div>,
		);
	}

	return wrapWithSelectable(wrappedComponent);
}

/**
 * Layout Renderer Component
 *
 * Recursively renders layout containers and their children.
 * Delegates component rendering to the ComponentRegistry.
 *
 * @example
 * <LayoutRenderer
 *   layout={{
 *     type: "column",
 *     gap: 16,
 *     children: [
 *       { id: "h1", type: "heading", props: { text: "Hello", level: 1 } },
 *       { id: "t1", type: "text", props: { text: "Welcome to the app" } },
 *     ],
 *   }}
 *   context={{ user: { name: "John" }, variables: {} }}
 * />
 */
export function LayoutRenderer({
	layout,
	context,
	className,
	parentKey = "root",
	isPreview,
	selectedComponentId,
	onSelectComponent,
}: LayoutRendererProps): React.ReactElement | null {
	const previewContext: PreviewSelectionContext = {
		isPreview,
		selectedComponentId,
		onSelectComponent,
	};

	if (isLayoutContainer(layout)) {
		return renderLayoutContainer(
			layout,
			context,
			className,
			parentKey,
			previewContext,
		);
	}

	return renderComponent(layout, context, className, previewContext);
}

export default LayoutRenderer;
