// Facade over `marked` (markdown -> HTML) + `mermaid` (diagram -> inline SVG)
// for Task 29's standalone `.html` export — cr_store.export_artifact() inlines
// this bundle verbatim as a <script type="module"> so a markdown/mermaid
// export renders fully offline, no server. Mirrors the tailwind-entry.js /
// codemirror-entry.js pattern: this file IS the bundle's entry point, built
// by build.mjs's LIBS loop like any other lib.
import { marked } from 'marked'
import mermaid from 'mermaid'

mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' })

function renderMarkdown(source) {
  return marked.parse(source || '')
}

async function renderMermaidInto(container, source) {
  const id = 'mmd-' + Math.random().toString(36).slice(2)
  const { svg, bindFunctions } = await mermaid.render(id, source || '')
  container.innerHTML = svg
  if (bindFunctions) bindFunctions(container)
}

export { renderMarkdown, renderMermaidInto }
