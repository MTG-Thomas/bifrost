import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, XCircle, Code2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { FormRenderer } from "@/components/forms/FormRenderer";
import { useForm } from "@/hooks/useForms";
import { useAuth } from "@/contexts/AuthContext";
import { Skeleton } from "@/components/ui/skeleton";

const DEV_MODE_STORAGE_KEY = "bifrost.devMode";

export function RunForm() {
	const { formId } = useParams();
	const navigate = useNavigate();
	const { data: form, isLoading, error } = useForm(formId);
	const { isPlatformAdmin } = useAuth();

	// Developer mode state - persisted to localStorage
	const [devMode, setDevMode] = useState(() => {
		if (typeof window !== "undefined") {
			return localStorage.getItem(DEV_MODE_STORAGE_KEY) === "true";
		}
		return false;
	});

	// Persist dev mode changes to localStorage
	useEffect(() => {
		localStorage.setItem(DEV_MODE_STORAGE_KEY, String(devMode));
	}, [devMode]);

	if (isLoading) {
		return (
			<div className="space-y-6">
				<Skeleton className="h-12 w-64" />
				<Skeleton className="h-96 w-full" />
			</div>
		);
	}

	if (error || !form) {
		return (
			<div className="space-y-6">
				<Alert variant="destructive">
					<XCircle className="h-4 w-4" />
					<AlertTitle>Error</AlertTitle>
					<AlertDescription>
						{error ? "Failed to load form" : "Form not found"}
					</AlertDescription>
				</Alert>
				<Button onClick={() => navigate("/forms")}>
					<ArrowLeft className="mr-2 h-4 w-4" />
					Back to Forms
				</Button>
			</div>
		);
	}

	if (!form.is_active) {
		return (
			<div className="space-y-6">
				<Alert>
					<AlertTitle>Form Inactive</AlertTitle>
					<AlertDescription>
						This form is currently inactive and cannot be submitted.
					</AlertDescription>
				</Alert>
				<Button onClick={() => navigate("/forms")}>
					<ArrowLeft className="mr-2 h-4 w-4" />
					Back to Forms
				</Button>
			</div>
		);
	}

	return (
		<div className="space-y-6">
			<div className="flex justify-center">
				<div className="w-full max-w-2xl">
					<div className="flex items-center justify-between">
						<div>
							<h1 className="text-4xl font-extrabold tracking-tight">
								{form.name}
							</h1>
							{form.description && (
								<p className="mt-2 text-muted-foreground">
									{form.description}
								</p>
							)}
						</div>
						<div className="flex items-center gap-3">
							{/* Developer Mode Toggle - Platform Admins Only */}
							{isPlatformAdmin && (
								<div className="flex items-center gap-2">
									<Switch
										id="dev-mode"
										checked={devMode}
										onCheckedChange={setDevMode}
									/>
									<Label
										htmlFor="dev-mode"
										className="flex items-center gap-1.5 text-sm cursor-pointer"
									>
										<Code2 className="h-3.5 w-3.5" />
										Dev
									</Label>
								</div>
							)}
							<Button
								variant="outline"
								size="icon"
								onClick={() => navigate("/forms")}
								title="Back to Forms"
							>
								<ArrowLeft className="h-4 w-4" />
							</Button>
						</div>
					</div>
				</div>
			</div>

			<FormRenderer form={form} devMode={isPlatformAdmin && devMode} />
		</div>
	);
}
