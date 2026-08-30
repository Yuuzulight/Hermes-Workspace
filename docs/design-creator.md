# Creator module — full design (Phases 1–4)

Status: draft for review
Last updated: 2026-08-30
Supersedes: `design-creator-sp1.md`

## 1. Purpose

A Claude-Artifacts equivalent, shipped as a second module (**Creator**) inside the
existing `hermes-workspace` plugin alongside **Knowledge**. The model creates
durable, versioned artifacts through explicit tools; a docked workspace tab
previews, edits, versions, and (Phase 2) runs them; Phase 3 exports/publishes;
Phase 4 gives artifacts a `window.hermes` runtime.

Built as one plan in four phases. Each phase is a working, useful increment:

| Phase | Delivers | Standalone value |
|---|---|---|
| **1** | 3 agent tools + a transcript-scan fallback + a persistent versioned store + a tab with a textarea editor, a preview, and a version stepper. Content types: code, HTML, SVG, Markdown, Mermaid. | Durable versioned artifacts you can edit and preview. |
| **2** | `type: react` + an esbuild-wasm runtime: transpile + bundle the artifact and its imported libraries into a sandboxed iframe, per-artifact Tailwind compiled in-frame, with error surfacing and a console panel. The Phase 1 textarea is replaced with a real CodeMirror 6 editor (both loaded on the same vendored-asset pipeline). | React/JSX artifacts run live with full Tailwind; editing is a proper code editor. |
| **3** | Standalone `.html` export (everything inlined) + "Publish to Gist". | Artifacts leave Hermes as portable files or links. |
| **4** | A `window.hermes` bridge into the artifact iframe: `complete`, `storage`, `readFile`. | Artifacts become interactive AI mini-apps. |

Not built: artifact sharing between users; Monaco (too heavy — ~5 MB, AMD loader,
web workers; CodeMirror 6 is the editor, landing in Phase 2); Hermes' built-in
artifact system (left alone — Creator runs in parallel).

## 2. What Hermes already provides

Hermes Desktop ships a built-in artifact system (`apps/desktop/src/store/
artifacts.ts`, `right-rail/preview-artifact.tsx`): auto-promotion of substantial
fenced blocks, a version stepper, a sandboxed `<iframe sandbox="allow-scripts">`
renderer, rendered/source toggle, download. It is **memory-only** (a renderer
atom rebuilt from the transcript on reload) and **app-internal** (not importable
by a plugin). No persistence, no diff, no restore, no JSX.

