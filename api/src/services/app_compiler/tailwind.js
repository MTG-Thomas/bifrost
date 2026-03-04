#!/usr/bin/env node
/**
 * Tailwind CSS generator for Bifrost app files.
 * Uses @tailwindcss/node to generate CSS from a list of class candidates.
 *
 * Input (stdin):  {"candidates": ["flex", "p-4", "!w-[33vw]", ...]}
 * Output (stdout): {"css": "...", "error": null}
 */
const { compile } = require("@tailwindcss/node");

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", async () => {
  try {
    const { candidates } = JSON.parse(input);
    if (!candidates || candidates.length === 0) {
      process.stdout.write(JSON.stringify({ css: "", error: null }));
      return;
    }

    const compiler = await compile("@import 'tailwindcss/theme' layer(theme);\n@import 'tailwindcss/utilities';", {
      base: __dirname,
      onDependency: () => {},
    });
    const css = compiler.build(candidates);

    process.stdout.write(JSON.stringify({ css, error: null }));
  } catch (err) {
    process.stdout.write(JSON.stringify({ css: null, error: err.message }));
    process.exit(1);
  }
});
