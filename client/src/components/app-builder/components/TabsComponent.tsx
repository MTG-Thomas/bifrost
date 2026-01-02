/**
 * Tabs Component for App Builder
 *
 * Displays tabbed content with support for horizontal/vertical orientation.
 */

import { cn } from "@/lib/utils";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { TabsComponentProps } from "@/lib/app-builder-types";
import type { RegisteredComponentProps } from "../ComponentRegistry";
import { LayoutRenderer } from "../LayoutRenderer";

export function TabsComponent({ component, context }: RegisteredComponentProps) {
	const { props } = component as TabsComponentProps;

	const defaultTab = props.defaultTab || props.items[0]?.id;
	const isVertical = props.orientation === "vertical";

	if (props.items.length === 0) {
		return null;
	}

	return (
		<Tabs
			defaultValue={defaultTab}
			className={cn(
				isVertical && "flex gap-4",
				props.className,
			)}
			orientation={props.orientation}
		>
			<TabsList
				className={cn(
					isVertical && "flex-col h-auto items-stretch",
				)}
			>
				{props.items.map((item) => (
					<TabsTrigger
						key={item.id}
						value={item.id}
						className={cn(
							isVertical && "justify-start",
						)}
					>
						{item.label}
					</TabsTrigger>
				))}
			</TabsList>
			<div className={cn(isVertical ? "flex-1" : "mt-4")}>
				{props.items.map((item) => (
					<TabsContent key={item.id} value={item.id} className="mt-0">
						<LayoutRenderer layout={item.content} context={context} />
					</TabsContent>
				))}
			</div>
		</Tabs>
	);
}
