import { readFileSync } from 'node:fs'
import { execSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import * as esbuild from 'esbuild'
const man = JSON.parse(readFileSync(new URL('./MANIFEST.json', import.meta.url)))
// React is the one shared runtime (Task 19's vfs resolver supplies it once);
// every other bare import means a lib's bundle isn't self-contained.
const REACT_EXTERNAL = ['react', 'react-dom', 'react/jsx-runtime']
for (const { file, subdeps = [] } of Object.values(man)) {
  const abs = fileURLToPath(new URL(file, import.meta.url))
  execSync(`node --check "${abs}"`)
  // Parse (don't bundle) to read the file's real static imports off esbuild's
  // metafile — robust against strings/comments that merely look like imports.
  const { metafile } = esbuild.buildSync({
    entryPoints: [abs], bundle: false, write: false, metafile: true, format: 'esm', platform: 'browser',
  })
  const imports = metafile.inputs[Object.keys(metafile.inputs)[0]].imports
  for (const { path } of imports) {
    if (!REACT_EXTERNAL.includes(path)) throw new Error(`${file} has a bare import of "${path}"`)
    if (!subdeps.includes(path)) throw new Error(`${file} imports "${path}" but it's missing from subdeps`)
  }
}
const wasm = readFileSync(new URL('./esbuild.wasm', import.meta.url))
if (wasm[0] !== 0x00 || wasm[1] !== 0x61) throw new Error('bad wasm magic')

const need = ['react','react-dom','recharts','lucide-react','d3','three','@react-three/fiber',
  'papaparse','xlsx','mathjs','tone','@tanstack/react-table','lodash','date-fns',
  'framer-motion','clsx','tailwind-merge','class-variance-authority','tailwind','codemirror','viewer']
for (const s of need) if (!man[s]) throw new Error(`MANIFEST missing ${s}`)

console.log('verify ok')
