/**
 * App Builder Module
 *
 * Recursive layout renderer and component system for building dynamic applications.
 */

// Core rendering
export { AppRenderer, StandalonePageRenderer } from "./AppRenderer";
export { LayoutRenderer } from "./LayoutRenderer";
export { AppShell, AppShellMinimal } from "./AppShell";
export { WorkflowStatusIndicator } from "./WorkflowStatusIndicator";
export { AppUpdateIndicator } from "./AppUpdateIndicator";
export { NewVersionBanner } from "./NewVersionBanner";

// Modals
export { CreateAppModal } from "./CreateAppModal";

// Editor
export {
	EditorShell,
	StructureTree,
	ComponentInserter,
	PropertyEditor,
	PageTree,
	type EditorShellProps,
	type StructureTreeProps,
	type PropertyEditorProps,
	type PageTreeProps,
} from "./editor";

// Component registry
export {
	registerComponent,
	getComponent,
	hasComponent,
	unregisterComponent,
	getRegisteredTypes,
	clearRegistry,
	renderRegisteredComponent,
	UnknownComponent,
	type RegisteredComponentProps,
} from "./ComponentRegistry";

// Components
export {
	// Basic
	HeadingComponent,
	TextComponent,
	CardComponent,
	DividerComponent,
	SpacerComponent,
	ButtonComponent,
	// Display
	StatCardComponent,
	ImageComponent,
	BadgeComponent,
	ProgressComponent,
	// Data
	DataTableComponent,
	TabsComponent,
	// Registration
	registerAllComponents,
	registerBasicComponents,
} from "./components";

// Re-export types from @/types/app-builder for convenience
export type {
	ExpressionUser,
	ExpressionContext,
	LayoutContainer,
	PageDefinition,
	ApplicationDefinition,
	WorkflowResult,
	NavItem,
	NavigationConfig,
	RepeatFor,
	OnCompleteAction,
} from "@/types/app-builder";

// Re-export from API types
export type { components } from "@/lib/v1";

// Re-export utility functions
export { isLayoutContainer, isAppComponent, canHaveChildren, getElementChildren, CONTAINER_TYPES } from "@/lib/app-builder-utils";

// Re-export expression utilities
export {
	evaluateExpression,
	evaluateVisibility,
	evaluateSingleExpression,
	hasExpressions,
	extractVariablePaths,
} from "@/lib/expression-parser";
