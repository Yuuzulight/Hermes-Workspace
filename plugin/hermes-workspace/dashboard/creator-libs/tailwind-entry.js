// Wrapper around tailwindcss v4's core compiler (see docs/design-creator.md
// §6.3 / task-20-report.md for why this package and not @tailwindcss/browser
// — the latter is DOM-coupled (querySelectorAll/MutationObserver, injects its
// own <style> tag) with no pure candidate-driven function; `tailwindcss`
// itself exports the same `compile()` that package uses internally, and it
// takes an explicit candidate list rather than scanning files/DOM.
//
// tailwindcss's own `theme.css` (default color/spacing/font scale) is plain
// `@theme default { ... }` — bundled here as text (via esbuild's `.css`
// loader override, see build.mjs) and prepended ahead of the caller's CSS so
// ordinary utilities (`p-4`, `grid-cols-3`, ...) resolve offline, with no
// `@import` / `loadStylesheet` filesystem hook needed.
import { compile as twCompile } from 'tailwindcss'
import defaultThemeCss from 'tailwindcss/theme.css'

// Tailwind only emits a plain `@theme { ... }` custom property into the
// output CSS if some candidate's utility actually references it — fine for
// scanning real source files, wrong here: a Creator preview's @theme tokens
// are often consumed via inline `style={{...}}`, outside what the class-attr
// candidate sweep below can see, and would otherwise vanish silently. Force
// caller-supplied blocks to `@theme static` (always emitted) so they don't;
// leave `@theme default`/`@theme inline`/`@theme static` (already-qualified,
// including the embedded default theme below) alone.
const FORCE_STATIC_THEME_RE = /@theme(\s*)\{/g

export async function compile(baseCss, opts = {}) {
  const candidates = (opts && opts.candidates) || []
  const forced = (baseCss || '').replace(FORCE_STATIC_THEME_RE, '@theme static$1{')
  const full = defaultThemeCss + '\n' + forced
  const result = await twCompile(full, {})
  return result.build(candidates)
}
