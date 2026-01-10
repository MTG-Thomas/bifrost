import { useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/contexts/AuthContext";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { AlertCircle, FileText, Server } from "lucide-react";
import { SystemLogsTab } from "./components/SystemLogsTab";
import { WorkersTab } from "./components/WorkersTab";

export function DiagnosticsPage() {
	const { isPlatformAdmin } = useAuth();
	const navigate = useNavigate();
	const location = useLocation();

	// Parse the current tab from the URL path
	const currentTab = location.pathname.split("/diagnostics/")[1] || "logs";

	const handleTabChange = (value: string) => {
		navigate(`/diagnostics/${value}`);
	};

	// Redirect /diagnostics to /diagnostics/logs (first tab)
	useEffect(() => {
		if (location.pathname === "/diagnostics") {
			navigate("/diagnostics/logs", { replace: true });
		}
	}, [location.pathname, navigate]);

	// Check admin access
	if (!isPlatformAdmin) {
		return (
			<div className="container mx-auto py-8">
				<Alert variant="destructive">
					<AlertCircle className="h-4 w-4" />
					<AlertDescription>
						You do not have permission to view diagnostics. Platform
						administrator access is required.
					</AlertDescription>
				</Alert>
				<Button onClick={() => navigate("/")} className="mt-4">
					Return to Dashboard
				</Button>
			</div>
		);
	}

	return (
		<div className="h-[calc(100vh-8rem)] flex flex-col space-y-6">
			{/* Header */}
			<div>
				<h1 className="text-4xl font-extrabold tracking-tight">
					Diagnostics
				</h1>
				<p className="mt-2 text-muted-foreground">
					Monitor system health, process pools, and troubleshoot issues
				</p>
			</div>

			{/* Tabs */}
			<Tabs
				value={currentTab}
				onValueChange={handleTabChange}
				className="flex flex-col flex-1 overflow-hidden"
			>
				<TabsList>
					<TabsTrigger value="logs" className="flex items-center gap-2">
						<FileText className="h-4 w-4" />
						System Logs
					</TabsTrigger>
					<TabsTrigger value="workers" className="flex items-center gap-2">
						<Server className="h-4 w-4" />
						Process Pools
					</TabsTrigger>
				</TabsList>

				<TabsContent value="logs" className="mt-6 flex-1 overflow-hidden">
					<SystemLogsTab />
				</TabsContent>

				<TabsContent value="workers" className="mt-6 flex-1 overflow-auto">
					<WorkersTab />
				</TabsContent>
			</Tabs>
		</div>
	);
}

export default DiagnosticsPage;
