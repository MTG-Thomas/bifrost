import { cn } from "@/lib/utils";
import { Sparkline } from "./Sparkline";

export interface StatCardProps {
	label: string;
	value: string;
	/** Text line under the value. E.g. "+18% vs prior week". Optional. */
	delta?: string;
	/** Tone of the delta line. "up" = green, "down" = red, unset = muted. */
	deltaTone?: "up" | "down";
	/** Optional mini-sparkline rendered under the value/delta row. */
	sparkline?: number[];
	sparklineColorClass?: string;
	/** When true, theme the card with a red accent — used for "Needs review". */
	alert?: boolean;
	icon?: React.ReactNode;
	onClick?: () => void;
	className?: string;
}

/**
 * A single stat card matching the mockup's `.stat-card` spec:
 *  - small uppercase label
 *  - 22px semibold value
 *  - optional delta line under value
 *  - optional embedded sparkline
 *  - optional alert treatment (red label + value + left border accent)
 */
export function StatCard({
	label,
	value,
	delta,
	deltaTone,
	sparkline,
	sparklineColorClass,
	alert,
	icon,
	onClick,
	className,
}: StatCardProps) {
	const interactive = !!onClick;
	return (
		<div
			role={interactive ? "button" : undefined}
			tabIndex={interactive ? 0 : undefined}
			onClick={onClick}
			onKeyDown={(e) => {
				if (interactive && (e.key === "Enter" || e.key === " ")) {
					e.preventDefault();
					onClick?.();
				}
			}}
			className={cn(
				"rounded-[10px] border bg-card px-4 py-3.5 transition-colors",
				alert && "border-rose-500/40",
				interactive && "cursor-pointer hover:border-border/80",
				className,
			)}
			data-slot="stat-card"
		>
			<div
				className={cn(
					"flex items-center gap-1.5 text-[11.5px] uppercase tracking-wider font-medium",
					alert
						? "text-rose-600 dark:text-rose-400"
						: "text-muted-foreground",
				)}
			>
				{icon}
				{label}
			</div>
			<div
				className={cn(
					"mt-1.5 text-[22px] leading-tight font-semibold tracking-tight tabular-nums",
					alert && "text-rose-600 dark:text-rose-400",
				)}
			>
				{value}
			</div>
			{delta ? (
				<div
					className={cn(
						"mt-1 text-xs",
						deltaTone === "up" && "text-emerald-600 dark:text-emerald-400",
						deltaTone === "down" && "text-rose-600 dark:text-rose-400",
						!deltaTone && "text-muted-foreground",
					)}
				>
					{delta}
				</div>
			) : null}
			{sparkline && sparkline.length > 1 ? (
				<div className="mt-2 h-8">
					<Sparkline values={sparkline} colorClass={sparklineColorClass} />
				</div>
			) : null}
		</div>
	);
}
