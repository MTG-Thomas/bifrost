import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/sonner";
import "./index.css";
import App from "./App.tsx";
import { queryClient } from "./lib/queryClient";
import { ThemeProvider } from "./contexts/ThemeContext";
import { OrgScopeQueryInvalidator } from "./components/OrgScopeQueryInvalidator";
import { configureMonaco } from "./lib/monaco-setup";

// Configure Monaco editor before React renders (sets up CDN paths for workers)
configureMonaco();

createRoot(document.getElementById("root")!).render(
	<StrictMode>
		<ThemeProvider>
			<QueryClientProvider client={queryClient}>
				<OrgScopeQueryInvalidator />
				<App />
				<Toaster />
			</QueryClientProvider>
		</ThemeProvider>
	</StrictMode>,
);
