import { Component, ErrorInfo, ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardFooter,
	CardHeader,
	CardTitle,
	CardDescription,
} from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";

interface Props {
	children: ReactNode;
	resetKey?: string;
}

interface State {
	hasError: boolean;
	error: Error | null;
}

export class PageErrorBoundary extends Component<Props, State> {
	constructor(props: Props) {
		super(props);
		this.state = { hasError: false, error: null };
	}

	static getDerivedStateFromError(error: Error): Partial<State> {
		return { hasError: true, error };
	}

	componentDidCatch(error: Error, errorInfo: ErrorInfo) {
		if (import.meta.env.DEV) {
			console.error("PageErrorBoundary caught:", error, errorInfo);
		}
	}

	componentDidUpdate(prevProps: Props) {
		if (prevProps.resetKey !== this.props.resetKey && this.state.hasError) {
			this.setState({ hasError: false, error: null });
		}
	}

	handleReset = () => {
		this.setState({ hasError: false, error: null });
	};

	render() {
		if (this.state.hasError) {
			return (
				<div className="flex items-center justify-center min-h-[400px] p-4">
					<Card className="w-full max-w-lg">
						<CardHeader>
							<div className="flex items-center gap-3">
								<div className="flex h-10 w-10 items-center justify-center rounded-lg bg-destructive/10">
									<AlertTriangle className="h-5 w-5 text-destructive" />
								</div>
								<div>
									<CardTitle className="text-lg">
										Something went wrong
									</CardTitle>
									<CardDescription>
										This page encountered an error
									</CardDescription>
								</div>
							</div>
						</CardHeader>
						<CardContent>
							<Alert variant="destructive">
								<AlertDescription className="font-mono text-sm">
									{this.state.error?.message || "Unknown error"}
								</AlertDescription>
							</Alert>
						</CardContent>
						<CardFooter>
							<Button onClick={this.handleReset}>
								<RotateCcw className="mr-2 h-4 w-4" />
								Try Again
							</Button>
						</CardFooter>
					</Card>
				</div>
			);
		}
		return this.props.children;
	}
}

export function RouteErrorBoundary({ children }: { children: ReactNode }) {
	const location = useLocation();
	return (
		<PageErrorBoundary resetKey={location.pathname}>
			{children}
		</PageErrorBoundary>
	);
}
