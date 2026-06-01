import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

const disabledReactCompilerRules = [
	"config",
	"error-boundaries",
	"gating",
	"globals",
	"immutability",
	"incompatible-library",
	"preserve-manual-memoization",
	"purity",
	"refs",
	"set-state-in-effect",
	"set-state-in-render",
	"static-components",
	"unsupported-syntax",
	"use-memo",
];

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
			...Object.fromEntries(
				disabledReactCompilerRules.map((ruleName) => [
					`react-hooks/${ruleName}`,
					"off",
				]),
			),
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
