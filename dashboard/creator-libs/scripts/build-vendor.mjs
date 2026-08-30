#!/usr/bin/env node
import { build } from "esbuild-wasm";
await build({
  entryPoints: ["src/initial.tsx"],
  bundle: true,
  platform: "browser",
  target: ["es2018"],
  outfile: "../static/bundle.js",
});