#!/usr/bin/env node
/**
 * Export Creator artifacts as standalone .html files.
 * Bundles React + Tailwind CSS into a single file per artifact.
 */

import fs from "fs/promises";
import path from "path";
import { build } from "esbuild-wasm";

const SRC_DIR = process.argv[2] || "./src";
const OUTPUT_DIR = process.argv[3] || "./export";

async function exportArtifact(identifier, artifactType) {
  const entryPath = path.join(SRC_DIR, `${identifier}.tsx`);
  
  if (!fs.existsSync(entryPath)) {
    console.error(`Entry not found: ${entryPath}`);
    return null;
  }

  // Build bundle
  await build({
    entryPoints: [entryPath],
    bundle: true,
    minify: false,
    sourcemap: true,
    platform: "browser",
    target: ["es2018"],
    outfile: path.join(OUTPUT_DIR, `${identifier}.bundle.js`),
    external: ["react", "react-dom"],
  });

  // Generate HTML template with embedded CSS (Tailwind compiled separately)
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>${identifier}</title>
</head>
<body class="min-h-screen bg-white">
  <div id="root"></div>
  <script type="module" src="${path.join(OUTPUT_DIR, `${identifier}.bundle.js`)}"></script>
</body>
</html>`;

  await fs.promises.writeFile(
    path.join(OUTPUT_DIR, `${identifier}.html`),
    html,
    "utf-8"
  );

  console.log(`✓ Exported ${identifier} → ${path.join(OUTPUT_DIR, `${identifier}.html`)}`);
}

// Scan for artifact entries
async function main() {
  const files = await fs.promises.readdir(SRC_DIR);
  
  for (const file of files) {
    if (/\.tsx$/.test(file)) {
      const identifier = path.basename(file, ".tsx");
      await exportArtifact(identifier, "react");
    }
  }

  console.log(`
Export complete. Output: ${OUTPUT_DIR}`);
}

main().catch(console.error);
