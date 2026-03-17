/**
 * ESM React Shim — ensures external esm.sh packages use the platform's
 * React instance instead of downloading their own copy.
 *
 * Problem: esm.sh serves its own React 19 bundle. Same version, but a
 * different JS object in memory. React hooks require the exact same object
 * instance, so any package calling useState/useContext crashes.
 *
 * Solution: expose Vite's React on window globals, create blob-URL ES
 * modules that re-export from those globals, and inject an import map so
 * bare `import "react"` resolves to the platform's copy.
 *
 * Must be called once at startup, before any dynamic import() of esm.sh URLs.
 */
import React from "react";
import ReactDOM from "react-dom";
import * as ReactJSXRuntime from "react/jsx-runtime";
import * as ReactJSXDevRuntime from "react/jsx-dev-runtime";
import * as ReactDOMClient from "react-dom/client";

declare global {
	interface Window {
		__BIFROST_REACT: typeof React;
		__BIFROST_REACT_DOM: typeof ReactDOM;
		__BIFROST_REACT_JSX_RUNTIME: typeof ReactJSXRuntime;
		__BIFROST_REACT_JSX_DEV_RUNTIME: typeof ReactJSXDevRuntime;
		__BIFROST_REACT_DOM_CLIENT: typeof ReactDOMClient;
	}
}

/**
 * Create a blob URL for an ES module that re-exports from a window global.
 */
function makeBlobModule(globalName: string, moduleObj: object): string {
	const keys = Object.keys(moduleObj).filter((k) => k !== "default");
	const lines: string[] = [
		`const m = window.${globalName};`,
		`export default m.default !== undefined ? m.default : m;`,
	];
	if (keys.length > 0) {
		lines.push(
			`export const { ${keys.join(", ")} } = m;`,
		);
	}
	const code = lines.join("\n");
	const blob = new Blob([code], { type: "application/javascript" });
	return URL.createObjectURL(blob);
}

/**
 * Inject an import map into the document so bare specifiers like
 * `import "react"` resolve to our blob URLs.
 */
function injectImportMap(imports: Record<string, string>): void {
	const script = document.createElement("script");
	script.type = "importmap";
	script.textContent = JSON.stringify({ imports });
	document.head.appendChild(script);
}

let initialized = false;

/**
 * Initialize the React shim. Call once at app startup before createRoot().
 */
export function initReactShim(): void {
	if (initialized) return;
	initialized = true;

	// 1. Expose on window
	window.__BIFROST_REACT = React;
	window.__BIFROST_REACT_DOM = ReactDOM;
	window.__BIFROST_REACT_JSX_RUNTIME = ReactJSXRuntime;
	window.__BIFROST_REACT_JSX_DEV_RUNTIME = ReactJSXDevRuntime;
	window.__BIFROST_REACT_DOM_CLIENT = ReactDOMClient;

	// 2. Create blob URLs
	const imports: Record<string, string> = {
		react: makeBlobModule("__BIFROST_REACT", React),
		"react-dom": makeBlobModule("__BIFROST_REACT_DOM", ReactDOM),
		"react/jsx-runtime": makeBlobModule(
			"__BIFROST_REACT_JSX_RUNTIME",
			ReactJSXRuntime,
		),
		"react/jsx-dev-runtime": makeBlobModule(
			"__BIFROST_REACT_JSX_DEV_RUNTIME",
			ReactJSXDevRuntime,
		),
		"react-dom/client": makeBlobModule(
			"__BIFROST_REACT_DOM_CLIENT",
			ReactDOMClient,
		),
	};

	// 3. Inject import map
	injectImportMap(imports);
}
