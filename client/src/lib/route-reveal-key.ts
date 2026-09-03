export function routeRevealKey(pathname: string, locationKey: string): string {
	if (pathname === "/settings" || pathname.startsWith("/settings/")) {
		return "settings";
	}

	const isChatConversationRoute =
		pathname === "/chat" ||
		/^\/chat\/(?!artifacts(?:\/|$))[^/]+\/?$/.test(pathname);
	if (isChatConversationRoute) {
		return "chat";
	}

	const isAppRoute = /^\/apps\/[^/]+(?:\/|$)/.test(pathname);
	const isAppEditorRoute = /^\/apps\/[^/]+\/edit(?:\/|$)/.test(pathname);
	const isAppRunnerRoute = isAppRoute && !isAppEditorRoute;
	return isAppRunnerRoute ? "app-runner" : locationKey;
}
