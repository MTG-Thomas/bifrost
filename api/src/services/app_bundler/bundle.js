#!/usr/bin/env node
/**
 * esbuild-based bundler for Bifrost apps.
 *
 * Input (stdin JSON):
 *   {
 *     "source_dir": "/tmp/bundle-xyz/src",   // app source tree (TSX/TS/CSS)
 *     "out_dir":    "/tmp/bundle-xyz/dist",  // where to write bundle output
 *     "entry":      "_entry.tsx",            // entry file (relative to source_dir)
 *     "mode":       "preview" | "live",      // preview = not-minified + sourcemap
 *     "externals":  ["react", "react-dom", "react-router-dom", ...]
 *   }
 *
 * Output (stdout JSON):
 *   {
 *     "success": true,
 *     "outputs": [
 *       { "path": "entry-ABC123.js", "bytes": 12345 },
 *       { "path": "chunk-DEF456.js", "bytes": 6789 },
 *       ...
 *     ],
 *     "entry_file": "entry-ABC123.js",
 *     "css_file":   "entry-ABC123.css" | null,
 *     "duration_ms": 180
 *   }
 *
 * Or on failure:
 *   { "success": false, "error": "..." }
 */

const esbuild = require("esbuild");
const path = require("path");
const fs = require("fs");

async function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => (data += chunk));
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

// Convert esbuild's Message[] (errors/warnings) into a plain shape the
// Python side can route through the diagnostics channel. esbuild produces
// paths relative to cwd; we strip the source_dir prefix so callers see the
// app-relative path (e.g. "pages/email/index.tsx" not "/tmp/bundle-xyz/src/...").
function shapeMessages(messages, source_dir) {
  return messages.map((m) => {
    const loc = m.location;
    let file = loc?.file || null;
    if (file && source_dir) {
      // esbuild gives paths relative to process cwd, which is NOT source_dir
      // (we spawn from the API's cwd). Normalize to absolute, then strip
      // the source_dir prefix so the Python side sees an app-relative path.
      const abs = path.resolve(process.cwd(), file);
      if (abs.startsWith(source_dir)) {
        file = abs.slice(source_dir.length).replace(/^\/+/, "");
      } else {
        file = abs;
      }
    }
    return {
      text: m.text,
      file,
      line: loc?.line ?? null,
      column: loc?.column ?? null,
      line_text: loc?.lineText ?? null,
    };
  });
}

async function main() {
  const raw = await readStdin();
  const cfg = JSON.parse(raw);
  const { source_dir, out_dir, entry, mode, externals = [] } = cfg;

  if (!source_dir || !out_dir || !entry || !mode) {
    console.log(JSON.stringify({
      success: false,
      errors: [{ text: "Missing required config: source_dir, out_dir, entry, mode", file: null, line: null, column: null, line_text: null }],
    }));
    return;
  }

  fs.mkdirSync(out_dir, { recursive: true });

  const t0 = Date.now();

  let result;
  try {
    result = await esbuild.build({
      entryPoints: [path.join(source_dir, entry)],
      outdir: out_dir,
      bundle: true,
      format: "esm",
      target: "es2020",
      splitting: true,
      loader: {
        ".tsx": "tsx",
        ".ts": "ts",
        ".jsx": "jsx",
        ".js": "js",
        ".css": "css",
      },
      jsx: "automatic",
      external: externals,
      sourcemap: mode === "preview" ? "linked" : "inline",
      minify: mode === "live",
      entryNames: "entry-[hash]",
      chunkNames: "chunk-[hash]",
      assetNames: "asset-[hash]",
      metafile: true,
      logLevel: "silent",
    });
  } catch (buildErr) {
    // esbuild throws BuildFailure with .errors[] on syntax / resolve errors.
    const errs = Array.isArray(buildErr.errors) && buildErr.errors.length
      ? shapeMessages(buildErr.errors, source_dir)
      : [{ text: buildErr.message || String(buildErr), file: null, line: null, column: null, line_text: null }];
    const warns = Array.isArray(buildErr.warnings)
      ? shapeMessages(buildErr.warnings, source_dir)
      : [];
    console.log(JSON.stringify({
      success: false,
      errors: errs,
      warnings: warns,
      duration_ms: Date.now() - t0,
    }));
    return;
  }

  const duration_ms = Date.now() - t0;

  const outputs = [];
  let entry_file = null;
  let css_file = null;

  for (const [abs, meta] of Object.entries(result.metafile.outputs)) {
    const rel = path.relative(out_dir, abs);
    const bytes = meta.bytes;
    outputs.push({ path: rel, bytes });
    if (meta.entryPoint) {
      if (rel.endsWith(".js")) entry_file = rel;
      else if (rel.endsWith(".css")) css_file = rel;
    }
  }
  // Fallback: if entry is known but no CSS was flagged as an entry, just
  // grab the first top-level .css output (there'll only be one per entry).
  if (entry_file && !css_file) {
    const css = outputs.find((o) => o.path.endsWith(".css"));
    if (css) css_file = css.path;
  }

  console.log(JSON.stringify({
    success: true,
    outputs,
    entry_file,
    css_file,
    duration_ms,
    warnings: shapeMessages(result.warnings, source_dir),
  }));
}

main().catch((err) => {
  console.log(JSON.stringify({
    success: false,
    errors: [{ text: err.message || String(err), file: null, line: null, column: null, line_text: null }],
  }));
  process.exit(0);
});
