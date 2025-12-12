import { useState } from "react";
import {
	Bell,
	X,
	AlertCircle,
	AlertTriangle,
	Info,
	CheckCircle,
	Trash2,
	Loader2,
	Github,
	Upload,
	Package,
	Cog,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { useNotifications } from "@/hooks/useNotifications";
import {
	type Notification,
	type NotificationCategory,
	type NotificationStatus,
	isActiveNotification,
	getNotificationCounts,
	useNotificationStore,
	type OneOffNotification,
	type AlertStatus,
	getAlertCounts,
} from "@/stores/notificationStore";
import { cn } from "@/lib/utils";

/**
 * Notification Center component for the header
 *
 * Displays:
 * 1. Progress notifications - Long-running operations with status and optional progress bar
 * 2. One-off alerts - Dismissable success/error/warning/info messages
 */

// Icon mapping for notification categories
const categoryIcons: Record<NotificationCategory, typeof Github> = {
	github_setup: Github,
	github_sync: Github,
	file_upload: Upload,
	package_install: Package,
	system: Cog,
};

// Status config for one-off alerts
const alertStatusConfig: Record<
	AlertStatus,
	{ icon: typeof AlertCircle; color: string; bgColor: string }
> = {
	error: {
		icon: AlertCircle,
		color: "text-red-500",
		bgColor: "bg-red-500/10",
	},
	warning: {
		icon: AlertTriangle,
		color: "text-yellow-500",
		bgColor: "bg-yellow-500/10",
	},
	info: {
		icon: Info,
		color: "text-blue-500",
		bgColor: "bg-blue-500/10",
	},
	success: {
		icon: CheckCircle,
		color: "text-green-500",
		bgColor: "bg-green-500/10",
	},
};

// Status config for progress notifications
const notificationStatusConfig: Record<
	NotificationStatus,
	{ color: string; bgColor: string }
> = {
	pending: { color: "text-blue-500", bgColor: "bg-blue-500/10" },
	running: { color: "text-blue-500", bgColor: "bg-blue-500/10" },
	completed: { color: "text-green-500", bgColor: "bg-green-500/10" },
	failed: { color: "text-red-500", bgColor: "bg-red-500/10" },
	cancelled: { color: "text-muted-foreground", bgColor: "bg-muted/50" },
};

function ProgressNotificationItem({
	notification,
	onDismiss,
}: {
	notification: Notification;
	onDismiss: () => void;
}) {
	const Icon = categoryIcons[notification.category] || Cog;
	const statusConfig = notificationStatusConfig[notification.status];
	const isActive = isActiveNotification(notification);

	return (
		<div
			className={cn(
				"flex items-start gap-3 p-3 rounded-lg border",
				statusConfig.bgColor,
			)}
		>
			<div className={cn("mt-0.5 flex-shrink-0", statusConfig.color)}>
				{isActive ? (
					<Loader2 className="h-5 w-5 animate-spin" />
				) : notification.status === "completed" ? (
					<CheckCircle className="h-5 w-5" />
				) : notification.status === "failed" ? (
					<AlertCircle className="h-5 w-5" />
				) : (
					<Icon className="h-5 w-5" />
				)}
			</div>
			<div className="flex-1 min-w-0">
				<span className="text-sm font-medium truncate block">
					{notification.title}
				</span>
				{notification.description && (
					<p className="text-xs text-muted-foreground mt-1">
						{notification.description}
					</p>
				)}
				{notification.error && (
					<p className="text-xs text-red-500 mt-1">
						{notification.error}
					</p>
				)}
				{/* Progress bar for determinate progress */}
				{isActive && notification.percent !== null && (
					<Progress
						value={notification.percent}
						className="h-1.5 mt-2"
					/>
				)}
				<p className="text-xs text-muted-foreground/60 mt-1">
					{new Date(notification.updatedAt).toLocaleString()}
				</p>
			</div>
			{!isActive && (
				<Button
					variant="ghost"
					size="icon"
					className="h-6 w-6 flex-shrink-0"
					onClick={onDismiss}
				>
					<X className="h-3 w-3" />
				</Button>
			)}
		</div>
	);
}

function AlertNotificationItem({
	alert,
	onDismiss,
}: {
	alert: OneOffNotification;
	onDismiss: () => void;
}) {
	const config = alertStatusConfig[alert.status];
	const Icon = config.icon;

	return (
		<div
			className={cn(
				"flex items-start gap-3 p-3 rounded-lg border",
				config.bgColor,
			)}
		>
			<Icon
				className={cn("h-5 w-5 mt-0.5 flex-shrink-0", config.color)}
			/>
			<div className="flex-1 min-w-0">
				<span className="text-sm font-medium truncate block">
					{alert.title}
				</span>
				<p className="text-xs text-muted-foreground mt-1">
					{alert.body}
				</p>
				<p className="text-xs text-muted-foreground/60 mt-1">
					{new Date(alert.createdAt).toLocaleString()}
				</p>
			</div>
			<Button
				variant="ghost"
				size="icon"
				className="h-6 w-6 flex-shrink-0"
				onClick={onDismiss}
			>
				<X className="h-3 w-3" />
			</Button>
		</div>
	);
}

export function NotificationCenter() {
	const [isOpen, setIsOpen] = useState(false);
	const { notifications, dismiss, clearAll } = useNotifications();
	const alerts = useNotificationStore((state) => state.alerts);
	const removeAlert = useNotificationStore((state) => state.removeAlert);
	const clearAlerts = useNotificationStore((state) => state.clearAlerts);

	const notificationCounts = getNotificationCounts(notifications);
	const alertCounts = getAlertCounts(alerts);

	// Total count for badge
	const totalCount =
		notificationCounts.active +
		notificationCounts.failed +
		alertCounts.error +
		alertCounts.warning;

	// Badge color based on highest priority
	const getBadgeVariant = () => {
		if (notificationCounts.failed > 0 || alertCounts.error > 0)
			return "destructive";
		if (alertCounts.warning > 0) return "default";
		if (notificationCounts.active > 0) return "secondary";
		return "secondary";
	};

	// Sort notifications: active first, then by date
	const sortedNotifications = [...notifications].sort((a, b) => {
		// Active notifications first
		const aActive = isActiveNotification(a) ? 0 : 1;
		const bActive = isActiveNotification(b) ? 0 : 1;
		if (aActive !== bActive) return aActive - bActive;

		// Then by date (newest first)
		return (
			new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
		);
	});

	// Sort alerts by status priority, then date
	const sortedAlerts = [...alerts].sort((a, b) => {
		const priority: Record<AlertStatus, number> = {
			error: 0,
			warning: 1,
			info: 2,
			success: 3,
		};
		if (priority[a.status] !== priority[b.status]) {
			return priority[a.status] - priority[b.status];
		}
		return (
			new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
		);
	});

	const handleClearAll = () => {
		clearAll();
		clearAlerts();
	};

	const hasNotifications =
		sortedNotifications.length > 0 || sortedAlerts.length > 0;

	return (
		<Popover open={isOpen} onOpenChange={setIsOpen}>
			<PopoverTrigger asChild>
				<Button variant="ghost" size="icon" className="relative">
					<Bell className="h-4 w-4" />
					{totalCount > 0 && (
						<Badge
							variant={getBadgeVariant()}
							className="absolute -top-1 -right-1 h-5 w-5 flex items-center justify-center p-0 text-xs"
						>
							{totalCount > 99 ? "99+" : totalCount}
						</Badge>
					)}
				</Button>
			</PopoverTrigger>
			<PopoverContent className="w-96 p-0" align="end" sideOffset={8}>
				<div className="flex items-center justify-between px-4 py-3 border-b">
					<h3 className="font-semibold">Notifications</h3>
					{hasNotifications && (
						<Button
							variant="ghost"
							size="sm"
							className="h-8 text-xs"
							onClick={handleClearAll}
						>
							<Trash2 className="h-3 w-3 mr-1" />
							Clear all
						</Button>
					)}
				</div>
				<div className="h-[400px] overflow-y-auto">
					{!hasNotifications ? (
						<div className="flex flex-col items-center justify-center h-32 text-muted-foreground">
							<Bell className="h-8 w-8 mb-2 opacity-50" />
							<p className="text-sm">No notifications</p>
						</div>
					) : (
						<div className="p-2 space-y-2">
							{/* Progress notifications */}
							{sortedNotifications.map((notification) => (
								<ProgressNotificationItem
									key={notification.id}
									notification={notification}
									onDismiss={() => dismiss(notification.id)}
								/>
							))}

							{/* Divider if both types present */}
							{sortedNotifications.length > 0 &&
								sortedAlerts.length > 0 && (
									<div className="border-t my-2" />
								)}

							{/* One-off alerts */}
							{sortedAlerts.map((alert) => (
								<AlertNotificationItem
									key={alert.id}
									alert={alert}
									onDismiss={() => removeAlert(alert.id)}
								/>
							))}
						</div>
					)}
				</div>
			</PopoverContent>
		</Popover>
	);
}
