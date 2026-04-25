import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
	{ ignores: ["dist"] },
	{
		extends: [js.configs.recommended, ...tseslint.configs.recommended],
		files: ["**/*.{ts,tsx}"],
		languageOptions: {
			ecmaVersion: 2020,
			globals: globals.browser,
		},
		plugins: {
			"react-hooks": reactHooks,
			"react-refresh": reactRefresh,
		},
		rules: {
			...reactHooks.configs.recommended.rules,
			// eslint-plugin-react-hooks v7 enables React Compiler diagnostics in
			// its recommended set. This codebase is not compiler-clean yet, so keep
			// the traditional hooks checks without turning existing compiler
			// diagnostics into merge blockers.
			"react-hooks/config": "off",
			"react-hooks/error-boundaries": "off",
			"react-hooks/gating": "off",
			"react-hooks/globals": "off",
			"react-hooks/immutability": "off",
			"react-hooks/incompatible-library": "off",
			"react-hooks/preserve-manual-memoization": "off",
			"react-hooks/purity": "off",
			"react-hooks/refs": "off",
			"react-hooks/set-state-in-effect": "off",
			"react-hooks/set-state-in-render": "off",
			"react-hooks/static-components": "off",
			"react-hooks/unsupported-syntax": "off",
			"react-hooks/use-memo": "off",
			"react-refresh/only-export-components": "off",
			"no-console": ["warn", { allow: ["warn", "error"] }], // Allow console.warn and console.error, but warn about console.log
			// Allow underscore-prefixed unused variables
			"@typescript-eslint/no-unused-vars": [
				"error",
				{
					argsIgnorePattern: "^_",
					varsIgnorePattern: "^_",
				},
			],
		},
	},
);
