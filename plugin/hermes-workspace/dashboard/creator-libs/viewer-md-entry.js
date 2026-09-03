// Facade over `marked` (markdown -> HTML) for Task 29's standalone `.html`
// export — cr_store.py's _viewer_doc inlines this bundle verbatim as a
// <script type="module"> so a markdown export renders fully offline, no
// server. Split from the combined marked+mermaid viewer.js (final-review
// Fix 4): renderMarkdown never invokes mermaid, so a markdown export was
// paying for the ~3.5MB mermaid bundle it never used — see
// viewer-mermaid-entry.js for the mermaid-only counterpart. Mirrors the
// tailwind-entry.js / codemirror-entry.js pattern: this file IS the bundle's
// entry point, built by build.mjs's LIBS loop like any other lib.
import { marked } from 'marked'

function renderMarkdown(source) {
  return marked.parse(source || '')
}

export { renderMarkdown }
