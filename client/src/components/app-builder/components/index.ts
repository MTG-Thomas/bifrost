/**
 * App Builder Components
 *
 * Export all components and registration function.
 */

// Basic components
export { HeadingComponent } from "./HeadingComponent";
export { TextComponent } from "./TextComponent";
export { HtmlComponent } from "./HtmlComponent";
export { CardComponent } from "./CardComponent";
export { DividerComponent } from "./DividerComponent";
export { SpacerComponent } from "./SpacerComponent";
export { ButtonComponent } from "./ButtonComponent";

// Display components
export { StatCardComponent } from "./StatCardComponent";
export { ImageComponent } from "./ImageComponent";
export { BadgeComponent } from "./BadgeComponent";
export { ProgressComponent } from "./ProgressComponent";

// Data components
export { DataTableComponent } from "./DataTableComponent";
export { TabsComponent } from "./TabsComponent";

// File and modal components
export { FileViewerComponent } from "./FileViewerComponent";
export { ModalComponent } from "./ModalComponent";

// Form input components
export { TextInputComponent } from "./TextInputComponent";
export { NumberInputComponent } from "./NumberInputComponent";
export { SelectComponent } from "./SelectComponent";
export { CheckboxComponent } from "./CheckboxComponent";

// Form integration components
export { FormEmbedComponent } from "./FormEmbedComponent";
export { FormGroupComponent } from "./FormGroupComponent";

import { registerComponent } from "../ComponentRegistry";
import { HeadingComponent } from "./HeadingComponent";
import { TextComponent } from "./TextComponent";
import { HtmlComponent } from "./HtmlComponent";
import { CardComponent } from "./CardComponent";
import { DividerComponent } from "./DividerComponent";
import { SpacerComponent } from "./SpacerComponent";
import { ButtonComponent } from "./ButtonComponent";
import { StatCardComponent } from "./StatCardComponent";
import { ImageComponent } from "./ImageComponent";
import { BadgeComponent } from "./BadgeComponent";
import { ProgressComponent } from "./ProgressComponent";
import { DataTableComponent } from "./DataTableComponent";
import { TabsComponent } from "./TabsComponent";
import { FileViewerComponent } from "./FileViewerComponent";
import { ModalComponent } from "./ModalComponent";
import { TextInputComponent } from "./TextInputComponent";
import { NumberInputComponent } from "./NumberInputComponent";
import { SelectComponent } from "./SelectComponent";
import { CheckboxComponent } from "./CheckboxComponent";
import { FormEmbedComponent } from "./FormEmbedComponent";
import { FormGroupComponent } from "./FormGroupComponent";

/**
 * Register all components with the ComponentRegistry.
 * Call this function once during app initialization.
 */
export function registerAllComponents(): void {
	// Basic components
	registerComponent("heading", HeadingComponent);
	registerComponent("text", TextComponent);
	registerComponent("html", HtmlComponent);
	registerComponent("card", CardComponent);
	registerComponent("divider", DividerComponent);
	registerComponent("spacer", SpacerComponent);
	registerComponent("button", ButtonComponent);

	// Display components
	registerComponent("stat-card", StatCardComponent);
	registerComponent("image", ImageComponent);
	registerComponent("badge", BadgeComponent);
	registerComponent("progress", ProgressComponent);

	// Data components
	registerComponent("data-table", DataTableComponent);
	registerComponent("tabs", TabsComponent);

	// File and modal components
	registerComponent("file-viewer", FileViewerComponent);
	registerComponent("modal", ModalComponent);

	// Form input components
	registerComponent("text-input", TextInputComponent);
	registerComponent("number-input", NumberInputComponent);
	registerComponent("select", SelectComponent);
	registerComponent("checkbox", CheckboxComponent);

	// Form integration components
	registerComponent("form-embed", FormEmbedComponent);
	registerComponent("form-group", FormGroupComponent);
}

/**
 * @deprecated Use registerAllComponents instead
 */
export const registerBasicComponents = registerAllComponents;

// Auto-register all components when this module is imported
// This ensures components are available immediately for the editor preview
registerAllComponents();
