/** @type {import('tailwindcss').Config} */
export default {
	darkMode: ["class"],
	content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
	safelist: [
		// App Builder dynamic gap and padding classes
		{ pattern: /^gap-/ },
		{ pattern: /^p-/ },
		{ pattern: /^gap-\[.+\]/ },
		{ pattern: /^p-\[.+\]/ },
		// Grid columns
		{ pattern: /^grid-cols-/ },
	],
	plugins: [require("@tailwindcss/typography")],
};
