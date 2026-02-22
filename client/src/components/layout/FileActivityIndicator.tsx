import { useMemo, useState, useEffect } from "react";
import { Radio } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useFileActivityStore } from "@/stores/fileActivityStore";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function FileActivityIndicator() {
	const { user } = useAuth();
	const activeWatchers = useFileActivityStore((s) => s.activeWatchers);
	const recentPushes = useFileActivityStore((s) => s.recentPushes);

	// Tick every 15s to re-evaluate recency filtering
	const [now, setNow] = useState(() => Date.now());
	useEffect(() => {
		const timer = setInterval(() => setNow(Date.now()), 15_000);
		return () => clearInterval(timer);
	}, []);

	// Filter out own activity
	const otherWatchers = activeWatchers.filter(
		(w) => w.user_id !== user?.id,
	);
	const recentOtherPushes = useMemo(
		() =>
			recentPushes.filter(
				(p) =>
					p.user_id !== user?.id &&
					now - new Date(p.timestamp).getTime() < 60_000,
			),
		[recentPushes, user?.id, now],
	);

	if (otherWatchers.length === 0 && recentOtherPushes.length === 0)
		return null;

	const hasLiveWatcher = otherWatchers.length > 0;

	const label = hasLiveWatcher
		? otherWatchers.length > 1
			? `${otherWatchers.length} developers active`
			: `${otherWatchers[0].user_name} editing ${otherWatchers[0].prefix}`
		: recentOtherPushes.length > 0
			? `${recentOtherPushes[recentOtherPushes.length - 1].user_name} pushed files`
			: "";

	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<div className="flex items-center gap-1.5 mr-2 text-xs text-muted-foreground">
					<Radio
						className={cn(
							"h-3.5 w-3.5",
							hasLiveWatcher
								? "text-green-500 animate-pulse"
								: "text-blue-500",
						)}
					/>
					<span className="hidden lg:inline max-w-48 truncate">
						{label}
					</span>
				</div>
			</TooltipTrigger>
			<TooltipContent side="bottom" className="max-w-64">
				{otherWatchers.length > 0 && (
					<div className="space-y-1">
						<p className="font-medium">Active watchers:</p>
						{otherWatchers.map((w) => (
							<p key={`${w.user_id}:${w.prefix}`}>
								{w.user_name} — {w.prefix}
							</p>
						))}
					</div>
				)}
				{recentOtherPushes.length > 0 && (
					<div className="space-y-1 mt-1">
						<p className="font-medium">Recent file changes:</p>
						{recentOtherPushes.slice(-5).map((p, i) => (
							<p key={i}>
								{p.user_name} — {p.file_count} files to{" "}
								{p.prefix}
							</p>
						))}
					</div>
				)}
			</TooltipContent>
		</Tooltip>
	);
}
