# Creator module — full design (Phases 1–4)

Status: draft for review (revised after the three-lens source review)
Last updated: 2026-08-31
Supersedes: `design-creator-sp1.md`

## 1. Purpose

A Claude-Artifacts equivalent, shipped as a second module (**Creator**) inside the
existing `hermes-workspace` plugin alongside **Knowledge**. The model creates
durable, versioned artifacts through explicit tools; a docked pane previews,
edits, versions, and (Phase 2) runs them; Phase 3 adds a real editor and
export/publish; Phase 4 gives artifacts a `window.hermes` runtime.

One plan, four phases. Each phase is a working, useful increment:

| Phase | Delivers | Standalone value |
|---|---|---|
| **1** | 3 agent tools + a transcript-scan fallback + a persistent versioned store + a pane with a textarea editor, a per-type preview, and a version stepper. Types: code, HTML, SVG, Markdown, Mermaid. | Durable versioned artifacts you can edit and preview. |
| **2** | `type: react` — an esbuild-wasm runtime that bundles the artifact + its imports (the full Claude import set, vendored) into a sandboxed iframe; per-artifact Tailwind compiled in the plugin renderer; render-error surfacing + a plain console pane. | React/JSX artifacts run live with full Tailwind. |
| **3** | A real CodeMirror 6 editor (replaces the textarea) + standalone `.html` export (everything inlined) + Publish to Gist. | A proper editor; artifacts leave Hermes as portable files or links. |
| **4** | A `window.hermes` bridge into the artifact iframe: `complete`, `storage`, `readFile` / `readdir`. | Artifacts become interactive AI mini-apps. |

Not built: artifact sharing between users; Monaco (~5 MB, AMD loader, workers —
CodeMirror 6 is the editor); first-party artifact hosting (Gist + a documented
self-host option instead); Hermes' built-in artifact system (left alone —
Creator runs in parallel).

## 2. What Hermes already provides

Hermes Desktop ships a built-in artifact system
(`apps/desktop/src/store/artifacts.ts`,
`apps/desktop/src/app/chat/right-rail/preview-artifact.tsx`): auto-promotion of
substantial fenced blocks, a version stepper, a sandboxed
`<iframe sandbox="allow-scripts">` renderer, rendered/source toggle, download.
It is **memory-only** (a renderer atom rebuilt from the transcript on reload)
and **app-internal** (not importable by a plugin). It caps itself
(`MAX_VERSIONS_PER_ARTIFACT=20`, `MAX_ARTIFACTS_PER_SESSION=24`) but has no
persistence, no diff, no restore, no JSX.

