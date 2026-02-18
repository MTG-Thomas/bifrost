import { useState, useCallback } from "react";

export function useSidebar() {
	const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
	const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => {
		return localStorage.getItem("sidebar-collapsed") === "true";
	});

	const toggleSidebar = useCallback(() => {
		const newState = !isSidebarCollapsed;
		setIsSidebarCollapsed(newState);
		localStorage.setItem("sidebar-collapsed", String(newState));
	}, [isSidebarCollapsed]);

	return {
		isMobileMenuOpen,
		setIsMobileMenuOpen,
		isSidebarCollapsed,
		toggleSidebar,
	};
}
