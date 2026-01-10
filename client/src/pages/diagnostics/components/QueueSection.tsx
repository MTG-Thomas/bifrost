import { motion, AnimatePresence } from "framer-motion";
import { RefreshCw, Clock, Inbox } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { QueueItem } from "@/services/workers";

interface QueueSectionProps {
	items: QueueItem[];
	isLoading?: boolean;
	onRefresh?: () => void;
}

/**
 * Format relative time from ISO date string
 */
function formatRelativeTime(dateStr: string | null | undefined): string {
	if (!dateStr) return "Unknown";
	const date = new Date(dateStr);
	const now = new Date();
	const diffMs = now.getTime() - date.getTime();
	const diffSec = Math.floor(diffMs / 1000);

	if (diffSec < 60) return `${diffSec}s ago`;
	const minutes = Math.floor(diffSec / 60);
	if (minutes < 60) return `${minutes}m ago`;
	const hours = Math.floor(minutes / 60);
	return `${hours}h ago`;
}

export function QueueSection({ items, isLoading, onRefresh }: QueueSectionProps) {
	return (
		<Card>
			<CardHeader className="pb-3">
				<div className="flex items-center justify-between">
					<div className="flex items-center gap-2">
						<CardTitle className="text-lg">QUEUE</CardTitle>
						<Badge variant="secondary">{items.length} pending</Badge>
					</div>
					{onRefresh && (
						<Button
							variant="ghost"
							size="icon"
							onClick={onRefresh}
							disabled={isLoading}
						>
							<RefreshCw
								className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`}
							/>
						</Button>
					)}
				</div>
			</CardHeader>
			<CardContent>
				{items.length === 0 ? (
					<div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
						<Inbox className="h-8 w-8 mb-2" />
						<p className="text-sm">No jobs queued</p>
					</div>
				) : (
					<div className="divide-y">
						<AnimatePresence mode="popLayout">
							{items.map((item, index) => (
								<motion.div
									key={item.execution_id}
									initial={{ opacity: 0, y: -10 }}
									animate={{ opacity: 1, y: 0 }}
									exit={{ opacity: 0, x: 20 }}
									transition={{ duration: 0.2, delay: index * 0.05 }}
									layout
									className="flex items-center justify-between py-3 first:pt-0 last:pb-0"
								>
									<div className="flex items-center gap-3">
										<span className="text-muted-foreground font-mono text-sm w-6">
											#{item.position}
										</span>
										<span className="font-medium">
											{item.execution_id.substring(0, 8)}...
										</span>
									</div>
									<div className="flex items-center gap-2 text-sm text-muted-foreground">
										<Clock className="h-3 w-3" />
										<span>
											queued {formatRelativeTime(item.queued_at)}
										</span>
									</div>
								</motion.div>
							))}
						</AnimatePresence>
					</div>
				)}
			</CardContent>
		</Card>
	);
}