Creator's value: a persistent versioned store, explicit `create_artifact` /
`update_artifact` / `read_artifact` tools (like Claude's `<antArtifact
identifier=…>`), a real editor, a cross-session picker, a React runtime, export,
and an in-artifact runtime. The two systems do not interact; when both surface
the same fenced block, that is a documented, accepted duplicate.

## 3. Source-verified constraints (Hermes 0.20.6)

Every claim below was checked against the real tree at
`C:\Users\User\AppData\Local\hermes\hermes-agent` by three independent reviews.

### 3.1 Plugin loading and gating

- `hermes-workspace` is a **user directory plugin**. Its **agent + dashboard**
  halves are gated by `plugins.enabled` in `~/.hermes/config.yaml`
  (`hermes_cli/plugins.py:4464-4478`, `web_server.py:19185-19197`).
- The **desktop renderer** half is **not** gated by `config.yaml`. The runtime
  loader activates a discovered plugin when `pluginActive(id, defaultEnabled ??
  true)` — an *undecided* plugin runs by default (`contrib/runtime-loader.ts:190`,
  `contrib/plugins-store.ts:32` records only *explicit* user choices). So once
  `~/.hermes/plugins/hermes-workspace/` exists and Hermes restarts, `plugin.js`
  loads with **no toggle**; the user can *disable* it in **Settings → Plugins**,
  but nothing needs to be turned on. (The earlier draft's "must also toggle
  Settings→Plugins" was wrong.)
- A directory plugin needs `plugin.yaml` **and** a root `__init__.py` exporting
  `def register(ctx)` (`plugins.py:5461-5463`; missing → `Failed to load plugin
  hermes-workspace: No __init__.py`). `hermes-workspace` has none today, so its
  agent side currently fails harmlessly. Adding it makes the agent half load;
  desktop and dashboard discovery are unaffected.
- `plugin.yaml` declares **`kind: standalone`**. `_detect_kind_from_source` is
  skipped whenever **any** `kind:` key is present (`plugins.py:4801`), so this is
  doubly safe; without it the loader scans the first 8192 chars of `__init__.py`
  for `register_memory_provider` / `MemoryProvider` / `register_provider` +
  `ProviderProfile` (`plugins.py:997-1000`) and would reroute the plugin to the
  memory system.
- `provides_tools` is **display only** for a `kind: standalone` user plugin
  (`hermes plugins list` reads it; the `register_tools(ctx)` predeclare path is
  platform-only, `plugins.py:5016-5088`). Tools register via
  `ctx.register_tool(...)` in `register(ctx)`. **Do not name the tool file
  `tools.py`** — that filename + `provides_tools` is exactly the deferred-platform
  client-tools shape and a latent footgun; use `cr_tools.py`.
- `provides_hooks` has zero loader consumers — omit it.

### 3.2 Agent-tool handlers

- Run **in-process in the agent, no sandbox**. `def handler(args: dict, **kwargs)
  -> str`; `kwargs` carries `task_id`, `session_id`, `user_task` (not
  `enabled_tools` — `execute_code`-only, `model_tools.py:1546-1559`).
- Schema passed **in Python** to `ctx.register_tool(name, toolset, schema,
  handler, ...)`; toolset `"creator"`.
- Return a **string** (`tools.registry.tool_result()` / `tool_error()`). A
  handler that raises is caught by `registry.dispatch` (`tools/registry.py:1147-1155`)
  and returned as an opaque `{error}` — so **don't raise**, return `tool_error()`.
- `kwargs["session_id"]` **equals** `sessions.id`, the `YYYYMMDD_HHMMSS_xxxxxx`
  stored key (`model_tools.py:1553-1559`). An in-process subagent's tool call
  receives the **parent's** `session_id` (not empty); only a genuine
  pre-first-prompt draft turn yields `""`.
- **The renderer has two session ids** and Creator must use the right one at each
  call site (the shipped `plugin.js:722-728` learned this the hard way):
  - `host.state.focusedStoredSessionId` — the stored key, **equals**
    `kwargs["session_id"]`. Use for: the scan POST body, `GET
    /creator/artifacts?session_id=`, and "auto-follow the focused session".
  - `host.state.focusedSessionId || host.state.activeSessionId` — the **runtime**
    id (can be `""`, so `||` not `??`). Use for: `llm.oneshot` (Phase 4). A
    stored key here makes `llm.oneshot` fall back to the weak aux model.

### 3.3 System-prompt section

`ctx.register_system_prompt_section(id, content, *, position="after_memory",
max_chars=4000)` — `position`/`max_chars` keyword-only; `id` matches
`^[a-z0-9][a-z0-9._-]{0,127}$`; rendered **once at session start**, frozen into
the persisted prompt (cache-safe); duplicate `id` raises
(`plugins.py:3412-3446, 558-563`). `max_chars=4000` is the exact ceiling
(`0 < max_chars <= 4000`) — zero headroom. The 8000-char / 32-section aggregate
is shared across **all** plugin sections process-wide (fine now — Knowledge
registers none). The host frames the section as `## Plugin Context:
hermes-workspace`, so the content must **not** start with its own `##` heading.

### 3.4 Store: processes and SQLite

- Agent process and dashboard web server are separate siblings; no localhost HTTP
  path between them; they coordinate through the filesystem under `~/.hermes/`.
- Data dir: `plugin_storage.plugin_data_dir("hermes-workspace")` →
  `<HERMES_HOME>/plugin-data/hermes-workspace/`, profile-aware, resolved through
  `get_hermes_home()` **every call** (never cache a `Path`). **Not** the install
  dir `<HERMES_HOME>/plugins/hermes-workspace/data/` (which `hermes plugins
  update` clobbers — `hw_store.py` uses that; Creator must not).
- **Creator does not use `plugin_storage.plugin_db()`.** That helper rejects any
  filename containing a slash (`Path(filename).name != filename`,
  `plugin_storage.py:74`) and places the DB directly in `plugin_data_dir(name)`,
  so it cannot put the index inside `creator/`. Instead `cr_store` opens its own
  `sqlite3.connect(<creator/>/creator-index.db)` and applies, per connection:
  `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `PRAGMA
  busy_timeout=5000`, `isolation_level=None` (for explicit `BEGIN IMMEDIATE`),
  `check_same_thread=False`; `try/finally: conn.close()`. This is plain stdlib —
  so the "stdlib + FastAPI only" module rule (3.5) holds and `python
  dashboard/selftest.py` works with `plugins` off `sys.path`.
- Finding `plugin_data_dir`: a **function-body** `try: from plugins.plugin_storage
  import plugin_data_dir except Exception:` with a local `get_hermes_home`
  fallback (the `plugins/hermes-achievements/dashboard/plugin_api.py:152-161`
  pattern). Never at module scope.
- **Cross-process writes**: both processes may open the same DB file. `BEGIN
  IMMEDIATE` + `busy_timeout=5000` is the only coordination. On
  `sqlite3.OperationalError` (lock timeout) retry 3× with ~100 ms backoff; then
  return `{error:"busy"}` / `tool_error("store busy, retry")`.

### 3.5 Dashboard module loading

- `_mount_plugin_api_routes` execs `dashboard/plugin_api.py` as a bare top-level
  module (no `submodule_search_locations`) inside **one** `try/except`
  (`web_server.py:19232-19257`). **Any** import-time exception in a pulled-in file
  unmounts **all** of `hermes-workspace`'s routes. So `plugin_api.py` gains
  `import logging` and:
  ```python
  try:
      import cr_api
      router.include_router(cr_api.router)
  except Exception as e:
      logging.getLogger(__name__).warning("creator API not mounted: %s", e)
  ```
- Only `dashboard/` is on `sys.path` (`plugin_api.py:8`). **Never add the plugin
  root** — a root `cr_tools.py`/`cr_store.py` there is fine, but a root `tools.py`
  would shadow Hermes' own `tools/` package (`from tools.registry import
  registry`). `cr_api.py` loads `cr_store.py` by **explicit path**
  (`spec_from_file_location`).
- `cr_store.py` and `cr_api.py`: **module-level imports = stdlib + FastAPI only.**
  `plugin_data_dir` and `SessionDB` imports live in function bodies,
  try/except-guarded. `cr_store.py` is also imported by `__init__.py` as `from .
  import cr_store` on the agent side, so it must be import-safe **both** ways: no
  relative imports, no top-level `hermes_*`.
- `cr_api.router = APIRouter(prefix="/creator")` — **every** Creator route lives
  under `/api/plugins/hermes-workspace/creator/…` so it can never shadow a future
  Knowledge route (`/status`, `/config`, `/search`, `/note`, `/tree`,
  `/reindex`, `/memories/*`).

### 3.6 Renderer

- One `desktop/plugin.js`; imports only `@hermes/plugin-sdk`, `react`,
  `react/jsx-runtime`; **any** other specifier is an up-front load error
  (`contrib/runtime-loader.ts:69-86`); no build step; read via
  `hermes:readPluginSource`, 16 MB cap. Current file ~42 KB / 1228 lines.
- **No CSP on the renderer** — no `<meta>`, no `onHeadersReceived`, no server
  header (confirmed; `runtime-loader.ts:26` notes no boundary exists yet).
  `WebAssembly.compile` / `instantiate`, blob Web Workers, and sandboxed `srcdoc`
  iframes with inline `<script>` all run freely **in the renderer**.
- **`ctx.rest` is JSON-only.** It resolves to `pluginRest` →
  `window.hermesDesktop.api` → `handleHermesApiRequest` → `fetchJson`
  (`electron/main.ts:5044-5086`), which reads every body as utf-8, **rejects**
  HTML / `text/html`, and **always** `JSON.parse`s — a raw `.wasm` or `.js` body
  throws `Invalid JSON`. `PluginRestOptions` has no `responseType`/`arraybuffer`;
  the default timeout is 30 s (`electron/hardening.ts:16`). There is no other
  binary path a plugin can reach.
  **⇒ every vendored asset is delivered as a JSON envelope:** `GET
  /creator/asset/{name}` → `{name, encoding: "utf8" | "base64", data, sha256}`;
  the renderer decodes (`atob` → `Uint8Array` for wasm, string for text). Pass an
  explicit `timeoutMs` (e.g. 120 000) for the ~13 MB base64 esbuild body; cache
  after the first load. `readFile` (Phase 4) uses the same envelope.
- **An iframe cannot `import` from any Hermes URL** (opaque origin, no
  `allow-same-origin`, CORS-blocked, `.wasm` excluded from
  `/dashboard-plugins/`). Code enters an iframe only inlined into its `srcdoc`.
- **Pane registration**: `host.openWorkspace` forces `placement:"main"` — a
  chat-area-takeover tab (`sdk/index.ts:1123-1139`), **not** a side pane.
  Knowledge registers a raw `PANES_AREA` pane, `data.placement:"right"`,
  `dock:{pane:"workspace", pos:"right"}` (`plugin.js:1125-1137`). **Creator
  registers the same way** so the two panes genuinely dock side by side. No
  `openWorkspace`.
- Every pane `render` is host-wrapped in `ContribBoundary` (an `ErrorBoundary`).
  Creator adds its **own inner `ErrorBoundary` per artifact** so one bad artifact
  doesn't blank the pane.
- `themePrelude()` (for iframe theming) must be **copied** from
  `inline-preview-directive.tsx` — it can't be imported. Tokens it resolves:
  `--ui-text-primary`, `--ui-text-tertiary`, `--ui-accent`,
  `--ui-stroke-tertiary`, `--ui-bg-editor`.
- SDK exports used directly: `Streamdown` (markdown + Shiki + Mermaid),
  `Textarea`, `Button`, `CopyButton`, `ConfirmDialog`, `Badge`, `EmptyState`,
  `StatusDot`, `useValue`, `atom`, `PANES_AREA`, `STATUSBAR_AREAS`,
  `PALETTE_AREA`, `host.*`, `ctx.*`. (`SegmentedControl` exists but the picker is
  a plain `<select>` — artifact count is unbounded.)
- `ctx.storage` is renderer-only `localStorage` — UI state only (last-open
  artifact, panel split).
- `ctx.os.revealPath` / `ctx.os.openExternal` (`contrib/plugin.ts:44-60`) are
  always present and **return `false`** when unavailable — branch on the return
  value, not on member presence. (Not `host.openExternal` — no such method.)

### 3.7 `llm.oneshot` and reading sessions

- `host.request('llm.oneshot', {instructions, input, session_id?, temperature?,
  max_tokens?})` → `{text}`, non-streaming (`methods_session.py:1496-1541`). It
  inherits the live agent's model **only** when `session_id` resolves to a
  session in the gateway's in-memory `_sessions` map (the runtime id of the
  currently-active chat); otherwise it silently uses the weak `task` aux backend
  (`methods_session.py:1491-1518`). On some builds the RPC is absent entirely —
  Knowledge already probes and degrades (`MISSING_RPC` regex,
  `plugin.js:629-644`); Phase 4 `complete()` does the same probe.
- The transcript scan reads **server-side** via `SessionDB` directly
  (`list_sessions_rich`, `get_messages`), as `hermes-achievements` does. Message
  `content` may be a JSON string or a list of typed blocks — flatten it (` `.join
  of block `text`, as `plugin_api.py:154-159` does) — and pass
  `include_compacted=True` or a compacted chat loses its older fenced blocks
  (`hermes_state.py:11998-12004`).
- A brand-new chat's `sessions` row is written lazily on the first prompt.

### 3.8 The `inline-preview-directive.tsx` bridge pattern (Phase 4 reference)

A per-mount nonce `Math.random().toString(36).slice(2)` is baked into an injected
`<script>` that `parent.postMessage({type, token, …}, "*")`. The parent adds a
global `message` listener and accepts a message only when `type` and `token`
match this mount, re-validating/clamping every field, throttled ~1/sec. The
reference sets **no `window.hermes`** (its scripts only post size/intent) —
Phase 4 builds `window.hermes` on top of the same nonce model. Creator holds its
single iframe's ref, so it **also** checks `event.source ===
frameRef.current?.contentWindow` (mandatory, not optional). No `MessageChannel`.

### 3.9 Product rule

No trace of AI authorship anywhere in the repo, code, comments, or commit
messages.

## 4. File layout

Added to `plugin/hermes-workspace/`:

```
plugin.yaml            + kind: standalone
                       + provides_tools: [create_artifact, update_artifact, read_artifact]
                         (+ nothing else; publish is a pane button, not a tool)
__init__.py            NEW, plugin root. def register(ctx): from . import cr_tools; cr_tools.register(ctx)
cr_tools.py            NEW, plugin root. Thin: arg-parse -> cr_store; the schema dicts;
                       ctx.register_tool(...) x3 + ctx.register_system_prompt_section(...).
cr_store.py            NEW, plugin root. stdlib only; guarded plugin_data_dir in a function body;
                       NO relative imports (imported both as a package submodule and by explicit path).
                       Owns ALL behaviour: identifier sanitize, create/update dispatch, type validation,
                       version writes, restore, session recording, dedup, scan thresholds, config,
                       storage (Phase 4). One monkeypatchable _read_assistant_messages(session_id)->list[str].
desktop/plugin.js      + a "// ===== CREATOR =====" block. Only new cr_* top-level symbols; references
                       ZERO Knowledge symbols; captures its own `const crCtx = ctx`. Knowledge's
                       register(ctx) SYNC body gains one line: `try { crRegister(ctx) } catch (e) {...}`,
                       after Knowledge's own contributions. Budget ~1200 -> ~3500 lines across phases.
                       Phase 2 adds the esbuild driver + iframe host + renderer-side Tailwind compile +
                       error/console strip; Phase 3 adds the CodeMirror blob-loader; Phase 4 the bridge.
dashboard/plugin_api.py  + import logging + the defensive `try: import cr_api` block (3.5).
dashboard/cr_api.py    NEW. stdlib + FastAPI only. Loads cr_store by explicit path.
                       router = APIRouter(prefix="/creator"). SessionDB import inside the scan handler.
                       Phase 2: GET /creator/asset/{name} -> JSON envelope.
dashboard/creator-libs/  NEW (Phase 2). Committed, ~17 MB. Produced by a pinned, committed build:
                       package.json + lockfile (exact versions) + build.mjs (pinned esbuild). Holds
                       esbuild.wasm, esbuild.js (browser driver), the full vendored ESM set + MANIFEST.json,
                       the Tailwind compiler, and (Phase 3) codemirror.js. Each ESM file is zero-import.
dashboard/selftest.py  + explicit-path cr_store load in the check list + a Creator HTTP round-trip. Additive.
```

## 5. Phase 1 — core artifact loop

### 5.1 Store on disk

```
<HERMES_HOME>/plugin-data/hermes-workspace/creator/
├── creator-index.db      # sqlite3.connect(...) directly (not plugin_db); authoritative
└── <dir>/
    ├── v1.<ext>
    ├── v2.<ext>
    ├── storage.json      # Phase 4 — artifact-owned KV (window.hermes.storage)
    ├── .cr-meta.json     # Phase 4 — Creator-internal (e.g. complete() first-call consent)
    └── files/            # Phase 4 (readFile scope 1)
```

The index is truth. A version file with no index row is an ignored orphan; an
index row whose file is missing reads as "version unavailable" (HTTP 410).

`<dir>` = the sanitized identifier: lowercase, keep `[a-z0-9._-]`, collapse `-`
runs, strip leading/trailing `-.`, reject `..` and backslashes, first char
alnum, cap 64 chars, empty → `artifact`. A collision with a *different*
identifier gets `-2`, `-3`, … chosen **inside the write transaction** (5.3).

### 5.2 Schema

```sql
CREATE TABLE artifacts (
  identifier    TEXT PRIMARY KEY,
  dir           TEXT NOT NULL UNIQUE,
  type          TEXT NOT NULL,   -- validated in cr_store; NO SQL CHECK (Phase 2 adds 'react' with no migration)
  language      TEXT,
  title         TEXT NOT NULL,
  origin        TEXT NOT NULL,   -- 'tool' | 'scan'
  created_at    REAL NOT NULL,
  updated_at    REAL NOT NULL
);
CREATE TABLE versions (
  identifier    TEXT NOT NULL REFERENCES artifacts(identifier) ON DELETE CASCADE,
  n             INTEGER NOT NULL,
  ext           TEXT NOT NULL,
  sha256        TEXT NOT NULL,   -- of normalized content (5.4)
  bytes         INTEGER NOT NULL,-- raw on-disk file size
  source        TEXT NOT NULL,   -- see 5.2.1
  restored_from INTEGER,
  created_at    REAL NOT NULL,
  PRIMARY KEY (identifier, n)
);
CREATE TABLE artifact_sessions (
  identifier    TEXT NOT NULL REFERENCES artifacts(identifier) ON DELETE CASCADE,
  session_id    TEXT NOT NULL,
  first_seen    REAL NOT NULL,
  PRIMARY KEY (identifier, session_id)
);
CREATE INDEX ix_versions_sha    ON versions(sha256);
CREATE INDEX ix_artifacts_updated ON artifacts(updated_at);
CREATE INDEX ix_artsess_session ON artifact_sessions(session_id);  -- the ~2s poll flags in_session across all artifacts
```

`ON DELETE CASCADE` on both child FKs (connections set `foreign_keys=ON`).

**5.2.1 `versions.source` — every value pinned to exactly one writer:**

| value | written by |
|---|---|
| `create` | `create_artifact` on a new identifier (`v1`) |
| `update` | `update_artifact` tool; or `create_artifact` re-hitting an existing identifier |
| `user-edit` | the pane's Save → `POST /creator/artifacts/{id}/versions {content}` |
| `restore` | `POST /creator/artifacts/{id}/versions {restore_from:n}` |
| `scan` | the transcript scan |

### 5.3 Write path — all in `cr_store.py`

Every mutating op:

1. Resolve the data dir; `sqlite3.connect(creator/creator-index.db)` **fresh**;
   apply the PRAGMAs (3.4).
2. `BEGIN IMMEDIATE`.
3. Inside the transaction: resolve/allocate `<dir>` —
   `SELECT dir FROM artifacts WHERE dir = ?1 OR dir GLOB ?1 || '-[0-9]*'` — pick
   the lowest free suffix; compute `N`; write `<dir>/v<N>.<ext>.tmp`, `flush` +
   `fsync(file)`, `os.replace` → `v<N>.<ext>`, `fsync(dir)`; upsert `artifacts`,
   insert `versions`, upsert `artifact_sessions`, bump `updated_at`.
4. `COMMIT`. `finally: conn.close()`.

Content file and index row are both inside the lock, so a race can't leave a
file with the loser's bytes against a row with the winner's sha. Two ops racing
the same identifier: one holds `IMMEDIATE`; the other waits `busy_timeout`, then
(finding the identifier now exists) takes the update path. Catch
`OperationalError` and `IntegrityError` in the retry (3.4). This is the **only**
write coordination; in-process subagents share the same process and the same
lock.

### 5.4 Content normalization (hash input)

`content.replace("\r\n","\n").replace("\r","\n").rstrip("\n")`, then sha256.
Used by: update no-op detection, restore no-op detection, scan dedup.

Deliberately-accepted behaviours:
(a) identical content under two different identifiers → two artifacts (only the
scan dedups cross-identifier);
(b) version content is retained forever, unbounded (≤ 1 MB each) — no GC;
(c) an assistant that edits a fenced block across turns changes its content hash,
so a re-scan creates a **second** `origin=scan` artifact rather than versioning
the first (the tool path versions by identifier; the scan path keys new
identifiers off content hash).

### 5.5 Type → extension

`code` → language ext (`python`→`.py`, …; unknown/absent → `.txt`, previewed as
plain text) · `html`→`.html` · `svg`→`.svg` · `markdown`→`.md` ·
`mermaid`→`.mmd` · `react`→`.jsx` (Phase 2). A `create_artifact` on an existing
identifier keeps the **original** type and ext.

### 5.6 Caps

- **`read_artifact` returns the full current content** — no truncation. (An
  earlier draft capped it at 10 KB; the model then reads a truncated body + a
  marker string and writes that back via `update_artifact`, destroying content.)
  For a pathological artifact (> 256 KB), `read_artifact` returns a head plus
  `{truncated: true, note: "open the Creator pane for the full artifact"}` — and
  `update_artifact` **rejects** any payload that still contains that note string.
- Tool-result `content` echo (in the `create_artifact` / `update_artifact`
  *result*, not `read_artifact`): 10 KB, truncated with `… (full content in the
  artifact)`.
- Tool-result `diff` (unified, vs the previous version): 4 KB.
- Any write (tool or HTTP): content > 1 MB → `tool_error` / HTTP 400
  `{error:"too_large"}`.

### 5.7 Agent tools

`create_artifact` — `{identifier, type, title, content, language?}`; required
`identifier, type, title, content`. New identifier → new artifact + `v1`
(`source="create"`). Existing identifier → append a version
(`source="update"`), keep the original `type`/ext, update `title` and
`language` when non-empty. Records `(identifier, session_id)`. Result
`{identifier, version, type, title, action:"created"|"updated", note}` — no
content echoed; `note` explains if the type/ext was kept.

`update_artifact` — `{identifier, content}`. Identifier must exist. Normalized
content identical to the latest version → `{action:"unchanged", version:N}`
(still records the session). Else `v(N+1)` `source="update"`; result adds `diff`
(≤ 4 KB) and `content` (≤ 10 KB). Rejects a payload containing the read
truncation-note string.

`read_artifact` — `{identifier}` → `{identifier, version, type, title,
version_count, updated_at, content}` (full — 5.6). System-prompt guidance to call
this before an update is now safe.

Errors: `tool_error("no artifact '<id>' — call create_artifact first")`,
`tool_error("type must be one of code, html, svg, markdown, mermaid[, react]")`,
`tool_error("store busy, retry")`.

### 5.8 System-prompt section (~950 chars, **no leading `##`**)

> You have `create_artifact` / `update_artifact` / `read_artifact` (Creator).
> Use them for substantial standalone content the user will want to preview,
> revise, and keep — a web page, an SVG, a code file of ~40+ lines, a Markdown
> document, a Mermaid diagram (and, when available, a React component). Put the
> content in the artifact, not in your reply — a one-line pointer is enough.
> - Choose a short stable kebab-case identifier per artifact and reuse it with
>   `update_artifact` on later turns; each update is a version the user can step
>   through and revert.
> - Call `read_artifact` before an update if the user may have edited it.
> - `type=html` may be a full document or a fragment. `type=code` takes a
>   `language`. Use `type=react` for an interactive component (Phase 2+).
> - Small snippets, inline examples, and command output stay in your reply as
>   normal code blocks.

Phase 2 appends the `type=react` paragraph + the import list; the plan asserts
the rendered section stays < 4000 chars.

### 5.9 Server-side transcript scan (`POST /creator/scan {session_id}`)

`_read_assistant_messages(session_id) -> list[str]` (the monkeypatchable seam;
real impl lazy-imports `SessionDB`, flattens structured content blocks, passes
`include_compacted=True`) → extract fenced blocks → apply the thresholds. These
are **inspired by** `lib/artifact-detect.ts`, not a port — Creator owns them in
one named constant block. Real `NON_ARTIFACT_LANGUAGES` skip-list (16 entries):
`{'', console, diff, log, logs, markdown, md, mermaid, output, patch, plain,
plaintext, shell-session, stdout, text, txt}`; Creator diverges on `mermaid` and
`md`/`markdown`.

Row precedence: **svg → html → markdown → code** (a `<svg`-starting body in an
`html` fence is svg).

| Fence lang / signal | Threshold → type |
|---|---|
| lang `svg`, or body starts `<svg` | ≥ 2000 chars → `svg` |
| lang `html`/`htm`/`xhtml`, or lang empty + body starts `<!doctype`/`<html` | ≥ 160 chars → `html` |
| lang empty/none + HTML-tag-dense, no doc wrapper | ≥ 1200 chars → `html` |
| lang `mermaid` | any length → `mermaid` |
| lang `md`/`markdown` | ≥ 600 chars → `markdown` |
| any other lang not in the skip-list | ≥ 48 lines **or** ≥ 3000 chars → `code` |

Per candidate: normalize + hash → **skip** if the hash matches any existing
version of any artifact (still record `(identifier, session_id)` for the matched
artifact if the session isn't linked). Else create `origin="scan"`,
`source="scan"`, identifier = `slug[:55].rstrip('-') + '-' + hash[:8]` computed
**before** the 64-char cap so the deterministic suffix always survives; slug from
`<title>` / `<h1>` / first declaration / lang.

Idempotent. Debounce state is a dict in `cr_store` process memory (~10 s per
session; lost on dashboard restart — harmless, the op is idempotent). Trigger:
the pane calls scan when it opens for a session and on
`host.state.$focusedBusy` true→false. Cannot recover a current-live-turn
artifact (SessionDB lags).

### 5.10 HTTP API — all under `/api/plugins/hermes-workspace/creator/`

```
GET  /artifacts?session_id=          -> {artifacts:[{identifier,type,title,version,updated_at,origin,in_session}]}
GET  /artifacts/{id}                 -> {identifier,type,language,title,version_count,updated_at,
                                         versions:[{n,source,restored_from,created_at,bytes}]}
GET  /artifacts/{id}/v/{n}           -> {identifier,n,type,content}   (JSON; ≤1 MB; 410 if the file is missing)
POST /artifacts/{id}/versions {content}      -> {identifier,version,action:"updated"|"unchanged"}   source='user-edit'
POST /artifacts/{id}/versions {restore_from:n} -> {identifier,version,action:"restored"|"unchanged"}  source='restore'
                                              (410 if v<n> file is missing)
POST /scan {session_id}              -> {found:N, skipped:M}
DELETE /artifacts/{id}               -> {ok:true}   (cascades rows; rm -rf <dir>/)
GET  /config                         -> {project_root, github_token_set:bool}          (Phase 3)
POST /config {project_root?, github_token?}  -> {ok:true}                              (Phase 3)
GET  /asset/{name}                   -> {name, encoding, data, sha256}                  (Phase 2)
POST /artifacts/{id}/export {dest?}          | /export/bundle {html}                   (Phase 3)
GET/POST /artifacts/{id}/storage             (whole-object; key in the body)           (Phase 4)
GET  /readfile?scheme=&path=          -> JSON envelope                                  (Phase 4)
GET  /readdir?scheme=&path=           -> {entries:[...]}                                (Phase 4)
```

Every `{id}` is the raw identifier; the handler resolves it to `<dir>` via the
index and never joins an unsanitized string to a path; `GET /v/{n}` and `DELETE`
assert the resolved path stays under `creator/`. Write endpoints enforce the
1 MB cap with HTTP 400. `/config` writes `creator/config.json` (atomic; not the
install dir); `github_token` is stored but never echoed.

No WebSocket. The pane polls `GET /creator/artifacts?session_id=` (~2 s while
open, `session_id` = `focusedStoredSessionId`) and re-fetches the open artifact
on change. A failed poll keeps last state, retries. A `GET /artifacts/{id}` 404
(deleted elsewhere) → the pane drops to the picker / `EmptyState`.

### 5.11 Renderer — the Creator pane

`crRegister(ctx)` — one guarded call from Knowledge's **synchronous**
`register(ctx)` body, after Knowledge's own contributions. It captures `const
crCtx = ctx` (its own binding — never Knowledge's `CTX` global) and adds:

- A raw `PANES_AREA` pane (the same mechanism Knowledge uses), `data.placement:
  "right"`, `dock:{pane:"workspace", pos:"right"}`, a comfortable default width
  (editor + preview). Wrapped in Creator's own `ErrorBoundary`, plus a
  per-artifact inner boundary.
- `PALETTE_AREA`: "Open Creator", "Creator: rescan this chat".
- `STATUSBAR_AREAS.right`: "◆ Creator".
- Not auto-opened. When open it auto-follows the latest artifact of
  `focusedStoredSessionId`; a null id (brand-new chat) → `EmptyState`, no poll.

Pane content:

- **Header** — a `<select>` picker (this-session artifacts first, then the rest,
  by `updated_at`), a version stepper (`◀ v3/5 ▶` · `latest` · `↺ restore`), a
  `CopyButton`, a `Delete` `Button` behind `ConfirmDialog`.
- **Body** — editor + preview, arranged by a **CSS container query** (side-by-side
  when wide, stacked when narrow). No layout control.
  - **Editor** (Phase 1): SDK `Textarea`, monospace, tab-to-indent, a dirty dot,
    explicit **Save** (button + ⌘S) → `POST /creator/artifacts/{id}/versions`.
    Non-latest version → read-only. **Phase 3 swaps in CodeMirror 6** (§7.1)
    behind the identical dirty/Save/read-only contract.
  - **Preview**:

    | type | preview |
    |---|---|
    | `code` | the editor is the view; no separate render |
    | `markdown` | `<Streamdown>{content}</Streamdown>` |
    | `mermaid` | `<Streamdown>` with a ```` ```mermaid ```` fence |
    | `html` | own `<iframe sandbox="allow-scripts" srcDoc={…}>`, opaque origin, copied theme prelude; a fragment wrapped in a minimal doc + reset. Phase 2 also inlines the renderer-compiled Tailwind `<style>` unless the doc links its own |
    | `svg` | `<img src="data:image/svg+xml;base64,…">` — script-safe |
    | `react` | Phase 2 (§6.3) |

## 6. Phase 2 — React/JSX runtime

**First task is a proof-of-life spike** (§11): vendor esbuild only, prove the
JSON-envelope transport + `WebAssembly.compile` + `esbuild.build` + a trivial
iife bundle rendering in the srcdoc iframe, in the real renderer, before building
the rest.

### 6.1 Assets — `dashboard/creator-libs/` (committed, ~17 MB)

Produced by a **pinned, committed build**: `package.json` + lockfile pinning
exact versions of every lib + esbuild + the node engine; `build.mjs` runs esbuild
to emit each artifact deterministically. Re-vendor = bump a version, re-run,
commit.

- `esbuild.wasm` (~10 MB) + `esbuild.js` — the esbuild-wasm **browser driver
  module** (needed alongside the wasm; it also spawns a blob Web Worker).
- The **full Claude import set**, each a single zero-import ESM file:
  `react`, `react-dom`, `react-dom/client`, `react/jsx-runtime`, `recharts`,
  `lucide-react`, `d3`, `three`, `@react-three/fiber`, `@react-three/drei`,
  `papaparse`, `xlsx`, `mathjs`, `tone`, `@tanstack/react-table`, `lodash` /
  `lodash-es`, `date-fns`, `framer-motion`, `clsx`, `tailwind-merge`,
  `class-variance-authority`, and the shadcn/ui component set + its Radix
  primitives.
- `MANIFEST.json` — `{ "<specifier>": { "file": "...", "subdeps": ["..."] } }`.
  Shared deps (e.g. `react`, pulled by many) are deduped in the esbuild
  in-memory vfs by resolved specifier; cycles are broken by the resolver marking
  in-progress specifiers.
- The **Tailwind compiler** as a zero-import ESM exposing a
  `compile(baseCss, { candidates }) -> css` API, run **in the plugin renderer**
  (not in the iframe).

`GET /creator/asset/{name}` returns the JSON envelope (3.6).

### 6.2 The esbuild driver (plugin renderer)

First `type=react` use: `ctx.rest` GET `/creator/asset/esbuild.js` (decode text,
blob-import) and `/creator/asset/esbuild.wasm` (base64 → `Uint8Array`,
`timeoutMs: 120000`) → `WebAssembly.compile(bytes)` → `esbuild.initialize({
wasmModule, worker: true })`. One instance kept warm for the plugin's lifetime.

**Preview build** — debounced on editor/artifact change:

1. Parse the source for bare import specifiers.
2. `ctx.rest`-fetch each lib envelope (+ subdeps from `MANIFEST.json`), cached by
   name.
3. In-memory vfs; `esbuild.build({ stdin: { contents, loader: 'tsx' }, bundle:
   true, format: 'iife', globalName: '__CreatorArtifact', jsx: 'automatic',
   jsxImportSource: 'react', write: false, plugins: [vfsResolver] })`.
   `loader:'tsx'` strips any TS syntax and handles JSX.
4. Success → one iife string; failure → esbuild diagnostics shown in the pane, no
   iframe.
5. Cache the bundle by artifact-content hash.

**iife + `globalName`** (not `format:'esm'`) so the bootstrap can read
`window.__CreatorArtifact.default` directly — no blob `import()` inside the
frame, no `about:srcdoc` bare-specifier failure.

### 6.3 The preview iframe

`<iframe sandbox="allow-scripts" srcDoc={doc}>`, opaque origin, copied
`themePrelude()`. `srcDoc`:

- `<style>` — the Tailwind CSS **compiled in the renderer**: after esbuild
  produces the bundle, feed the bundle text (and any `@theme` block the artifact
  declares) to the Tailwind compiler's candidate extractor → `compile()` → a CSS
  string, inlined. Cached by class-set hash. The **same CSS** is reused by
  export (§7.2), so there is no in-frame compiler and no hidden-iframe scrape.
- `<div id="root">`.
- `<script>` (classic) — the iife bundle string inlined verbatim.
- `<script>` — bootstrap: `ReactDOM.createRoot(root).render(React.createElement(
  ErrorBoundary, null, React.createElement(window.__CreatorArtifact.default)))`
  in try/catch.
- the **error + console bridge script** (§6.4) carrying this mount's nonce.

No import map, no external fetches — everything inlined.

### 6.4 Feedback surfaces

- **Render-error surfacing** (kept in full): the bootstrap's `ErrorBoundary` +
  `window.onerror` / `onunhandledrejection` → `postMessage({type:'cr-error',
  token, message, stack})`. The pane shows *"Render error: <message> (<first
  stack frame>)"* instead of a blank frame; a successful re-render clears it.
- **Console pane** (trimmed to plain scrollback): the bridge patches
  `console.log/info/warn/error/debug` → `postMessage({type:'cr-console', token,
  level, text})` (`text` = safe-serialized args joined; primitives, shallow
  objects, `[Circular]` guards, length caps). The pane shows a plain `<pre>`
  scrollback below the preview with per-level color and a Clear button;
  `console.error` also lands in the error strip. Ring capped ~300 lines. No
  timestamps / badges / collapse-count.

Both use the §3.8 trust model: per-mount nonce, `type` + `token` match, mandatory
`event.source` check, re-validate every field.

### 6.5 `type: react`

Added to the `type` enum (code-validated, no SQL CHECK), the `create_artifact`
schema, and the prompt section:

> Use `type=react` for an interactive component — charts, dashboards, anything
> stateful. The content is a module whose **default export** is the component.
> You may import from: react, react-dom, recharts, lucide-react, d3, three +
> @react-three/fiber + @react-three/drei, papaparse, xlsx, mathjs, tone,
> @tanstack/react-table, lodash, date-fns, framer-motion, clsx, tailwind-merge,
> class-variance-authority, and shadcn/ui. Tailwind classes are compiled per
> artifact.

## 7. Phase 3 — editor, export & publish

### 7.1 CodeMirror 6 editor (replaces the textarea)

- **Vendor** `codemirror.js` (~1 MB, zero-import ESM: `@codemirror/{state,view,
  commands,language,search,autocomplete}`, `@codemirror/lang-{javascript,html,
  css,python,markdown}`, `@lezer/*`, one light + one dark theme) via the same
  `build.mjs`.
- **Load**: on first editor mount, `ctx.rest` GET `/creator/asset/codemirror.js`
  → decode text → `import(URL.createObjectURL(new Blob([text], {type:
  'text/javascript'})))` — the same blob-module mechanism Hermes uses for
  `plugin.js`; the module has nothing to resolve. Cached for the plugin's
  lifetime. Fetch/import failure → fall back to the Phase 1 `Textarea` + a
  one-line notice; editing never breaks.
- **Wrapper**: ~50 lines — a `useRef` div, one `EditorView` on mount / destroyed
  on unmount, `dispatch` to push external content (version stepping) in, an
  `updateListener` → dirty flag, `EditorState.readOnly` for a non-latest version.
  Hand-wired; no `@uiw/react-codemirror`.
- **Config**: line numbers, active-line highlight, bracket matching, history,
  search (⌘F), close-brackets; language mode from `artifact.language`/`type`;
  the plain bundled light/dark theme (not Hermes-CSS-var-derived — that's polish).
- **Contract unchanged**: dirty dot, Save (button + ⌘S) → `POST
  /creator/artifacts/{id}/versions` (`source='user-edit'`), read-only on old
  versions. Header, picker, stepper, preview, backend paths untouched.

### 7.2 Standalone `.html` export

`POST /creator/artifacts/{id}/export {dest?}`:

- `html` / `svg` / `markdown` / `mermaid` / `code` → server-side wrap in a
  minimal self-contained document (markdown/mermaid pre-rendered to static HTML
  by a small bundled renderer; raw file for `code`).
- `react` → the renderer assembles the file: minified esbuild bundle
  (`minify:true`) + the **renderer-computed Tailwind CSS already produced for the
  preview** (§6.3) + the theme prelude, then `POST
  /creator/artifacts/{id}/export/bundle {html}` persists it. No hidden iframe, no
  `<style>` scrape — the CSS is already in hand.
- Output `creator/exports/<dir>-v<N>.html` (or `dest`). The pane shows the path +
  "Reveal in folder" / "Open in browser" via `ctx.os.revealPath` /
  `ctx.os.openExternal` (branch on the boolean return); otherwise the path is
  copyable.

Fully offline, fully portable, no server.

### 7.3 Publish to Gist

A **pane button only** (`POST /creator/artifacts/{id}/publish`) — no agent tool
(a `publish_artifact` tool is half-broken for `type=react` and the button covers
the value).

- The handler produces the standalone HTML (for `react` the renderer supplies it,
  same as export), then:
  - `gh` on PATH and authed → `gh gist create --public <file> --desc "<title>"`,
    parse the URL;
  - else → `{error:"github_not_configured", how:"install gh and run gh auth
    login, or set a token in Creator settings"}`.
- A `creator.github_token` → GitHub REST `POST /gists` path is a one-line note,
  not fully specified here.
- Result: the gist URL + a `githack`-style raw-render URL for `html` artifacts.
- **Self-hosted publish** — a `creator.publish_url` pointing at a user-run static
  host (e.g. a DigitalOcean droplet) that the handler uploads to is a documented
  later option; not built.

## 8. Phase 4 — the `window.hermes` in-artifact runtime

Injected into every `type=react` **and** `type=html` preview iframe alongside the
error/console bridge, same per-mount nonce. (`type=html` doubles the bridge
surface but html artifacts are a real Claude-parity case.)

### 8.1 `complete(prompt, opts?) -> Promise<string>`

- **Availability probe first** — reuse Knowledge's `MISSING_RPC` check. If
  `llm.oneshot` is absent: `complete` rejects in the iframe with a clear message
  and the pane shows "model calls unavailable on this Hermes build".
- iframe → `postMessage({type:'cr-req', token, id, op:'complete', prompt})`.
- plugin render → `host.request('llm.oneshot', { instructions: <fixed "you are a
  helper inside a user's artifact">, input: prompt, session_id:
  host.state.focusedSessionId || host.state.activeSessionId, temperature: 0.4,
  max_tokens: 1500 })` — the **runtime** id (§3.2), `||` not `??`.
- result → `postMessage({type:'cr-res', token, id, ok, value|error})`.
- **Guards**: 1 call/sec throttle + a per-mount budget (default 50, resettable in
  the pane) as the core; **first-call confirmation** persisted **once per
  artifact** in `creator/<dir>/.cr-meta.json` (Creator-internal, separate from the
  artifact-owned `storage.json`) — not re-prompted on remount / reopen /
  version-step. Model-call activity shows in the console strip (no separate
  counter widget).
- One-shot only. If the runtime id doesn't resolve to a live gateway session the
  call silently uses the weak aux model — a documented limitation (Risk 11).

### 8.2 `storage` — `get(k)` / `set(k,v)` / `remove(k)` / `keys()`

- iframe → `postMessage({type:'cr-req', token, id, op:'storage.set', key,
  value})` etc. **Key travels in the body**, never the path (an arbitrary JSON
  key may contain `/`).
- plugin render → `ctx.rest('POST /creator/artifacts/{id}/storage', {op, key,
  value})` → `cr_store` reads/writes `creator/<dir>/storage.json` (one JSON
  object, atomic write, ~256 KB cap). This is a **separate atomic write**, not
  under the version `BEGIN IMMEDIATE` — the version path never touches
  `storage.json`; `DELETE`'s `rm -rf <dir>/` cleans it.
- Values are JSON; `keys()` lists the object's keys.

### 8.3 `readFile(path) -> string | Uint8Array` and `readdir(path) -> string[]`

Three read-only scopes, 1 MB per read across all of them, with a visible
"Exposed to this artifact:" indicator in the pane listing exactly what's
reachable. Returns via the JSON envelope (3.6).

1. **Artifact files** — a pane drop-zone; files land in `creator/<dir>/files/`.
   `readFile("data.csv")` resolves here first.
2. **The vault** — `readFile("vault:Areas/Argos.md")`, `readdir("vault:Areas")`.
   `cr_api` reuses Knowledge's path-guard + note-read code by **explicit-path
   import** of the relevant `hw_*` module (`spec_from_file_location`, the same
   trick as loading `cr_store`) — **in-process, not an HTTP call to Knowledge's
   routes** — so Creator doesn't couple to Knowledge's route shapes. Requires a
   vault configured in Knowledge; else the scheme rejects.
3. **A project root** — `creator.project_root` (config, or a "Set project
   folder…" button). `readFile("project:src/App.tsx")`, `readdir("project:")`.
   Path-guarded under the root, read-only.

An unconfigured scheme rejects with a clear message; the indicator shows which
scopes are live.

### 8.4 The bridge protocol

Envelope both ways: `{type, token, id, ...}`. `type ∈ cr-req | cr-res | cr-error
| cr-console`. `id` (iframe-generated) correlates request/response; the plugin
render keeps a `Map<id, resolver>`. Every inbound field validated and clamped;
unknown `op` → `cr-res` with an error. The `window` listener accepts a message
only when `type` + `token` match this mount **and** `event.source ===
frameRef.current?.contentWindow` (both mandatory — Creator has one frame and
holds its ref). `postMessage(..., "*")`.

## 9. Testing

### 9.1 Backend (framework-free, matching Knowledge)

`cr_store.py` ends with `def _selfcheck()` (`assert` + `tempfile`, `HERMES_HOME`
→ a temp dir). `dashboard/selftest.py` loads `cr_store` by **explicit path**
(the `MODULES` `__import__` loop can't reach a plugin-root file) and adds a
Creator HTTP round-trip via `TestClient`.

Phase 1 unit targets:

1. `sanitize_identifier` — `..` and backslash rejected, `""`→`artifact`,
   unicode/space collapse, first-char-alnum; `<dir>` `-2` suffix on a
   *different*-identifier collision (query in 5.3).
2. `create` then `create` same identifier → `updated` `v2`, one `artifacts`
   row, two `versions` rows, original type/ext kept, title + language updated.
3. `update` byte-identical → `unchanged`, no new file, **but**
   `artifact_sessions` still gets the row.
4. `update` differing only `\r\n`↔`\n` → `unchanged`.
5. restore: `POST /versions {restore_from:1}` at `v3` → `v4`, content == `v1`,
   `source='restore'`, `restored_from=1`; restore == current latest →
   `unchanged`; `restore_from` a missing file → 410.
6. Write path: content file + index row atomic; monkeypatched crash between
   `os.replace` and `COMMIT` → row absent, orphan file ignored, later `GET` works.
7. Concurrency: two threads `create` same identifier → one `artifacts` row, two
   `versions`, no exception surfaced; two threads `create` *different*
   identifiers that sanitize to the same base `<dir>` → two rows, two dirs.
8. Per-version integrity under concurrent `update`: `v<n>.<ext>` on disk matches
   the sha recorded for row `n` (not the loser's bytes).
9. `versions.source`: one assertion per row of 5.2.1 that the right path emits
   the right value.
10. Scan: HTML doc ≥ 160 chars via monkeypatched `_read_assistant_messages`
    (returning a list with a structured-content case + a compacted case) → one
    `origin='scan'` artifact; same content `create_artifact`'d first → scan adds
    nothing, links the session; re-run → 0. Rows: 20-line JS skipped; 60-line JS
    → `code`; 5000-char/10-line JS → `code`; `diff` skipped; `mermaid` any → 
    `mermaid`; `md` 200 → skipped, `md` ≥600 → `markdown`; `<svg` in an `html`
    fence → `svg` (precedence). Long-slug identifier keeps its 8-hex suffix.
11. `read_artifact` returns **full** content for a 30 KB artifact (no marker);
    the `create_artifact` *result* echo is ~10 KB truncated; `update_artifact`
    with a payload containing the truncation-note string → `tool_error`.
12. Caps: 1.1 MB content → error (tool + HTTP 400); 20 KB diff → ~4 KB.
13. HTTP round-trip (routes under `/creator/`): `POST /creator/scan` on a seeded
    temp `SessionDB` → `GET /creator/artifacts?session_id=` lists it
    `in_session:true` → `GET .../v/1` → `POST .../versions` (`user-edit`) → `GET
    /creator/artifacts/{id}` shows `v2` → restore → `v3`==`v1` → `DELETE` → 404,
    `<dir>/` gone, no orphan rows.
14. `GET/POST /creator/config` round-trip; `github_token` set but never echoed
    (`github_token_set:true`).
15. Path guard: `GET /creator/artifacts/..%2f..%2fpasswd/v/1` → 400.
16. Defensive mount: monkeypatch `cr_api` import to raise → `import plugin_api`
    still succeeds, `hw_*` routes still mount, `selftest.py` still green.

Phase 2:

- Asset envelope: `GET /creator/asset/react.js` → `{name, encoding:"utf8",
  data, sha256}` decoding to the exact source; `esbuild.wasm` →
  `encoding:"base64"` decoding to bytes that `WebAssembly.compile` accepts.
- esbuild driver: both `.js` + `.wasm` envelopes load; `esbuild.build` of a
  fixture importing `recharts` → one iife string, no diagnostics; a
  syntax-error artifact → diagnostics, no bundle; the vfs resolver dedups a
  shared `react`.
- Tailwind **renderer-side**: a fixture using `grid-cols-[1fr_2fr]` + a `@theme`
  token → `compile()` emits matching CSS; the export path inlines that same CSS
  and no compiler ships in the file.

Phase 3:

- CodeMirror blob-load + wrapper: mounting against a fixture yields an
  `EditorView`; a content-prop change replaces the doc; `readOnly` blocks edits;
  a forced asset failure falls back to the `Textarea`.
- Export of each type opens standalone; `react` export is minified and carries
  inlined CSS. Gist: `gh` present → parses a URL from stubbed output; absent →
  the setup message.

Phase 4:

- Bridge: bad `token` dropped; wrong `event.source` dropped; unknown `op` →
  `cr-res` error; oversized payload clamped.
- `complete`: probe gates it; throttle + budget enforced in the plugin render;
  the first-call confirm persists to `storage.json` and does not re-prompt.
- `storage`: key-in-body round-trip through `storage.json`; 256 KB cap.
- `readFile`: artifact file → bytes; `vault:` via explicit-path import (no vault
  → reject); `project:../escape` → reject.

### 9.2 Renderer (manual checklist, no headless Hermes)

Per phase: create from a chat; edit + save → new version; step + restore; each
type renders; the picker; backend-restart resilience. Phase 2: a React artifact
runs; a render error shows instead of a blank frame; the console pane works;
arbitrary Tailwind classes render. Phase 3: CodeMirror loads, highlights,
searches, falls back if the asset is pulled; export opens standalone and styled
with no compiler in the file; the Gist link works. Phase 4: `complete` confirms
once then works; `storage` survives a pane close; `readFile` sees only what the
indicator lists.

## 10. Risks

1. **Model under-uses the tools** — the scan is the hedge; the prompt section is
   tuned in testing.
2. **Preview build latency** — debounce + bundle cache (by content hash) +
   lib-source cache + Tailwind CSS cache (by class-set hash). A fast path that
   re-bundles only on import changes is a later refinement.
3. **`creator-libs/` is ~17 MB in git** — one-time; a pinned committed
   `build.mjs` + lockfile makes it reproducible; re-vendor = bump + re-run.
4. **Threshold drift from Hermes core** — Creator owns the thresholds in one
   named block with a comment pointing at `artifact-detect.ts`.
5. **Shared-import fragility** — `cr_store.py` / `cr_api.py` stdlib + FastAPI
   only, `hermes_*` in function bodies; the defensive mount + `import logging`.
   Test 16 covers it.
6. **`plugin.js` grows to ~3500 lines** — only `cr_*` symbols, zero Knowledge
   coupling, one guarded call. A split is impossible (SDK import ban). Revisit
   near ~350 KB.
7. **`window.hermes.complete` cost** — probe + 1/sec throttle + per-mount budget
   + persisted first-call confirm. A local model loop pegs the GPU but spends no
   money; a paid model is budget-capped.
8. **`readFile` surface** — three read-only scopes, a visible indicator,
   path-guarded, size-capped; `vault:` is an in-process explicit-path import, not
   an HTTP coupling to Knowledge.
9. **Same-turn artifacts not scanned** — SessionDB lags the live turn; caught
   next turn or by a tool call.
10. **Publish is a substitute** — Gist is the frictionless analogue;
    self-hosted publish is the documented path to a real match.
11. **`llm.oneshot` silent downgrade** — when the runtime `session_id` doesn't
    resolve to a live gateway session, the gateway uses the weak aux model with
    no signal; `complete()` can't detect it. Documented; acceptable for v1.
12. **esbuild blob Web Worker + renderer-side Tailwind WASM** — both run in the
    renderer (no CSP) but are unverified until the Phase 2 spike (§11, first
    task). If the worker path fails, esbuild-wasm has a `worker:false` fallback
    (slower, single-threaded).

## 11. Build order

Phase 1 → 2 → 3 → 4, each landing green (`selftest.py` + `node --check`) before
the next.

- **Phase 1** is a complete product on its own.
- **Phase 2** starts with a **proof-of-life spike** — esbuild only, the
  JSON-envelope transport, `WebAssembly.compile`, one iife bundle in the srcdoc
  iframe, in the real renderer — before the vendored set, the vfs resolver, the
  Tailwind compile, and the console/error strip build on top.
- **Phase 3** (CodeMirror + export + publish) and **Phase 4** are additive and
  small relative to Phase 2.

## 12. Install

1. Copy `plugin/hermes-workspace/` → `~/.hermes/plugins/hermes-workspace/`.
2. `hermes plugins enable hermes-workspace` — adds `plugins.enabled`
   (currently absent from `config.yaml`); enables the **agent + dashboard**
   halves of **both** Knowledge and Creator.
3. Restart Hermes Desktop. The renderer half auto-loads on discovery — no
   Settings toggle needed (you can *disable* it in **Settings → Plugins**).
4. `create_artifact` / `update_artifact` / `read_artifact` appear to the agent;
   "Open Creator" is in the command palette; the Creator pane docks beside
   Knowledge.
5. Optional: Creator settings — `project_root`, `github_token`.