Creator's value: a persistent versioned store, explicit `create_artifact` /
`update_artifact` / `read_artifact` tools (like Claude's `<antArtifact
identifier=…>`), a real editor, a cross-session picker, a React runtime, export,
and an in-artifact runtime. The two systems do not interact; when both surface
the same fenced block, that is a documented, accepted duplicate.

## 3. Source-verified constraints (Hermes 0.20.6)

### 3.1 Plugin loading

- `hermes-workspace` is a **user directory plugin**. Its agent + dashboard halves
  are gated by `plugins.enabled` in `~/.hermes/config.yaml`.
- **The desktop renderer half is gated separately** — a per-plugin user decision
  in Electron localStorage (`hermes.desktop.pluginDecisions.v2`), toggled in
  **Hermes Desktop → Settings → Plugins**. There is *no* sync from
  `config.yaml`. So enabling the plugin requires **both** `hermes plugins enable
  hermes-workspace` **and** the Settings→Plugins toggle. This also applies to the
  already-shipped Knowledge module, which currently has neither
  (`config.yaml` has no `plugins:` key).
- A directory plugin needs `plugin.yaml` **and** a root `__init__.py` exporting
  `def register(ctx)`. `hermes-workspace` has no `__init__.py` today, so its
  agent-side load currently fails harmlessly (`Failed to load plugin
  hermes-workspace: No __init__.py`). Adding `__init__.py` makes it load; desktop
  discovery (`runtime-loader.ts` → `desktop/plugin.js`) and dashboard discovery
  (`dashboard/manifest.json`) are unaffected.
- `plugin.yaml` **declares `kind: standalone`** — this disables
  `_detect_kind_from_source`, which otherwise scans the first 8192 chars of
  `__init__.py` and would reroute the plugin to the memory/provider system if it
  saw `register_memory_provider` / `MemoryProvider` / `register_provider` +
  `ProviderProfile`.
- `provides_tools` in `plugin.yaml` is **advisory/display only** for a user
  plugin. Tools register via `ctx.register_tool(...)` in `register(ctx)`.
- `provides_hooks` has **zero loader consumers** — omit it. Creator registers no
  hooks. (`register_system_prompt_section` is a `PluginContext` method, not a
  hook.)

### 3.2 Agent-tool handlers

- Run **in-process in the agent, no sandbox.** `def handler(args: dict, **kwargs)
  -> str`; `kwargs` carries `task_id`, `session_id`, `user_task` (not
  `enabled_tools` — that is `execute_code`-only). Arbitrary Python allowed.
- Tool JSON-schema passed **in Python** to `ctx.register_tool(name, toolset,
  schema, handler, ...)`.
- Return a **string** (`tools.registry.tool_result()` / `tool_error()`).
  Handlers must not raise.
- Tools register under a toolset name (`"creator"`). Visible under the default
  toolset config; a profile with an explicit `agent.toolsets:` allow-list must
  add `"creator"` (documented, not handled).
- `kwargs["session_id"]` **equals** `agent.session_id`, which the agent persists
  as `sessions.id` (the `YYYYMMDD_HHMMSS_xxxxxx` session key), which the gateway
  ships to the renderer as `stored_session_id`, which the renderer exposes as
  `host.state.focusedStoredSessionId`. **The two sides already name the same
  value — no normalization.** Do **not** read `HERMES_SESSION_ID` (contextvar
  only in the gateway; clobbered by in-process subagents) or the RPC runtime id.

### 3.3 System-prompt section

`ctx.register_system_prompt_section(id, content, *, position="after_memory",
max_chars=4000)` — `position` and `max_chars` are keyword-only. `id` matches
`^[a-z0-9][a-z0-9._-]{0,127}$`. Rendered **once at session start**, frozen into
the persisted prompt (cache-safe; not re-read for an existing session).
`position` must be `"after_memory"`. Duplicate `id` raises. All plugin sections
≤ 8000 chars / 32 sections.

### 3.4 Processes and the shared store

- Agent process and dashboard web server are separate sibling processes. No
  localhost HTTP path between them. They coordinate through `~/.hermes/`.
- Data dir: `plugin_storage.plugin_data_dir("hermes-workspace")` →
  `<HERMES_HOME>/plugin-data/hermes-workspace/`, profile-aware, resolved through
  `get_hermes_home()` **on every call** (never cache a Path). **Not** the install
  dir (`<HERMES_HOME>/plugins/hermes-workspace/data/`, which `hermes plugins
  update` clobbers — the current `hw_store.py` uses that; Creator must not).
- `plugin_storage.plugin_db(name, filename)` → a WAL `sqlite3` connection
  (`check_same_thread=False`, `foreign_keys=ON`, `journal_mode=WAL`). It sets
  **no `busy_timeout`** and uses the default `isolation_level`. Creator's store
  must, on every connection: `PRAGMA busy_timeout=5000`, set `isolation_level =
  None` (so its explicit `BEGIN IMMEDIATE` works), and close in a `finally`.

### 3.5 Dashboard module loading

- `_mount_plugin_api_routes` execs `dashboard/plugin_api.py` as a **bare
  top-level module** (no `submodule_search_locations`), wrapping the whole exec
  in one `try/except`. **Any** import-time exception in a file it pulls in
  unmounts **all** of `hermes-workspace`'s routes (all of Knowledge). Creator's
  addition must be defensively wrapped — and the `except` body must not reference
  an unimported name (the current `plugin_api.py` imports only
  `datetime/difflib/os/sys/uuid`; add `import logging`).
- The existing `sys.path.insert(0, os.path.dirname(__file__))` puts only
  `dashboard/` on `sys.path`. **Do not add the plugin root to `sys.path`** — it
  will hold `tools.py`, which shadows Hermes' own `tools/` package
  (`from tools.registry import registry`, used throughout the dashboard). The
  dashboard loads `cr_store.py` by **explicit path** (`spec_from_file_location`).
- `cr_store.py` and `cr_api.py`: **module-level imports = stdlib + FastAPI
  only.** `from hermes_constants import get_hermes_home` allowed only
  try/except-guarded. `from hermes_state import SessionDB` goes **inside** the
  scan handler, try/except-guarded (the Knowledge `selftest.py` does a bare
  `import plugin_api` in plain FastAPI).

### 3.6 Renderer

- One `desktop/plugin.js`, imports only `@hermes/plugin-sdk`, `react`,
  `react/jsx-runtime`. No relative imports (any other bare/relative specifier is
  an up-front load error), no build step. Read via `hermes:readPluginSource` IPC,
  16 MB cap. Current file ~42 KB / 1228 lines.
- **There is no CSP on the desktop renderer** — no `<meta>`, no Electron
  `onHeadersReceived`, no server header. `WebAssembly.compile` / `instantiate`
  are unrestricted. Sandboxed `srcdoc` iframes and their `<script type="module">`
  execute freely.
- **A desktop plugin has no static-asset URL.** `ctx.rest` is an IPC JSON/bytes
  call (via the Electron main-process fetch bridge — not CORS-gated), not a file
  server. The `/dashboard-plugins/<id>/<file>` route serves only the `dashboard/`
  subdir, **excludes `.wasm`**, and is CORS-blocked from the `file://` renderer
  and opaque-origin iframes (`Origin: null`). **An iframe cannot `import` from
  any Hermes URL.** Code enters an iframe only by being inlined into its
  `srcdoc`, or via `blob:`/`data:` URLs the iframe's own script builds from
  inlined bytes.
- `host.openWorkspace(id, {render, dock, title, minWidth, onClose})` — a docked,
  single-instance React tab. Feature-detect `typeof host.openWorkspace ===
  'function'` with a `PANES_AREA` fallback. `dock` default `pos` is `center`; the
  Knowledge pane already docks `{pane:'workspace', pos:'right'}` — Creator docks
  the same (`openWorkspace` is single-instance per id; the two tabs stack in the
  right dock).
- Every pane `render` is host-wrapped in `ContribBoundary` (an `ErrorBoundary`) —
  a plugin render throw shows a Retry fallback in that pane only. Creator ships
  its **own inner `ErrorBoundary` per artifact tab** so one bad artifact doesn't
  blank the tab strip.
- SDK exports usable directly: `Streamdown` (markdown + Shiki + Mermaid),
  `Textarea`, `Button`, `CopyButton`, `ConfirmDialog`, `Badge`,
  `SegmentedControl`, `EmptyState`, `StatusDot`, `useValue`, `atom`,
  `PANES_AREA`, `STATUSBAR_AREAS`, `PALETTE_AREA`, `host.*`, `ctx.*`.
- `ctx.storage` is renderer-only `localStorage` (best-effort, backend can't see
  it) — use only for UI state (last-open artifact, panel widths).
- `host.request(method, params)` works in an `openWorkspace` render with full SDK
  authority (same as `plugin.js`). `llm.oneshot` is one-shot (`{text}`); no
  streaming non-session completion exists.

### 3.7 Reading sessions

- `host.request('session.history', {session_id})` is **live-only**.
- Cross-session: `host.request('session.list', ...)` (DB-backed) or REST
  `/api/sessions/{id}/messages`; server-side reads `SessionDB` directly
  (`list_sessions_rich`, `get_messages`), as `plugins/hermes-achievements` does.
- A brand-new chat's `sessions` row is written lazily on the first prompt — until
  then there is nothing to attach an artifact to.

### 3.8 The `::preview` bridge pattern (Phase 4 reference)

`apps/desktop/src/components/assistant-ui/inline-preview-directive.tsx`: a
per-mount random nonce is baked into an injected `<script>` that sets
`window.hermes` and does `parent.postMessage({type, token, ...}, "*")`. The
parent adds a global `window` `message` listener and accepts a message only when
`msg.type` and `msg.token` match this mount, re-validating/clamping every field.
There is **deliberately no `event.source` or origin check** (the
`allow-scripts`-only sandbox has an opaque origin); the nonce is the whole trust
boundary. No `MessageChannel` anywhere. Throttled 1/sec, payloads capped.

### 3.9 Product rule

No trace of AI authorship anywhere in the repo, code, comments, or commit
messages.

## 4. File layout

Added to `plugin/hermes-workspace/`:

```
plugin.yaml            + kind: standalone
                       + provides_tools: [create_artifact, update_artifact, read_artifact,
                                          publish_artifact]   (display only; publish_artifact is Phase 3)
                       (no provides_hooks)
__init__.py            NEW, plugin root. def register(ctx): from . import tools; tools.register(ctx)
                       — ctx.register_tool(...) x3 (x4 in Phase 3) + ctx.register_system_prompt_section(...).
tools.py               NEW, plugin root. Thin handlers — arg parsing, then delegate to cr_store.
                       from . import cr_store. Also the schema dicts.
cr_store.py            NEW, plugin root. stdlib only + try/except `from hermes_constants import
                       get_hermes_home`. NO relative imports (loads both as a package submodule and by
                       explicit path). Owns ALL behaviour: identifier sanitization, create-vs-update
                       dispatch, type validation, version writes, restore, session recording, dedup,
                       the scan threshold logic, storage (Phase 4). One monkeypatchable
                       _read_assistant_messages(session_id) -> list[str] for the scan.
desktop/plugin.js      + a "// ===== CREATOR =====" section. Adds ONLY new top-level symbols
                       (cr_* prefix); references ZERO Knowledge symbols. Knowledge's register(ctx)
                       body gains exactly one guarded call (crRegister(ctx) in try/catch), placed
                       AFTER Knowledge's own registration. Budget ~1200 -> ~3000 lines across phases.
desktop/plugin.js  --  Phase 2+ adds the esbuild-wasm driver, the iframe host, the console panel,
                       and the blob-module loader that pulls in CodeMirror 6; Phase 4 adds the
                       postMessage bridge.
dashboard/plugin_api.py  + import logging  AND  defensively:
                           try:
                               import cr_api
                               router.include_router(cr_api.router)
                           except Exception as e:
                               logging.getLogger(__name__).warning("creator API not mounted: %s", e)
dashboard/cr_api.py    NEW. Module-level imports = stdlib + FastAPI only. Loads cr_store by explicit
                       path. router = APIRouter(). SessionDB import inside the scan handler.
                       Serves GET /creator/asset/{name} (Phase 2 — raw bytes of a vendored file).
dashboard/creator-libs/  NEW (Phase 2). Committed. ~16 MB: single-file ESM builds of the curated
                       import set, the Tailwind browser build, esbuild.wasm, and codemirror.js
                       (a pre-bundled CodeMirror 6 + language modes, ~1 MB, zero external imports).
                       Served byte-for-byte by cr_api's asset route; the renderer fetches via ctx.rest.
dashboard/selftest.py  loads cr_store by explicit path + appends a Creator HTTP round-trip. Additive.
```

## 5. Phase 1 — core artifact loop

### 5.1 Store on disk

```
<HERMES_HOME>/plugin-data/hermes-workspace/creator/
├── creator-index.db      # plugin_db("hermes-workspace", "creator-index.db"), authoritative
└── <dir>/
    ├── v1.<ext>
    └── v2.<ext>
```

The index is truth. A version file with no index row is an ignored orphan; an
index row whose file is missing reads as "version unavailable".

`<dir>` = the sanitized identifier: lowercase, keep `[a-z0-9._-]`, collapse `-`
runs, strip leading/trailing `-.`, reject `..`, cap 64 chars, empty →
`artifact`; a collision with a *different* identifier gets `-2`, `-3`, … — and
that suffix is chosen **inside the write transaction** (see 5.3).

### 5.2 Schema

```sql
CREATE TABLE artifacts (
  identifier    TEXT PRIMARY KEY,
  dir           TEXT NOT NULL UNIQUE,
  type          TEXT NOT NULL,          -- code | html | svg | markdown | mermaid | react  (react is Phase 2)
  language      TEXT,
  title         TEXT NOT NULL,
  origin        TEXT NOT NULL,          -- 'tool' | 'scan'
  created_at    REAL NOT NULL,
  updated_at    REAL NOT NULL
);
CREATE TABLE versions (
  identifier    TEXT NOT NULL REFERENCES artifacts(identifier) ON DELETE CASCADE,
  n             INTEGER NOT NULL,
  ext           TEXT NOT NULL,
  sha256        TEXT NOT NULL,          -- of normalized content (5.4)
  bytes         INTEGER NOT NULL,
  source        TEXT NOT NULL,          -- create | update | user-edit | restore | scan
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
CREATE INDEX ix_versions_sha ON versions(sha256);
CREATE INDEX ix_artifacts_updated ON artifacts(updated_at);
```

`ON DELETE CASCADE` on both child FKs (`plugin_db()` sets `foreign_keys=ON`, so a
plain artifact delete would otherwise raise).

### 5.3 Write path — all in `cr_store.py`, one writer module

Every mutating op:

1. Resolve the data dir + open `plugin_db()` **fresh**; `PRAGMA
   busy_timeout=5000`; `isolation_level=None`.
2. `BEGIN IMMEDIATE`.
3. Inside the transaction: resolve/allocate `<dir>` (read the index, pick a free
   suffix); compute `N`; write `<dir>/v<N>.<ext>.tmp` in that dir, `flush` +
   `fsync`, `os.replace` → `v<N>.<ext>`; upsert `artifacts`, insert `versions`,
   upsert `artifact_sessions`, bump `artifacts.updated_at`.
4. `COMMIT`. `finally: conn.close()`.

Content file and index row are now both inside the lock, so concurrent updates
can't leave a file with the loser's bytes against a row with the winner's sha.

Concurrency: two ops racing the same identifier — one holds the `IMMEDIATE`
lock; the other waits `busy_timeout`, then proceeds and (finding the identifier
now exists) takes the update path. Catch `sqlite3.OperationalError` (lock
timeout) as well as `IntegrityError` in the retry.

### 5.4 Content normalization (hash input)

`content.replace("\r\n","\n").replace("\r","\n").rstrip("\n")`, then sha256.
Line endings + trailing newline only. Used by: update no-op detection, restore
no-op detection, scan dedup.

Two deliberately-accepted behaviours: (a) identical content under two different
identifiers → two artifacts (only the scan dedups on cross-identifier hash; the
tool no-op check compares only to the same identifier's latest version); (b)
version content is retained forever, unbounded (up to 1 MB each) — no GC in this
plan.

### 5.5 Type → extension

`code` → language ext (`python`→`.py`, …; unknown or absent language → `.txt`,
and the preview treats it as plain text) · `html`→`.html` · `svg`→`.svg` ·
`markdown`→`.md` · `mermaid`→`.mmd` · `react`→`.jsx` (Phase 2).

### 5.6 Caps

- Tool-result `content`: 10 KB, truncated (`… (full content in the artifact)`).
- Tool-result `diff` (unified, vs the previous version): 4 KB, truncated.
- Any write (tool or HTTP): content > 1 MB → `tool_error("content exceeds 1 MB
  limit")` / HTTP 400 `{error:"too_large"}`.

### 5.7 Agent tools

`create_artifact` — `{identifier, type, title, content, language?}`, required
`identifier, type, title, content`. If the identifier exists → appends a version
(keeps the **original** `type`; updates `title` if non-empty); else new artifact
+ `v1` (`source="create"`). Records `(identifier, session_id)`. Result
`{identifier, version, type, title, action:"created"|"updated", note}` — no
content echoed.

`update_artifact` — `{identifier, content}`. Identifier must exist. Normalized
content identical to the latest version → `{action:"unchanged", version:N}`
(and still records `(identifier, session_id)`). Else appends `v(N+1)`,
`source="update"`; result adds `diff` (≤4 KB) and `content` (≤10 KB).

`read_artifact` — `{identifier}`. Result `{identifier, version, type, title,
version_count, updated_at, content:"<current ≤10 KB>"}`.

If `kwargs["session_id"]` is empty (a subagent or draft turn), the artifact is
still created but not session-linked.

Errors: `tool_error("no artifact '<id>' — call create_artifact first")`,
`tool_error("type must be one of code, html, svg, markdown, mermaid[, react]")`.

### 5.8 System-prompt section (static, ~950 chars)

> **## Creator artifacts**
> You have `create_artifact` / `update_artifact` / `read_artifact`. Use them for
> substantial standalone content the user will want to preview, revise, and
> keep — a web page, an SVG, a code file of ~40+ lines, a Markdown document, a
> Mermaid diagram (and, when available, a React component). Put the content in
> the artifact, not in your reply — a one-line pointer is enough.
> - Choose a short stable kebab-case identifier per artifact and reuse it with
>   `update_artifact` on later turns; each update is a version the user can step
>   through and revert.
> - Call `read_artifact` before an update if the user may have edited it.
> - `type=html` may be a full document or a fragment. `type=code` takes a
>   `language`. Use `type=react` for an interactive component (Phase 2+).
> - Small snippets, inline examples, and command output stay in your reply as
>   normal code blocks.

### 5.9 Server-side transcript scan (`cr_api.py`, `POST /artifacts/scan {session_id}`)

`_read_assistant_messages(session_id) -> list[str]` (the one monkeypatchable
seam; real impl lazy-imports `SessionDB`) → extract fenced blocks → apply the
thresholds below. These are **inspired by** Hermes' `lib/artifact-detect.ts`,
not a port — Creator owns them; the real `NON_ARTIFACT_LANGUAGES` skip-list is
`{'', console, diff, log, logs, markdown, md, mermaid, output, patch, plain,
plaintext, shell-session, stdout, text, txt}` and Creator **deliberately
diverges** on `markdown`/`md` and `mermaid` (core never makes those artifacts):

| Fence lang / signal | Threshold → type |
|---|---|
| lang `html`/`htm`/`xhtml`, or lang empty + body starts `<!doctype`/`<html` | ≥ 160 chars → `html` |
| lang empty/none + body is HTML-tag-dense, no doc wrapper | ≥ 1200 chars → `html` |
| lang `svg`, or body starts `<svg` | ≥ 2000 chars → `svg` |
| lang `mermaid` | any length → `mermaid` |
| lang `md`/`markdown` | ≥ 600 chars → `markdown` |
| any other lang not in the skip-list | ≥ 48 lines **or** ≥ 3000 chars → `code` (`language=lang`) |

Per candidate: normalize + hash → **skip** if the hash matches any existing
version of any artifact (still recording `(identifier, session_id)` for the
matched artifact if that session isn't linked yet). Else create with
`origin="scan"`, `source="scan"`, identifier = a slug from `<title>`/`<h1>`/first
declaration/lang, plus `-` + the first 8 hex of the content hash (deterministic).

Idempotent; debounced server-side (~10 s per session; no client debounce).
Cannot recover a current-live-turn artifact (SessionDB lags). Trigger: the
renderer calls scan when the tab opens for a session and on
`host.state.busy` true→false for the focused session.

### 5.10 HTTP API (`cr_api.py`, under `/api/plugins/hermes-workspace/`)

```
GET  /artifacts?session_id=          -> {artifacts:[{identifier,type,title,version,updated_at,origin,in_session}]}
                                        (all artifacts; in_session flags the ones linked to session_id)
GET  /artifacts/{id}                 -> {identifier,type,language,title,version_count,updated_at,
                                         versions:[{n,source,restored_from,created_at,bytes}]}
GET  /artifacts/{id}/v/{n}           -> {identifier,n,type,content}          (JSON; ≤1 MB)
POST /artifacts/{id}/versions {content}  |  {restore_from:n}
                                     -> {identifier,version,action:"updated"|"unchanged"|"restored"}
                                        (exactly one of `content` / `restore_from`. restore = server reads v<n>,
                                         writes it as a new version source='restore', restored_from=n; a restore
                                         whose content equals the current latest -> action:"unchanged", no new
                                         version. No dedicated restore endpoint.)
POST /artifacts/scan {session_id}    -> {found:N, skipped:M}
DELETE /artifacts/{id}               -> {ok:true}    (cascades versions + artifact_sessions; rm -rf <dir>/)
GET  /creator/asset/{name}           -> raw bytes of dashboard/creator-libs/<name>   (Phase 2)
```

Every `{id}` is the raw identifier; the handler resolves it to `<dir>` via the
index and never joins an unsanitized string to a path. `GET /v/{n}` and `DELETE`
assert the resolved path stays under `creator/`. Write endpoints enforce the
1 MB cap with HTTP 400.

No WebSocket. The renderer polls `GET /artifacts?session_id=` (~2 s while the tab
is open) and re-fetches the open artifact on change. A failed poll (backend
restart) keeps last state, retries, toasts.

### 5.11 Renderer — the Creator tab

`crRegister(ctx)` — called once from Knowledge's `register(ctx)` body inside a
`try/catch`, positioned after Knowledge's own registration. Adds:

- The Creator tab: `host.openWorkspace('creator', {render, title, dock:{pane:
  'workspace', pos:'right'}})` (stacks beside the Knowledge pane) if available,
  else a `PANES_AREA` pane. Wrapped in Creator's own inner
  `ErrorBoundary`; a per-artifact inner boundary too.
- `PALETTE_AREA`: "Open Creator", "Creator: rescan this chat".
- `STATUSBAR_AREAS.right`: "◆ Creator" opens the tab.
- Not auto-opened. When the tab is open it auto-follows the latest artifact of
  the focused session (or an `EmptyState` if none). No toast.

Tab content:

- **Header** — a flat artifact picker (a `SegmentedControl` or `<select>`:
  this-session artifacts first, then the rest, by `updated_at`), a version
  stepper (`◀ v3/5 ▶` · `latest` · `↺ restore this version`), a `CopyButton`
  (current content), a `Delete` `Button` (behind `ConfirmDialog`).
- **Body** — editor + preview, arranged by a **CSS container query**: side-by-side
  when the pane is wide, stacked when narrow. No layout control.
  - **Editor** (Phase 1): the SDK `Textarea`, monospace, tab-to-indent, a dirty
    dot, an explicit **Save** (button + ⌘S) → `POST /artifacts/{id}/versions`.
    Stepping to an older version shows it read-only. **Phase 2 replaces this with
    CodeMirror 6** (§6.6) behind the same dirty-dot / Save / read-only contract.
  - **Preview**:

    | type | preview |
    |---|---|
    | `code` | editor only (the textarea is the view); no separate render |
    | `markdown` | `<Streamdown>{content}</Streamdown>` |
    | `mermaid` | `<Streamdown>` with a ```` ```mermaid ```` fence |
    | `html` | own `<iframe sandbox="allow-scripts" srcDoc={…}>`, opaque origin, theme prelude; a fragment wrapped in a minimal doc + reset. Phase 2 also inlines `tailwind.browser.js` here unless the document already links its own Tailwind |
    | `svg` | `<img src="data:image/svg+xml;base64,…">` — script-safe |
    | `react` | Phase 2 |

## 6. Phase 2 — React/JSX runtime

### 6.1 Assets

`dashboard/creator-libs/` (committed, ~16 MB):

- `esbuild.wasm` (~10 MB).
- `codemirror.js` (~1 MB) — a pre-bundled CodeMirror 6: `@codemirror/{state,
  view,commands,language,search,autocomplete}`, `@codemirror/lang-{javascript,
  html,css,python,markdown}`, one theme, `@lezer/*`. Bundled offline with
  esbuild into one ESM file with **zero external imports** so
  `import(blobURL)` resolves with nothing to fetch.
- Single-file ESM builds of the curated set: `react`, `react-dom`, `recharts`,
  `lucide-react`, `d3`, `three` + `@react-three/fiber` + `@react-three/drei`,
  `papaparse`, `xlsx`, `mathjs`, `tone`, `@tanstack/react-table`, `lodash-es`,
  `date-fns`, plus the shadcn/ui + Radix primitive set. Each is a single ESM
  file produced from a pre-built source (e.g. esm.sh output) and committed. A
  `MANIFEST.json` maps import specifier → filename + sub-dependencies.
- `tailwind.browser.js` (~0.4 MB) — the Tailwind v4 in-browser compiler
  (`@tailwindcss/browser`), a single self-contained script. It scans the live
  DOM and emits a `<style>` — no config file, no build step, arbitrary utility
  classes, and a per-artifact `@theme` block for customization.

`cr_api.py`'s `GET /creator/asset/{name}` returns the raw bytes.

### 6.2 The esbuild driver (in the plugin renderer)

On first Creator use: `ctx.rest` fetch `esbuild.wasm` bytes →
`WebAssembly.compile(bytes)` → `esbuild.initialize(...)` → one instance kept warm
for the plugin's lifetime. (No `instantiateStreaming` — there is no
`application/wasm` route.)

**Preview build** — on a debounced editor/artifact change for `type=react`:

1. Parse the artifact source for its bare import specifiers.
2. For each, `ctx.rest`-fetch the lib file (+ its sub-deps from `MANIFEST.json`),
   cached in memory by name.
3. Build an in-memory virtual filesystem; run
   `esbuild.build({stdin:{contents: artifactSource, loader:'jsx'}, bundle:true,
   format:'esm', jsx:'automatic', plugins:[vfsResolverPlugin], write:false})`.
4. On success → one self-contained ESM string; on failure → the esbuild
   diagnostics, shown in the tab (no iframe rendered).
5. Cache the bundle by artifact-content hash.

**TS syntax** (`.tsx` artifacts): esbuild strips it — `loader:'tsx'` when TS
tokens are detected.

### 6.3 The preview iframe

`<iframe sandbox="allow-scripts" srcDoc={doc}>`, opaque origin, theme prelude
(copied from `inline-preview-directive.tsx`'s `themePrelude()`). The `srcDoc`:

- `<script>` — `tailwind.browser.js` inlined verbatim (the Tailwind v4 compiler),
  optionally preceded by `<style type="text/tailwindcss">@theme { … }</style>`
  when the artifact declares custom tokens. It compiles the DOM's classes to a
  `<style>` on load and on mutation — real per-artifact JIT, any class the model
  writes.
- `<div id="root">`.
- `<script type="module">` — the bundled artifact ESM string inlined verbatim,
  then a bootstrap: `import App from ...; createRoot(root).render(<App/>)`
  wrapped in a try/catch and a React error boundary.
- The **error + console bridge script** (6.4), carrying this mount's nonce.

No import map, no external fetches — everything (libs, artifact, Tailwind
compiler) is inlined into the string.

### 6.4 Feedback surfaces

**Error surfacing** — the bootstrap's error boundary + a `window.onerror` /
`window.onunhandledrejection` handler `postMessage` the error `{type:'cr-error',
token, message, stack}` to the parent. The tab shows *"Render error: <message>
(<first stack frame>)"* instead of a blank frame; re-render clears it.

**Console panel** — the bridge script patches `console.log/info/warn/error/debug`
to also `postMessage({type:'cr-console', token, level, args:<serialized>})`
(args serialized safely — primitives, shallow objects, `[Circular]` guards,
length caps). The tab has a collapsible console pane below the preview: level
badges, timestamps, a clear button, auto-scroll, a count badge on the toggle
when collapsed. Buffer capped (last ~500 entries).

Both use the `::preview` trust model: per-mount nonce, parent matches `type` +
`token`, re-validates every field, `postMessage(..., "*")`.

### 6.5 `type: react`

Added to the `type` enum, the `create_artifact` schema, and the prompt section
(*"Use `type=react` for an interactive component — charts, dashboards, anything
stateful. The content is a module whose **default export** is the component.
Import from the standard set: react, recharts, lucide-react, d3, three, lodash,
date-fns, papaparse, xlsx, mathjs, tone, @tanstack/react-table, and shadcn/ui.
Tailwind classes work."*). The full list also goes in the prompt so the model
knows what it can import.

### 6.6 CodeMirror editor (replaces the Phase 1 textarea)

- **Load**: on first editor mount, `ctx.rest`-fetch `codemirror.js` text →
  `import(URL.createObjectURL(new Blob([text], {type:'text/javascript'})))` —
  the same blob-module mechanism Hermes uses for `plugin.js`, and the module has
  no imports to resolve. The resolved module is cached for the plugin's
  lifetime. If the fetch/import fails, fall back to the Phase 1 `Textarea` and
  show a one-line notice — editing never breaks.
- **Component**: a thin React wrapper (~50 lines) — a `useRef` div, one
  `EditorView` created on mount and destroyed on unmount, `dispatch` to push
  external content changes (version stepping) in, an `updateListener` to raise
  the dirty flag. No `@uiw/react-codemirror` dependency; hand-wired.
- **Config**: line numbers, current-line highlight, bracket matching, history,
  search panel (⌘F), close-brackets, the language mode picked from
  `artifact.language` / `type` (`javascript`/`jsx`/`tsx` → the JS mode with the
  right flags; `html`, `css`, `python`, `markdown`; unknown → no mode). A theme
  derived from the Hermes CSS variables so it matches light/dark. `EditorState.
  readOnly` when viewing a non-latest version.
- **Contract unchanged**: same dirty dot, same explicit Save (button + ⌘S) →
  `POST /artifacts/{id}/versions`, same read-only-on-old-version behaviour. The
  header, picker, stepper, preview, and every backend path are untouched.

## 7. Phase 3 — export & publish

### 7.1 Standalone `.html` export

`cr_api.py` `POST /artifacts/{id}/export {dest?}`:

- `type` in `{html, svg, markdown, mermaid, code}` → wrap the content in a
  minimal self-contained document (Markdown/Mermaid pre-rendered to static HTML
  server-side via a small bundled renderer, or shipped as the raw file for
  `code`).
- `type=react` → the renderer produces the bundle (esbuild `build` with
  `minify:true`), then freezes the Tailwind output: it renders the artifact in a
  hidden iframe, lets `tailwind.browser.js` compile, reads the generated
  `<style>` back out, and inlines that static CSS (so the export carries frozen
  CSS, not the ~0.4 MB compiler). It POSTs the finished HTML to a
  `POST /artifacts/{id}/export/bundle {html}` companion endpoint that writes the
  file. (The bundle must be built in the renderer where esbuild-wasm lives; the
  dashboard just persists it.)
- Output: `<HERMES_HOME>/plugin-data/hermes-workspace/creator/exports/<dir>-v<N>.html`
  (or `dest` if the user picks a folder). The tab shows the path + a "Reveal in
  folder" / "Open in browser" pair when the SDK exposes a shell affordance for
  it (feature-detected — e.g. `ctx.os?.revealPath` / `host.openExternal`);
  otherwise the path is shown copyable.

Fully offline, fully portable, no server.

### 7.2 Publish to Gist

A 4th agent tool **and** a tab button (both reach `cr_api.py` `POST
/artifacts/{id}/publish`):

- `publish_artifact(identifier)` — agent tool; the handler (in `cr_store.py`,
  called by both `tools.py` and `cr_api.py`) produces the standalone HTML (for
  `react` it can't — it returns a note telling the user to publish from the tab),
  then:
  - if `gh` is on PATH and authed → `gh gist create --public <file> --desc
    "<title>"`, parse the URL;
  - else if `creator.github_token` is configured → GitHub REST `POST /gists`;
  - else → `{error:"github_not_configured", how:"install gh and run gh auth
    login, or set creator.github_token in plugin settings"}`.
- Result / tab: the gist URL, plus a `githack`-style raw-render URL for `html`
  artifacts so the link previews as a page.
- **Droplet-hosted publish** — a documented future option: a `creator.publish_url`
  pointing at a user-run static host (the user's DigitalOcean droplet); the
  handler `scp`/`rsync`s or POSTs the file and returns `https://<host>/<slug>`.
  Not built in this plan.

## 8. Phase 4 — the `window.hermes` in-artifact runtime

Injected into every `type=react` (and `type=html`) preview iframe alongside the
error/console bridge, carrying the same per-mount nonce. `window.hermes`:

### 8.1 `complete(prompt: string, opts?) -> Promise<string>`

- iframe → `postMessage({type:'cr-req', token, id, op:'complete', prompt})`.
- plugin render → `host.request('llm.oneshot', {instructions:<a fixed
  "you are a helper inside a user's artifact" system prompt>, input: prompt,
  session_id: host.state.focusedSessionId.get() ?? host.state.activeSessionId.get(),
  temperature: 0.4, max_tokens: 1500})` → inherits the user's active model.
- result → `postMessage({type:'cr-res', token, id, ok, value|error})`.
- **Guards**: 1 call/sec per iframe (throttle in the plugin render, not the
  iframe — the iframe can't be trusted); a per-artifact-mount budget (default 50
  calls, a "reset budget" affordance in the tab); and a **first-call
  confirmation** — the first `complete` from a given artifact pops a
  `ConfirmDialog` (*"'<title>' wants to call the model. Allow?"*) once per
  mount. A visible counter in the tab: *"this artifact has made N model calls"*.
- One-shot only (streaming genuinely unavailable). `opts` reserved.

### 8.2 `storage` — `get(k)` / `set(k, v)` / `remove(k)` / `keys()`

- Per-artifact persistent KV (survives the iframe, unlike raw `localStorage`).
- iframe → `postMessage({type:'cr-req', token, id, op:'storage.get', key})` etc.
- plugin render → `ctx.rest('/artifacts/{id}/storage/{key}', ...)` → `cr_api` →
  `cr_store` writes/reads `creator/<dir>/storage.json` (one JSON object,
  atomic-write, ~256 KB cap).
- Values are JSON. `keys()` lists the object's keys.

### 8.3 `readFile(path: string) -> Promise<string | Uint8Array>`

- Reads are gated to three roots, all **read-only**, with a visible "Exposed to
  this artifact:" indicator in the tab listing exactly what's reachable:
  1. **Artifact files** — a drop-zone in the tab; the user drags files in; they
     live in `creator/<dir>/files/`. `readFile("data.csv")` resolves here first.
  2. **The vault** (if Knowledge has one configured) — `readFile("vault:Areas/
     Argos.md")` → `cr_api` calls Knowledge's `GET /note` (already path-guarded).
     Read-only; listing via `readdir("vault:")` → Knowledge's `/tree`.
  3. **A user-set project root** — `creator.project_root` in plugin settings, or
     a "Set project folder…" button. `readFile("project:src/App.tsx")`,
     `readdir("project:")`. Path-guarded to stay under the root; no writes; a
     size cap per read (1 MB).
- iframe → `postMessage({type:'cr-req', token, id, op:'readFile', path})` →
  plugin render → `ctx.rest('/creator/readfile?...', ...)` → `cr_api` resolves
  the scheme, guards the path, returns bytes.
- If a scope isn't configured, `readFile` for that scheme rejects with a clear
  message; the indicator shows which scopes are active.

### 8.4 The bridge protocol

One message envelope both ways: `{type, token, id, ...}`. `type` ∈
`cr-req | cr-res | cr-error | cr-console`. `id` correlates request/response
(the iframe generates it). The plugin render keeps a `Map<id, resolver>`. Every
inbound field is validated and clamped; unknown `op` → `cr-res` with an error.
The parent listener is `window`-global and filters on `type` + `token` for this
mount (no origin/source check — opaque origin; nonce is the trust boundary),
with an optional `event.source === frameRef.current?.contentWindow` extra check
since Creator holds the iframe ref.

## 9. Testing

### 9.1 Backend (framework-free, matching Knowledge)

`cr_store.py` ends with `def _selfcheck()` (`assert` + `tempfile`, `HERMES_HOME`
→ a temp dir). `dashboard/selftest.py` loads `cr_store` by explicit path, appends
it to the check list, and adds a Creator HTTP round-trip via `TestClient`.

Phase 1 unit targets (each with the assert that fails if the logic breaks):

1. `sanitize_identifier` — `..` rejected, `""`→`artifact`, unicode/space
   collapse, first-char-alnum, `<dir>` suffix on a different-identifier
   collision.
2. `create` then `create` same identifier → `updated` `v2`, one `artifacts` row,
   two `versions` rows, original `type` kept, `title` updated.
3. `update` byte-identical → `unchanged`, no new version/file, **but**
   `artifact_sessions` still gets the row.
4. `update` differing only by `\r\n`↔`\n` → still `unchanged`.
5. restore (`POST /versions {restore_from:1}` at `v3`) → `v4`, content == `v1`,
   `source='restore'`, `restored_from=1`; restore of the current latest →
   `unchanged`.
6. Write path: content file + index row both committed atomically; monkeypatched
   crash between `os.replace` and `COMMIT` → the row is absent (rolled back), the
   orphan `.tmp`/file is ignored, a later `GET` works.
7. Concurrency: two threads `create` the same identifier → one `artifacts` row,
   two `versions`, no exception surfaced; and two threads `create` *different*
   identifiers that sanitize to the same `<dir>` → two rows, two distinct dirs.
8. Per-version content integrity under concurrent `update`: two racing updates →
   two versions, and `v<n>.<ext>` on disk matches the sha recorded for row `n`
   (not the loser's bytes).
9. Scan: an HTML doc ≥160 chars in an assistant message (via the monkeypatched
   `_read_assistant_messages`) → one `origin='scan'` artifact; the same content
   also `create_artifact`'d first → scan adds nothing but links the session;
   re-run scan → 0 found. Threshold rows: 20-line JS fence skipped; 60-line JS →
   `code`; 5000-char / 10-line JS → `code` (the OR); `diff` fence skipped;
   `mermaid` any length → `mermaid`; `md` ≥600 → `markdown`, `md` 200 chars →
   skipped; `svg` ≥2000 → `svg`; HTML fragment ≥1200 → `html`.
10. Caps: 1.1 MB content → error (tool + HTTP 400); a 30 KB artifact → tool
    `content` truncated ~10 KB with the marker, on-disk file intact; a 20 KB
    diff → truncated ~4 KB.
11. HTTP round-trip: `POST /artifacts/scan` on a seeded temp session → `GET
    /artifacts?session_id=` lists it `in_session:true` → `GET /v/1` → `POST
    /versions` → `GET /artifacts/{id}` shows `v2` → restore → `v3`==`v1` →
    `DELETE` → `GET` 404, `<dir>/` gone, no orphan `versions`/`artifact_sessions`
    rows.
12. Path guard: `GET /artifacts/..%2f..%2fpasswd/v/1` → 400; an identifier
    sanitizing to an escaping `<dir>` → rejected.
13. Defensive mount: monkeypatch `cr_api` import to raise → `import plugin_api`
    still succeeds, the `hw_*` routes still mount, `selftest.py` still passes.

Phase 2: `esbuild.build` of a fixture React artifact importing `recharts` →
one ESM string, no diagnostics; a syntax-error artifact → diagnostics, no
bundle; the vfs resolver pulls the right sub-deps from `MANIFEST.json`. Tailwind:
a fixture artifact using an arbitrary class (`grid-cols-[1fr_2fr]`) plus a
`@theme` token → the in-frame compiler emits matching CSS; the export path reads
the frozen `<style>` back and the offline compiler is absent from the file.
Asset route: `GET /creator/asset/react.js` → the exact bytes; `GET /creator/
asset/codemirror.js` → the exact bytes, and a `node --check`-style parse of the
vendored file (asserts it is valid ESM with no bare/relative import
specifiers, so `import(blobURL)` will resolve). CodeMirror wrapper: mounting
it against a fixture artifact yields an `EditorView`; a content prop change
replaces the doc; `readOnly` blocks edits; import failure falls back to the
textarea.

Phase 3: export of each type → a self-contained file that opens standalone;
`react` export is minified and inlines Tailwind. Gist path: `gh` present →
parses a URL from stubbed output; neither `gh` nor token → the setup message.

Phase 4: bridge envelope validation (bad `token` dropped, bad `op` → error
`cr-res`, oversized payload clamped); `complete` throttle + budget + first-call
confirm enforced in the plugin render; `storage` round-trips through
`storage.json`; `readFile` scheme guards (`vault:` with no vault → reject;
`project:../escape` → reject; an artifact file → bytes).

### 9.2 Renderer

No headless Hermes — a manual checklist per phase (create from a chat; edit +
save → new version; step + restore; each content type renders; the picker;
backend-restart resilience; Phase 2: a React artifact runs, an error shows, the
console panel works, arbitrary Tailwind classes render, the CodeMirror editor
loads and highlights/searches and falls back to the textarea if its asset is
removed; Phase 3: export opens standalone (and styled, with no compiler in the
file), Gist link works; Phase 4:
`complete` prompts once then works, `storage` persists across a tab close,
`readFile` sees only what the indicator lists).

## 10. Risks

1. **Model under-uses the tools** — the scan is the hedge; the prompt section is
   tuned in testing.
2. **`esbuild.build` latency per preview keystroke burst** — mitigated by debounce
   + content-hash cache + lib-source cache; a fast path that re-bundles only when
   imports change is a later refinement. The Tailwind compiler runs inside the
   iframe (own thread per frame), so it doesn't add to the plugin-renderer cost.
3. **`creator-libs/` is ~16 MB in git** — a one-time cost; `.gitignore` does not
   exclude it; updating a lib (esbuild, CodeMirror, a React lib) re-vendors one
   file. The offline bundle step (esbuild bundling CodeMirror + the lib set into
   zero-import ESM files) is a committed script, not a runtime dependency.
4. **Threshold drift from Hermes core** — Creator owns the thresholds (not a
   port); one named constant block, a comment pointing at the upstream file.
5. **Shared-import fragility** — `cr_store.py` stdlib-only + relative-import-free;
   `cr_api.py` keeps `hermes_*` imports in function bodies; the defensive mount +
   `import logging`. Test 13 covers it.
6. **One `plugin.js` grows to ~3000 lines** — Creator adds only `cr_*` top-level
   symbols, touches zero Knowledge symbols, one guarded call in `register`. A
   split is impossible (SDK bans relative imports). Revisit only if it nears
   ~350 KB.
7. **`window.hermes.complete` cost** — throttle + per-mount budget + first-call
   confirm + a visible counter. A local model loop pegs the GPU but spends no
   money; a paid model is capped by the budget.
8. **`readFile` surface** — three explicit read-only scopes, a visible indicator,
   path-guarded, size-capped, each opt-in.
9. **Same-turn artifacts not scanned** — SessionDB lags the live turn; caught on
   the next turn or by a tool call.
10. **Publish is a substitute, not a match** — Claude has first-party hosting;
    Gist is the closest frictionless analogue; droplet-hosted publish is the
    documented path to a real match.

## 11. Build order

Phase 1 → 2 → 3 → 4, each landing green (`selftest.py` + `node --check`) before
the next. Phase 1 is a complete product on its own. Phase 2 is the largest and
highest-risk (esbuild-wasm, the asset pipeline, the iframe host). Phases 3 and 4
are additive and small relative to 2.

## 12. Install

1. `hermes plugins enable hermes-workspace` (writes `plugins: {enabled:
   [hermes-workspace]}` — currently missing; this also enables the shipped
   Knowledge module's agent + dashboard halves).
2. In **Hermes Desktop → Settings → Plugins**, toggle **hermes-workspace** on
   (the desktop renderer is gated separately from `config.yaml`).
3. Re-copy `plugin/hermes-workspace/` to `~/.hermes/plugins/`.
4. Restart Hermes Desktop.
5. The `create_artifact` / `update_artifact` / `read_artifact` (/ `publish_artifact`)
   tools appear to the agent; "Open Creator" is in the command palette.
6. Optional: set `creator.project_root`, `creator.github_token`, or drop an
   `agent_rules.md` in the vault.
