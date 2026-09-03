// Facade over `mermaid` (diagram -> inline SVG) for Task 29's standalone
// `.html` export — cr_store.py's _viewer_doc inlines this bundle verbatim as
// a <script type="module"> so a mermaid export renders fully offline, no
// server. Split from the combined marked+mermaid viewer.js (final-review
// Fix 4) — see viewer-md-entry.js for the marked-only counterpart, used for
// markdown exports (which never invoke mermaid rendering). Mirrors the
// tailwind-entry.js / codemirror-entry.js pattern: this file IS the bundle's
// entry point, built by build.mjs's LIBS loop like any other lib.
import mermaid from 'mermaid'

mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' })

async function renderMermaidInto(container, source) {
  const id = 'mmd-' + Math.random().toString(36).slice(2)
  const { svg, bindFunctions } = await mermaid.render(id, source || '')
  container.innerHTML = svg
  if (bindFunctions) bindFunctions(container)
}

export { renderMermaidInto }
