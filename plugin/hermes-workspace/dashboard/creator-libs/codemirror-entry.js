// Small hand-written facade so the Task 27 renderer wrapper stays tiny — it
// imports one zero-import bundle instead of wiring up a dozen @codemirror/*
// and @lezer/* packages itself. Mirrors the tailwind-entry.js pattern (see
// that file's header): a source file that IS this bundle's entry point,
// bundled by build.mjs's LIBS loop like any other lib.
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection } from '@codemirror/view'
import { EditorState, Compartment } from '@codemirror/state'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { syntaxHighlighting, defaultHighlightStyle, bracketMatching, indentOnInput } from '@codemirror/language'
import { search, searchKeymap, highlightSelectionMatches } from '@codemirror/search'
import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from '@codemirror/autocomplete'
import { oneDark } from '@codemirror/theme-one-dark'
import { javascript } from '@codemirror/lang-javascript'
import { html } from '@codemirror/lang-html'
import { css } from '@codemirror/lang-css'
import { python } from '@codemirror/lang-python'
import { markdown } from '@codemirror/lang-markdown'

const LANGS = {
  javascript: () => javascript({ jsx: true, typescript: true }),
  html: () => html(),
  css: () => css(),
  python: () => python(),
  markdown: () => markdown(),
}

// "Batteries included" extension set: line numbers, syntax highlighting for
// `lang`, bracket matching, search, autocomplete, history/undo, standard
// editing/history/search keymaps, and the dark theme when `dark` is truthy.
// No save keymap here — Task 27's renderer wrapper owns Mod-s.
function basicExtensions(lang, dark) {
  const langExt = LANGS[lang]
  return [
    lineNumbers(),
    highlightActiveLine(),
    highlightActiveLineGutter(),
    drawSelection(),
    history(),
    indentOnInput(),
    bracketMatching(),
    closeBrackets(),
    autocompletion(),
    highlightSelectionMatches(),
    search(),
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    ...(langExt ? [langExt()] : []),
    ...(dark ? [oneDark] : []),
    keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...searchKeymap, ...historyKeymap, ...completionKeymap]),
  ]
}

// EditorState.readOnly is a Facet (no .reconfigure — that's Compartment API);
// this just hands back the extension so the caller can drop it in a
// Compartment of their own if they need to flip it later.
function readOnly(value) {
  return EditorState.readOnly.of(value)
}

export { EditorView, EditorState, basicExtensions, readOnly, Compartment }
