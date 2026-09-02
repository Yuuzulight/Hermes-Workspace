import { copyFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import * as esbuild from 'esbuild'

// React is vendored once; every other lib is built with it external so the
// Task 19 vfs resolver can supply a single shared copy at artifact-build time
// (see docs/design-creator.md §6.1/§6.2).
const REACT_EXTERNAL = ['react', 'react-dom', 'react/jsx-runtime']

// Each entry bundles a browser-side library into an ESM file (importing only
// from REACT_EXTERNAL, nothing else) consumed by the Creator plugin's
// esbuild-in-browser pipeline (see docs/design-creator.md §6.1).
const LIBS = [
  { specifier: 'react', entryPoint: 'react', outfile: './react.js' },
  { specifier: 'react-dom', entryPoint: 'react-dom', outfile: './react-dom.js' },
  { specifier: 'react-dom/client', entryPoint: 'react-dom/client', outfile: './react-dom-client.js' },
  { specifier: 'react/jsx-runtime', entryPoint: 'react/jsx-runtime', outfile: './react-jsx-runtime.js' },
  { specifier: 'recharts', entryPoint: 'recharts', outfile: './recharts.js' },
  { specifier: 'lucide-react', entryPoint: 'lucide-react', outfile: './lucide-react.js' },
  { specifier: 'd3', entryPoint: 'd3', outfile: './d3.js' },
  { specifier: 'three', entryPoint: 'three', outfile: './three.js' },
  { specifier: '@react-three/fiber', entryPoint: '@react-three/fiber', outfile: './react-three-fiber.js' },
  { specifier: 'papaparse', entryPoint: 'papaparse', outfile: './papaparse.js' },
  { specifier: 'xlsx', entryPoint: 'xlsx', outfile: './xlsx.js' },
  { specifier: 'mathjs', entryPoint: 'mathjs', outfile: './mathjs.js' },
  { specifier: 'tone', entryPoint: 'tone', outfile: './tone.js' },
  { specifier: '@tanstack/react-table', entryPoint: '@tanstack/react-table', outfile: './tanstack-react-table.js' },
  { specifier: 'lodash', entryPoint: 'lodash', outfile: './lodash.js' },
  { specifier: 'date-fns', entryPoint: 'date-fns', outfile: './date-fns.js' },
  { specifier: 'framer-motion', entryPoint: 'framer-motion', outfile: './framer-motion.js' },
  { specifier: 'clsx', entryPoint: 'clsx', outfile: './clsx.js' },
  { specifier: 'tailwind-merge', entryPoint: 'tailwind-merge', outfile: './tailwind-merge.js' },
  { specifier: 'class-variance-authority', entryPoint: 'class-variance-authority', outfile: './class-variance-authority.js' },
]

const here = (p) => new URL(p, import.meta.url)

const manifest = {}
for (const { specifier, entryPoint, outfile } of LIBS) {
  const result = await esbuild.build({
    entryPoints: [entryPoint],
    bundle: true,
    format: 'esm',
    platform: 'browser',
    minify: true,
    define: { 'process.env.NODE_ENV': '"production"' },
    outfile: fileURLToPath(here(outfile)),
    external: REACT_EXTERNAL,
    metafile: true,
  })
  // esbuild guarantees bundle:true inlines everything except `external`, so
  // the only imports that can survive are the shared React ones — read the
  // metafile (real parsed imports) instead of pattern-matching the output
  // text, which false-positives on strings that merely look like imports.
  const output = Object.values(result.metafile.outputs)[0]
  const subdeps = [...new Set(output.imports.filter((i) => i.external).map((i) => i.path))]
  manifest[specifier] = { file: outfile.replace(/^\.\//, ''), subdeps }
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
