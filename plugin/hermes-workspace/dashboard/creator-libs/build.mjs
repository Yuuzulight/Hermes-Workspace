import { copyFileSync, writeFileSync, readFileSync } from 'node:fs'
import * as esbuild from 'esbuild'

// Populated in later tasks: each entry bundles a browser-side library into a
// zero-import ESM file consumed by the Creator plugin's esbuild-in-browser
// pipeline (see docs/design-creator.md §6.1).
const LIBS = []

const here = (p) => new URL(p, import.meta.url)

const manifest = {}
for (const { specifier, entryPoint, outfile, subdeps = [] } of LIBS) {
  await esbuild.build({
    entryPoints: [entryPoint],
    bundle: true,
    format: 'esm',
    platform: 'browser',
    minify: true,
    outfile: here(outfile).pathname,
    external: [],
  })
  const src = readFileSync(here(outfile), 'utf8')
  if (/^\s*import\s.+\sfrom\s/m.test(src) || /^\s*export\s.+\sfrom\s/m.test(src))
    throw new Error(`${outfile} has a residual import statement`)
  manifest[specifier] = { file: outfile, subdeps }
}

// Vendor esbuild-wasm's browser driver + wasm binary alongside the libs.
copyFileSync(
  here('./node_modules/esbuild-wasm/esbuild.wasm'),
  here('./esbuild.wasm')
)
copyFileSync(
  here('./node_modules/esbuild-wasm/lib/browser.min.js'),
  here('./esbuild.js')
)

writeFileSync(here('./MANIFEST.json'), JSON.stringify(manifest, null, 2) + '\n')

console.log(`build ok (${Object.keys(manifest).length} libs)`)
