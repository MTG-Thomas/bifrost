/**
 * Tabs Component for App Builder
 *
 * Displays tabbed content with support for horizontal/vertical orientation.
 */

import { cn } from "@/lib/utils";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { components } from "@/lib/v1";
import type { AppComponent } from "@/lib/app-builder-helpers";

type TabsComponentType = components["schemas"]["TabsComponent"];
type TabItemComponentType = components["schemas"]["TabItemComponent"];
import type { RegisteredComponentProps } from "../ComponentRegistry";
import { LayoutRenderer } from "../LayoutRenderer";

export function TabsComponent({
	component,
	context,
}: RegisteredComponentProps) {
	// In the unified model, props are at the top level of the component
	const props = component as TabsComponentType;

	// Guard against undefined props or children
	// Filter to only TabItemComponent children
	const children = props?.children ?? [];
	const tabItems = children.filter(
		(child): child is TabItemComponentType => child.type === "tab-item"
	);
	if (tabItems.length === 0) {
		return null;
	}

	// Get tab value - prefer explicit value, fall back to id
	const getTabValue = (item: TabItemComponentType) => item.value ?? item.id;

	const defaultTab = props?.default_tab || getTabValue(tabItems[0]);
	const isVertical = props?.orientation === "vertical";

	return (
		<Tabs
			defaultValue={defaultTab}
			className={cn(isVertical && "flex gap-4", props?.class_name)}
			orientation={props?.orientation ?? undefined}
		>
			<TabsList
				className={cn(
					isVertical && "flex-col h-auto items-stretch"
				)}
			>
				{tabItems.map((item) => (
					<TabsTrigger
						key={item.id}
						value={getTabValue(item)}
						className={cn(isVertical && "justify-start")}
					>
						{item.label}
					</TabsTrigger>
				))}
			</TabsList>
			<div className={cn(isVertical ? "flex-1" : "")}>
				{tabItems.map((item) => (
					<TabsContent key={item.id} value={getTabValue(item)} className="mt-0 pt-4">
						{/* Render each child of the tab item */}
						{(item.children ?? []).map((child) => (
							<LayoutRenderer
								key={child.id}
								layout={child as AppComponent}
								context={context}
							/>
						))}
					</TabsContent>
				))}
			</div>
		</Tabs>
	);
}
