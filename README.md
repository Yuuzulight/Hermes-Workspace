# Hermes Workspace

Your Obsidian vault as long-term memory for Hermes Desktop. No AI provider owns
your memory — the vault does. Works the same whichever model you have active.

v1 ships the **Knowledge** module:

- **Read** — a composer toggle. When on, relevant notes from your vault are
  pulled in and prepended to your message before it is sent.
- **Write** — a command. Your active model extracts candidate memories from a
  finished chat; you approve each; approved ones are appended to your notes,
  following your vault's own capture conventions.

## Requirements

- Hermes Desktop 0.20.x
- An Obsidian vault (a folder of `.md` files)
- Python with SQLite FTS5 (Hermes bundles this)

## Install

1. Copy `plugin/hermes-workspace/` into `~/.hermes/plugins/`
   (`%LOCALAPPDATA%\hermes\plugins\` on Windows).
2. Add the plugin to the backend allow-list in `~/.hermes/config.yaml`:
   ```yaml
   plugins:
     enabled:
       - hermes-workspace
   ```
3. Restart Hermes Desktop.
4. Open the **Knowledge** pane via the "Toggle Knowledge" command in the
   command palette, then set your vault folder in the plugin settings.
5. Optional: drop an `agent_rules.md` in your vault (or point `rules_file` at
   one) to override the default capture conventions. See
   `plugin/hermes-workspace/dashboard/default_rules.md` for the defaults.

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
reversible round-trip. The renderer half (`desktop/plugin.js`) is a single file
with no build step — edit and Hermes hot-reloads it.
