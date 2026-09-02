import { readFileSync } from 'node:fs'
import { execSync } from 'node:child_process'
const man = JSON.parse(readFileSync(new URL('./MANIFEST.json', import.meta.url)))
for (const { file } of Object.values(man)) {
  execSync(`node --check "${new URL(file, import.meta.url).pathname}"`)
  const src = readFileSync(new URL(file, import.meta.url), 'utf8')
  if (/^\s*import\s.+\sfrom\s/m.test(src) || /^\s*export\s.+\sfrom\s/m.test(src))
    throw new Error(`${file} has a bare import`)
}
const wasm = readFileSync(new URL('./esbuild.wasm', import.meta.url))
if (wasm[0] !== 0x00 || wasm[1] !== 0x61) throw new Error('bad wasm magic')
console.log('verify ok')
