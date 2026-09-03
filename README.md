# Hermes Workspace

Your Obsidian vault as long-term memory and durable output for Hermes Desktop.
No AI provider owns your memory — the vault does. Works the same whichever
model you have active.

Two modules ship in this plugin:

## Knowledge

- **Read** — a composer toggle. When on, relevant notes from your vault are
  pulled in and prepended to your message before it is sent.
- **Write** — a command. Your active model extracts candidate memories from a
  finished chat; you approve each; approved ones are appended to your notes,
  following your vault's own capture conventions.

## Creator

Durable, versioned artifacts your agent can produce and revise across a
session, backed by their own store (`cr_store.py`) rather than living only in
chat scrollback. The agent gets three tools:

- **`create_artifact`** — save a new artifact (code, HTML, SVG, Markdown, or
  Mermaid) with a title and content.
- **`update_artifact`** — save a new version of an existing artifact.
- **`read_artifact`** — read back the current (or a prior) version.

A **Creator** pane docks beside Knowledge: it lists artifacts, renders a live
preview per type, and lets you step through and restore prior versions.

**Types** — `code`, `html`, `svg`, `markdown`, `mermaid`, and `react`: an
artifact whose default export is a React component runs live in a sandboxed
preview iframe, bundled per artifact with a real esbuild build and Tailwind
compilation. A syntax error surfaces as inline diagnostics instead of a blank
frame; a runtime error shows an error strip; `console.log`/`console.error`
from the component surface in a console pane. Bundles and Tailwind output are
cached by content hash, so re-stepping to an already-rendered version is
instant.

`creator-libs/` (`plugin/hermes-workspace/dashboard/creator-libs/`) vendors
what the React runtime needs — esbuild-wasm plus 20 npm libraries and a
Tailwind build, ~19 MB committed to the repo (not gitignored; only
`creator-libs/node_modules/` is). It's rebuilt with
`node plugin/hermes-workspace/dashboard/creator-libs/build.mjs` and checked
with `node plugin/hermes-workspace/dashboard/creator-libs/verify.mjs` — see
Development below.

**Editor** — the pane edits artifacts with a real CodeMirror 6 (syntax
highlighting per type/language, search, bracket matching). If the vendored
CodeMirror asset fails to load, editing falls back to the plain Phase 1
`Textarea` instead of breaking.

**Export** — the "Export" button writes the current artifact out as a
standalone, portable `.html` file (`react` artifacts get their bundle +
compiled Tailwind CSS inlined, no compiler or preview-bridge code in the
output). The exported file works fully offline — open it directly in any
browser, no server involved.

**Publish** — the "Publish" button posts the same standalone HTML to a
public GitHub Gist via the `gh` CLI and returns a shareable URL; a raw-render
link is also returned for every published type. `gh` is **optional**: if it isn't
installed and authenticated, Publish doesn't fail silently — it shows a
notice explaining how to enable it (`install gh and run gh auth login, or
set a token in Creator settings`).

## Requirements

- Hermes Desktop 0.20.x
- An Obsidian vault (a folder of `.md` files) — required for Knowledge; not
  needed to use Creator on its own
- Python with SQLite FTS5 (Hermes bundles this)

## Install

1. Copy `plugin/hermes-workspace/` into `~/.hermes/plugins/hermes-workspace/`
   (`%LOCALAPPDATA%\hermes\plugins\hermes-workspace\` on Windows). The folder
   is ~20 MB thanks to `creator-libs/`'s vendored esbuild + npm libraries —
   ships as part of the copy, no separate install step.
2. Run `hermes plugins enable hermes-workspace`. This adds the plugin to
   `plugins.enabled` in `~/.hermes/config.yaml` and enables the agent +
   dashboard halves of **both** Knowledge and Creator:
   ```yaml
   plugins:
     enabled:
       - hermes-workspace
   ```
3. Restart Hermes Desktop. The renderer half (both panes) auto-loads on
   discovery — no Settings toggle needed to turn it on. It can be *disabled*
   afterwards in **Settings → Plugins**.
4. `create_artifact` / `update_artifact` / `read_artifact` appear to the
   agent alongside the existing Knowledge tools, and "Open Creator" appears
   in the command palette. The Creator pane docks beside Knowledge. For
   Knowledge specifically, open it via the "Toggle Knowledge" command and set
   your vault folder in the plugin settings.
5. Optional:
   - Knowledge: drop an `agent_rules.md` in your vault (or point `rules_file`
     at one) to override the default capture conventions. See
     `plugin/hermes-workspace/dashboard/default_rules.md` for the defaults.
   - Creator: `project_root` and `github_token` in the Creator settings
     (`github_token` is only needed for Phase 3's Gist publish).

## How memories are written

Each approved memory is one dated bullet appended under a note's `## History`
section:

```
- **2026-08-30** — The user moved the project's CI to a self-hosted runner.
```

Byte-identical to a line you would type yourself — no metadata, no markers.
Dated cross-project facts also get a line in `Timeline/<year>.md`. New notes are
created with a plain `# Title` and `## History` — no YAML frontmatter.

Every write shows a diff first. A `.bak` of each touched note is kept, and
**Undo last memory extraction** reverts the most recent batch.

## Cautions

- **Apply memories with the target notes closed in Obsidian**, and pause Obsidian
  Sync if you can — a note being rewritten by Sync mid-write can lose the change.
- The read path puts note text in front of the model. A note containing
  instruction-like text ("ignore previous instructions…") will be shown to the
  model. Keep an eye on what's in your vault; a folder-exclusion setting is
  planned.

## Development

```bash
cd plugin/hermes-workspace/dashboard
python selftest.py
```

Framework-free; runs every module self-check plus the full HTTP read + write +
reversible round-trip for both modules — 12 checks in all: 6 Knowledge module
checks, 3 Knowledge HTTP checks, and one each for `cr_store`, the Creator HTTP
router, and the defensive mount (missing Creator dependency degrades cleanly
instead of taking Knowledge down with it).

The renderer half (`desktop/plugin.js`) is a single file with no build step —
edit and Hermes hot-reloads it. Syntax-check it directly:

```bash
node --check plugin/hermes-workspace/desktop/plugin.js
```

`creator-libs/` is rebuilt and re-verified separately when its pinned
versions change:

```bash
cd plugin/hermes-workspace/dashboard/creator-libs
node build.mjs   # re-vendor esbuild-wasm + the npm libraries
node verify.mjs  # check every vendored file loads and matches MANIFEST.json
```

## Manual verification

`selftest.py` and `node --check` cover everything scriptable without a real
Hermes Desktop. The renderer behavior below needs a human with an actual
install — walk this after any Creator change, per design-creator.md §9.2:

- Create an artifact from a chat via a tool call (`create_artifact`) and
  confirm it shows up in the Creator pane.
- Edit it and Save — a new version is created, not an overwrite.
- Step the version stepper back and forward, then restore an older version.
- Each of the 5 Phase 1 types — `code`, `html`, `svg`, `markdown`, `mermaid`
  — renders correctly in the preview.
- The artifact picker lists and switches between artifacts correctly.
- Kill and restart the dashboard process while the pane is open, and confirm
  the pane retries gracefully instead of getting stuck or erroring out.

Phase 2 (`type=react`), per design-creator.md §9.2:

- A `react` artifact whose default export is a component runs in the
  preview iframe.
- A syntax error in the artifact shows inline diagnostics, not a blank or
  broken frame.
- A runtime error (thrown during render) shows the error strip instead of a
  blank frame.
- `console.log` / `console.error` calls in the component show up in the
  console pane.
- Arbitrary Tailwind utility classes render correctly in the preview.
- Editing the artifact triggers a debounced re-render (not one rebuild per
  keystroke).
- Stepping the version stepper to an already-seen version is instant — served
  from the bundle/Tailwind cache, not rebuilt.

Phase 3 (CodeMirror editor, export, publish), per design-creator.md §9.2 —
**not run as part of this change; walk it after any Creator change**:

- The CodeMirror editor loads and highlights correctly for each artifact
  type/language; forcing the CodeMirror asset load to fail falls back to the
  Phase 1 `Textarea` instead of breaking editing.
- Edit + Save still works through the new editor (new version created, dirty
  dot behaves, ⌘S works, read-only on a non-latest version).
- Exporting each artifact type produces a valid standalone `.html` file that
  opens correctly in a real browser with no server running.
- The `react` export specifically: no console errors on open, and no
  orphaned bridge/`postMessage` code in the output (it's a real standalone
  doc, not a leftover preview iframe payload) — and it's styled (the
  compiled Tailwind CSS is inlined, not missing).
- Publish, with `gh` installed and authenticated, returns a working Gist URL
  that opens the published artifact.
- Publish, without `gh` configured, shows the "how to enable" notice instead
  of failing silently or throwing.
