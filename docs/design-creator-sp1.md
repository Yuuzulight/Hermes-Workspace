# Creator module — SP1: core artifact loop (design)

Status: draft for review
Last updated: 2026-08-30

## 1. Purpose

Give Hermes a Claude-Artifacts-equivalent, as a second module (**Creator**) inside
the existing `hermes-workspace` plugin alongside **Knowledge**.

SP1 is the **core artifact loop only**: the agent tools, a transcript-scan
fallback, a persistent versioned store, and a docked workspace tab with an editor,
a preview, and a version stepper. Content types: **code, HTML, SVG, Markdown,
Mermaid**.

Explicitly deferred to later sub-projects and out of scope here:

- **SP2** — the React/JSX runtime (bundled curated import set, JSX transpile,
  import map).
- **SP3** — standalone `.html` export + "Publish to Gist".
- **SP4** — the in-artifact `window.hermes.complete` / `.storage` / `.readFile`
  runtime.

## 2. What Hermes already provides (and why SP1 builds its own)

Hermes Desktop ships a built-in artifact system (`apps/desktop/src/store/
artifacts.ts`, `right-rail/preview-artifact.tsx`): auto-promotion of substantial
fenced blocks, a version stepper, a sandboxed `<iframe sandbox="allow-scripts">`
renderer, rendered/source toggle, download. It is **memory-only** — the registry
is a renderer atom rebuilt from the transcript on every reload — and it is
**app-internal** (not importable by a plugin). It has no persistence, no diff, no
restore, and does not run JSX.

SP1's value over the built-in is **durability and an explicit model API**: a
persistent versioned store, `create_artifact`/`update_artifact`/`read_artifact`
tools the model calls deliberately (like Claude's `<antArtifact identifier=…>`),
a real editor, and a cross-session library. It runs in parallel with the built-in
system; the two do not interact.

## 3. Constraints (verified against the Hermes 0.20.6 source)

### 3.1 Plugin loading

- `hermes-workspace` is a **user directory plugin**. It is gated by exactly one
  list: `plugins.enabled` in `~/.hermes/config.yaml`. That single list enables
  all three surfaces — the agent `register(ctx)`, the dashboard route mount, and
  the renderer asset/list visibility. There is no per-surface enable.
- **Prerequisite, not currently satisfied:** the dev machine's `config.yaml` has
  no `plugins:` key, so `hermes-workspace` (Knowledge included) is not loading
  anywhere today. `hermes plugins enable hermes-workspace` writes
  `plugins: { enabled: [hermes-workspace] }` and must be run once.
- A directory plugin needs `plugin.yaml` **and** a root `__init__.py` exporting
  `def register(ctx)`. Today `hermes-workspace` has no `__init__.py`, so its
  agent-side load currently fails with a harmless `WARNING: No __init__.py`
  (the agent half ships nothing). Adding `__init__.py` converts that into a
  successful load. Desktop discovery (`runtime-loader.ts` walks to
  `desktop/plugin.js`) and dashboard discovery (`dashboard/manifest.json` scan)
  are unaffected — no regression to Knowledge.
- The manifest parser reads the first 8 KB of `__init__.py` and runs
  `_detect_kind_from_source`. The first 8 KB **must not** contain the tokens
  `register_memory_provider`, `MemoryProvider`, or the pair
  `register_provider` + `ProviderProfile` — any of those reroute the plugin to
  the memory/model-provider discovery system and it never loads.
- `provides_tools` in `plugin.yaml` is **advisory / display only** for a user
  plugin (`hermes plugins list`, the dashboard hub). Tools register purely via
  `ctx.register_tool(...)` in `register(ctx)`.
- `provides_hooks` has **zero loader consumers** — omit it. SP1 registers no
  hooks. (`register_system_prompt_section` is a plain `PluginContext` method, not
  a hook, and is not gated by any manifest field.)

### 3.2 Agent-tool handlers

- Run **in-process in the agent, no sandbox.** Signature
  `def handler(args: dict, **kwargs) -> str`; `kwargs` carries `task_id`,
  `session_id`, `enabled_tools`, `user_task`. Arbitrary Python is allowed —
  import siblings, write files, `os.replace`, SQLite, network.
- The tool JSON-schema is passed **in Python** to `ctx.register_tool(name,
  toolset, schema, handler, ...)`.
- Return: a **string** (built with `tools.registry.tool_result()` /
  `tool_error()`). Handlers must not raise — exceptions are caught, logged, and
  returned as an error string.
- Tools register under a toolset name (`"creator"`). Visible under the default
  toolset config; a profile with an explicit `agent.toolsets:` allow-list would
  need to add `"creator"` (documented, not handled in SP1).

### 3.3 System-prompt section

`ctx.register_system_prompt_section(id, content, position="after_memory",
max_chars≤4000)`. `id` is `[a-z0-9._-]`, 1–128 chars. Rendered **once at session
start** and frozen into the persisted prompt (cache-safe; not re-read for an
existing session). `position` must be `"after_memory"`. Duplicate `id` raises.
All plugin sections together ≤ 8000 chars / 32 sections.

### 3.4 Processes and the shared store

- The **agent process** and the **dashboard web server** are separate sibling
  processes. There is no localhost HTTP path from the agent to the dashboard and
  the dashboard may not be running. They coordinate only through the shared
  `~/.hermes/` tree.
- The plugin data dir is `plugin_storage.plugin_data_dir("hermes-workspace")` →
  `<HERMES_HOME>/plugin-data/hermes-workspace/`, profile-aware, resolved through
  `get_hermes_home()` on every call. **Not** the plugin's install dir
  (`<HERMES_HOME>/plugins/hermes-workspace/data/`, which `hermes plugins update`
  clobbers — the current `hw_store.py` uses that location; Creator must not).
- `plugin_storage.plugin_db(name, filename)` returns a WAL-mode `sqlite3`
  connection (`check_same_thread=False`) purpose-built for an agent-tool writer
  and a dashboard reader to coexist with snapshot isolation. There is **no
  file-lock helper**, and `fcntl.flock` does not exist on Windows.

### 3.5 Dashboard module loading

- `_mount_plugin_api_routes` execs `dashboard/plugin_api.py` as a **bare
  top-level module** (`spec_from_file_location`, no `submodule_search_locations`)
  and wraps the whole exec in one `try/except` — **any** import-time exception in
  a file it pulls in unmounts **all** of `hermes-workspace`'s routes (all of
  Knowledge). Creator's addition to `plugin_api.py` must be defensively wrapped.
- The existing `sys.path.insert(0, os.path.dirname(__file__))` in `plugin_api.py`
  puts only `dashboard/` on `sys.path`. A root-level `cr_store.py` is **not**
  importable by name from the dashboard, and **inserting the plugin root onto
  `sys.path` is forbidden** — the plugin root will hold `tools.py`, which would
  shadow Hermes' own `tools/` package (`from tools.registry import registry` is
  used throughout the dashboard) and break it. The dashboard side loads
  `cr_store.py` by **explicit path** via `importlib.util.spec_from_file_location`.
- `cr_store.py` and `cr_api.py` restrict **module-level** imports to the stdlib +
  FastAPI. `from hermes_constants import get_hermes_home` is allowed only
  try/except-guarded. `from hermes_state import SessionDB` goes **inside** the
  scan handler, try/except-guarded (the Knowledge `selftest.py` does a bare
  `import plugin_api` in a plain-FastAPI context and must keep working).

### 3.6 Renderer

- One `desktop/plugin.js`, imports only `@hermes/plugin-sdk`, `react`,
  `react/jsx-runtime`. No relative imports, no build step. The 512 KB source cap
  is a non-issue on modern shells (16 MB); the file is ~42 KB today.
- `host.openWorkspace(id, {render, dock, title, minWidth, onClose})` — a docked,
  single-instance React tab in the main workspace zone. **Feature-detect
  `typeof host.openWorkspace === 'function'`** with a `PANES_AREA` fallback.
- The SDK exports `Streamdown` (markdown + Shiki + Mermaid, via `streamdown` v2),
  `Button`, `EmptyState`, `SegmentedControl`, `StatusDot`, `useValue`, `atom`,
  `PANES_AREA`, `STATUSBAR_AREAS`, `PALETTE_AREA`, `host.*`, `ctx.*`. **No code
  editor component is exported.**
- `ctx.rest(path, opts)` → the plugin's own dashboard backend (query params in
  the path — no `query` option). `ctx.socket(path, onMessage)` → a live WS to the
  backend, **but it resolves to a no-op on OAuth remotes** and is documented as
  "an accelerator over your polling, never a replacement."
- `ctx.storage` is synchronous plugin-scoped JSON KV.

### 3.7 Reading sessions

- `host.request('session.history', {session_id})` is **live-only** — it serves
  only a session currently attached in the gateway process.
- Cross-session: the renderer uses `host.request('session.list', ...)` (DB-backed,
  all stored sessions) or the REST `/api/sessions/{id}/messages`; server-side code
  reads `SessionDB` directly (`list_sessions_rich`, `get_messages`), as
  `plugins/hermes-achievements` does.

### 3.8 Product rule

No trace of AI authorship anywhere in the repo, code, comments, or commit
messages.

## 4. File layout

Added to `plugin/hermes-workspace/`:

```
plugin.yaml            + provides_tools: [create_artifact, update_artifact, read_artifact]  (display only)
__init__.py            NEW, plugin root. def register(ctx): from . import tools; tools.register(ctx)
                       — 3 ctx.register_tool(...) + 1 ctx.register_system_prompt_section(...).
                       First 8 KB free of the memory/provider tokens (§3.1).
tools.py               NEW, plugin root. 3 handlers + the 3 schema dicts inline.
                       from . import cr_store
cr_store.py            NEW, plugin root. stdlib only + try/except `from hermes_constants import
                       get_hermes_home`. NO relative imports. Imports cleanly as a package
                       submodule (agent) AND as an explicit-path module (dashboard).
desktop/plugin.js      + a "// ===== CREATOR =====" section. Knowledge section byte-unchanged.
                       Creator registration in its own try/catch inside register(ctx).
dashboard/plugin_api.py  + defensively:
                           try:
                               import cr_api
                               router.include_router(cr_api.router)
                           except Exception as e:
                               logging.getLogger(__name__).warning("creator API not mounted: %s", e)
dashboard/cr_api.py    NEW. Module-level imports = stdlib + FastAPI only. Loads cr_store by
                       explicit path:
                           _p = Path(__file__).resolve().parent.parent / "cr_store.py"
                           _s = importlib.util.spec_from_file_location("hw_cr_store", _p)
                           cr_store = importlib.util.module_from_spec(_s); _s.loader.exec_module(cr_store)
                       Exposes router = APIRouter(). SessionDB import inside the scan handler.
                       @router.websocket("/events") self-authorizes (copy kanban's
                       _ws_upgrade_authorized: lazy `hermes_cli.web_server._ws_auth_ok`, accept
                       on ImportError).
dashboard/selftest.py  append "cr_store" to MODULES + a Creator HTTP round-trip. Additive.
```

## 5. The store

### 5.1 On disk

```
<HERMES_HOME>/plugin-data/hermes-workspace/creator/
├── creator-index.db             # plugin_db("hermes-workspace", "creator-index.db") — WAL SQLite, authoritative
└── <dir>/                        # <dir> = sanitized identifier, collision-suffixed
    ├── v1.<ext>
    └── v2.<ext>
```

The index is truth. A version file with no index row is an ignored orphan. An
index row whose file is missing reads as "version unavailable", never an error.

Sanitize an identifier for `<dir>`: lowercase, keep `[a-z0-9._-]`, collapse runs
of `-`, strip leading/trailing `-.`, reject `..`, cap 64 chars, empty →
`artifact`; on a `<dir>` collision with a *different* identifier append `-2`,
`-3`, …

### 5.2 Schema

```sql
CREATE TABLE artifacts (
  identifier    TEXT PRIMARY KEY,        -- model-chosen, verbatim
  dir           TEXT NOT NULL UNIQUE,
  type          TEXT NOT NULL,           -- code | html | svg | markdown | mermaid
  language      TEXT,                    -- when type=code
  title         TEXT NOT NULL,
  origin        TEXT NOT NULL,           -- 'tool' | 'scan'
  created_at    REAL NOT NULL,
  updated_at    REAL NOT NULL
);
CREATE TABLE versions (
  identifier    TEXT NOT NULL REFERENCES artifacts(identifier),
  n             INTEGER NOT NULL,        -- 1-based
  ext           TEXT NOT NULL,
  sha256        TEXT NOT NULL,           -- of normalized content (§5.4)
  bytes         INTEGER NOT NULL,
  source        TEXT NOT NULL,           -- create | update | user-edit | restore | scan
  restored_from INTEGER,                 -- the n copied, when source='restore'
  created_at    REAL NOT NULL,
  PRIMARY KEY (identifier, n)
);
CREATE TABLE artifact_sessions (
  identifier    TEXT NOT NULL,
  session_id    TEXT NOT NULL,
  first_seen    REAL NOT NULL,
  PRIMARY KEY (identifier, session_id)
);
CREATE INDEX ix_versions_sha ON versions(sha256);
CREATE INDEX ix_artifacts_updated ON artifacts(updated_at);
```

### 5.3 Write path (single writer module, `cr_store.py`)

Every mutating op:

1. Resolve the data dir + open `plugin_db()` **fresh** (profile-aware — never
   cache a module-level Path or connection).
2. Write the version content file: `<dir>/v<N>.<ext>.tmp` in the same directory,
   `flush` + `fsync`, `os.replace` → `<dir>/v<N>.<ext>`.
3. Commit the index rows inside `BEGIN IMMEDIATE` (artifact upsert, version
   insert, `artifact_sessions` upsert, `artifacts.updated_at`).

Order matters: content file first, then the index row. A crash between the two
leaves an orphan file the index never references.

Concurrency: two `create_artifact` for the same identifier race on the
`artifacts` PK → the loser gets `sqlite3.IntegrityError` and retries the whole op
as an `update_artifact` (append a version). No lock files.

### 5.4 Content normalization (the hash input)

`content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")`, then
`sha256`. Line endings and a trailing newline only — nothing that changes code
meaning. Used by: `update` no-op detection, `restore` no-op detection, scan
dedupe.

### 5.5 Type → extension

`code` → language extension (`python`→`.py`, `typescript`→`.ts`, …; unknown →
`.txt`). `html` → `.html`. `svg` → `.svg`. `markdown` → `.md`. `mermaid` →
`.mmd`.

### 5.6 Caps

- Tool-result `content`: 10 KB, truncated with `… (full content in the artifact)`.
- Tool-result `diff`: 4 KB, truncated with a marker.
- On-disk version content: 1 MB hard limit — larger is rejected with
  `tool_error("content exceeds 1 MB limit")`.

## 6. Agent tools (`tools.py`)

### 6.1 Schemas

`create_artifact` — `{identifier: string, type: "code"|"html"|"svg"|"markdown"|
"mermaid", language?: string, title: string, content: string}`, required
`identifier, type, title, content`. Description: prefer this over inlining
substantial standalone content; reuse the identifier via `update_artifact`.

`update_artifact` — `{identifier: string, content: string}`, both required.

`read_artifact` — `{identifier: string}`, required.

### 6.2 Behaviour

- **`create_artifact`** — sanitize `identifier`. If it already exists → append a
  content version (the `update_artifact` path); the artifact keeps its **original
  `type`** (the new `type` arg is ignored — changing type mid-stream would break
  the version/preview story), and `title` is updated if the arg is non-empty.
  Result `action: "updated"` with `note` that the identifier existed. Else new
  artifact + `v1`. Record `(identifier, session_id)`. Validate `type`; `type=html`
  content need not be a full document (the preview wraps a fragment). Result:
  `{identifier, version, type, title, action: "created"|"updated", note}`. No
  content echoed.
- **`update_artifact`** — identifier must exist, else
  `tool_error("no artifact '<id>' — call create_artifact first")`. If normalized
  content hashes identical to the latest version → no-op, result
  `{identifier, version: N, action: "unchanged"}`. Else append `v(N+1)`,
  `source: "update"`. Result adds `diff` (unified vs `v(N-1)`, ≤4 KB) and
  `content` (≤10 KB). Record `(identifier, session_id)`.
- **`read_artifact`** — identifier must exist. Result
  `{identifier, version: N, type, title, version_count, updated_at, content: "<current, ≤10 KB>"}`.

### 6.3 System-prompt section

`register_system_prompt_section("creator", <static string ~900 chars>,
position="after_memory")`:

```
## Creator artifacts

You have create_artifact / update_artifact / read_artifact. Use them for
substantial standalone content the user will want to preview, revise, and keep —
a web page, an SVG, a code file of ~40+ lines, a Markdown document, a Mermaid
diagram. Put that content in the artifact, not in your reply (a one-line pointer
is enough).

- Choose a short stable kebab-case identifier per artifact and reuse it with
  update_artifact on later turns — each update is a version the user can step
  through and revert.
- Call read_artifact before an update if the user may have edited it themselves.
- type=html content must be a complete standalone document. type=code takes a
  language.
- Small snippets, inline examples, and command output stay in your reply as
  normal code blocks.
```

## 7. Server-side transcript scan (`cr_api.py`, `POST /artifacts/scan {session_id}`)

Reads the session's assistant messages via `SessionDB` (lazy import, try/except),
extracts fenced blocks, applies the thresholds below (**ported from Hermes'
`lib/artifact-detect.ts`; documented as a sync-with-upstream maintenance point**):

| Signal | Threshold → type |
|---|---|
| `<!doctype` / `<html` at the start | ≥ 160 chars → `html` |
| other HTML (`<`… tags, no doc wrapper) | ≥ 1200 chars → `html` |
| `<svg` | ≥ 2000 chars → `svg` |
| fence lang `mermaid` | any length → `mermaid` |
| fence lang `md` / `markdown` | ≥ 600 chars → `markdown` |
| other fence lang | ≥ 48 lines **or** ≥ 3000 chars → `code`, `language = lang` |
| lang in `{diff, patch, console, text, txt, log, output, sh-output}` | skip |

`mermaid` deliberately diverges from Hermes core (which excludes it) — our design
treats a Mermaid diagram as artifact-worthy.

For each candidate: normalize + hash → **skip** if the hash matches any existing
version of any artifact. Else `create` with `origin="scan"`, `identifier` = a
slug from `<title>` / `<h1>` / first declaration / lang, plus `-` and the first 8
hex of the content hash (deterministic — a re-scan of unchanged content produces
the same identifier and dedupes anyway); record `(identifier, session_id)`.

The scan is **idempotent** (re-running skips everything already captured) and
debounced server-side (skip if run for this session within ~10 s). It **cannot**
recover an artifact from the current live turn (SessionDB lags the turn) —
acceptable for SP1.

Trigger: the renderer calls it when the Creator tab opens for a session and after
each assistant turn completes.

## 8. HTTP API (`cr_api.py`, mounted at `/api/plugins/hermes-workspace/`)

```
GET  /artifacts?session_id=&scope=chat|library
                              -> {artifacts: [{identifier, type, title, version, updated_at, origin}]}
GET  /artifacts/{id}          -> {identifier, type, language, title, version_count, versions:
                                   [{n, source, restored_from, created_at, bytes}], updated_at}
GET  /artifacts/{id}/v/{n}    -> {identifier, n, type, content}         (JSON; content capped at 1 MB)
POST /artifacts/{id}/versions {content}
                              -> {identifier, version, action: "updated"|"unchanged"}   (source='user-edit')
POST /artifacts/{id}/restore  {n}
                              -> {identifier, version, action: "restored"|"unchanged", restored_from: n}
POST /artifacts/scan          {session_id} -> {found: N, skipped: M}
DELETE /artifacts/{id}        -> {ok: true}          (removes the row + the <dir>/; irreversible)
WS   /events                  -> {type: "artifact", identifier, version} frames on any change
```

`GET /artifacts/{id}` also returns `session_count` (how many sessions the
identifier appears in) so the picker can badge shared artifacts.

Every `{id}` is the raw identifier; the handler resolves it to `<dir>` via the
index and never joins an unsanitized string to a path. `GET /v/{n}` and
`DELETE` guard that the resolved path stays under `creator/`.

Path/vault guard style and per-request data-dir resolution follow the Knowledge
module's fixed patterns (no long-cached singletons — that was a real bug there).

## 9. Renderer (`// ===== CREATOR =====` in `desktop/plugin.js`)

### 9.1 Registration

Inside `register(ctx)`, wrapped in its own `try/catch` so a Creator throw cannot
take down the Knowledge pane:

- If `typeof host.openWorkspace === 'function'` → the Creator tab is an
  `openWorkspace` pane (`dock: { pane: 'workspace', pos: 'right' }`, `minWidth`
  ~`'26rem'`, a `title`). Else → a `PANES_AREA` right-side pane.
- `PALETTE_AREA`: "Open Creator", "Creator: rescan this chat for artifacts".
- `STATUSBAR_AREAS.right`: a "◆ Creator" item that opens the tab.
- Not auto-opened. On a `create`/`update` while the tab is closed → a
  `host.notify` toast (*"Sales Dashboard v1 — open in Creator"*, action opens the
  tab). When the tab is open it auto-follows the latest artifact of the focused
  session; if that session has no artifacts, the tab shows an `EmptyState`
  pointing at the library.
- Scan trigger: the Creator section calls `POST /artifacts/scan {session_id}`
  when the tab first opens for a session and whenever `host.state.busy` for the
  focused session transitions true→false (turn end), debounced client-side.

### 9.2 Tab

Header: an artifact-picker dropdown (two sections — **This chat**:
`artifact_sessions.session_id == host.state.focusedStoredSessionId`; **Library**:
all, by `updated_at desc`; one artifact shown at a time), a version stepper
(`◀ v3/5 ▶` · `latest` · `↺ restore this version`), a layout button
(split / toggle / preview-only — auto split ≥ ~50rem, toggle when narrow;
remembered in `ctx.storage`), a `⋯` overflow with **Delete artifact** (confirm →
`DELETE /artifacts/{id}`), and a **Copy** button (`ctx.os.writeClipboard` the
current content). "Open in browser" for `type=html` is SP3 (needs the
export-to-file path).

Body:

- **Editor** — a styled `<textarea>` (monospace, tab-to-indent, line count, dirty
  dot). Explicit **Save** (button + ⌘S) → `POST /artifacts/{id}/versions`. Not
  autosave. Stepping to an older version shows it read-only.
- **Preview** —

  | type | preview |
  |---|---|
  | `code` | `<Streamdown>` with a ```` ```<language> ```` fence (Shiki) — the editor is primary, this is "read" mode |
  | `markdown` | `<Streamdown>{content}</Streamdown>` |
  | `mermaid` | `<Streamdown>` with a ```` ```mermaid ```` fence |
  | `html` | own `<iframe sandbox="allow-scripts" srcdoc={…}>`, opaque origin; a fragment is wrapped in a minimal doc + CSS reset + light theme |
  | `svg` | `<img src="data:image/svg+xml;base64,…">` — inherently script-safe, no sanitizer |

### 9.3 Refresh & resilience

`ctx.rest` poll every ~2 s while the tab is open (**primary**);
`ctx.socket('/events')` as an accelerator (no-op on OAuth remotes — must not be
depended on). A failed poll (backend restart) keeps the last-rendered state,
retries next tick, and toasts on error — never wipes the view.

## 10. Testing

Backend: framework-free, matching the Knowledge module. `cr_store.py` ends with
`def _selfcheck()` (`assert` + `tempfile`, `HERMES_HOME` pointed at a temp dir).
`dashboard/selftest.py` appends `"cr_store"` to `MODULES` and a Creator HTTP
round-trip via `TestClient`.

Backend unit targets (each with the one assert that fails if the logic breaks):

1. `sanitize_identifier` — `..` rejected, `""` → `artifact`, unicode/spaces
   collapsed, `<dir>` collision suffixing.
2. `create` then `create` same identifier → second is an `updated` with `v2`,
   one `artifacts` row, two `versions` rows.
3. `update` with byte-identical content → `unchanged`, no new version, no file.
4. `update` with `\r\n` vs `\n` only differing → still `unchanged` (normalization).
5. `restore` of `v1` when at `v3` → new `v4`, `v4` content == `v1` content,
   `source='restore'`, `restored_from=1`. `restore` of the current latest →
   `unchanged`.
6. Write path: content file present before the index row; a simulated crash
   between them (monkeypatch) leaves an orphan file the index ignores and a
   subsequent `GET` still works.
7. Concurrency: two threads `create` the same identifier → one row, two versions,
   no exception surfaced (loser retried as update).
8. Scan: an HTML doc ≥160 chars in an assistant message → one `origin='scan'`
   artifact; the same content also passed to `create_artifact` first → scan finds
   the hash and adds nothing; re-run scan → 0 found.
9. Scan thresholds: a 20-line JS fence → skipped; a 60-line JS fence → `code`;
   a `diff` fence → skipped; a `mermaid` fence of any length → `mermaid`.
10. Caps: 1.1 MB content → `tool_error`; a 30 KB artifact → tool-result `content`
    truncated at ~10 KB with the marker, on-disk file intact.
11. HTTP round-trip (`selftest.py`): `POST /artifacts/scan` on a seeded temp
    session → `GET /artifacts?scope=chat` lists it → `GET /artifacts/{id}/v/1`
    returns content → `POST /versions` → `GET /artifacts/{id}` shows `v2` →
    `POST /restore {n:1}` → `v3` == `v1` → `DELETE` → `GET` 404.
12. Path guard: `GET /artifacts/..%2f..%2fetc%2fpasswd/v/1` → 400; an identifier
    whose sanitized `<dir>` would escape → rejected.
13. Defensive mount: monkeypatch `cr_api` import to raise → `import plugin_api`
    still succeeds and the `hw_*` routes still mount (the `try/except` in
    `plugin_api.py`).

Destructive / edge cases:

- Dashboard down while `create_artifact` fires → the write lands; the next
  `GET /artifacts` sees it.
- Two sessions produce artifacts with the same identifier → the second `create`
  becomes an update; both sessions appear in `artifact_sessions`.
- Profile switch mid-use → the next op resolves a different `plugin-data` dir;
  the previous profile's artifacts are simply not listed (no crash, no cache
  serving the wrong profile).
- `plugin.js` `node --check` passes; the Creator section's registration
  `try/catch` swallows a thrown error without affecting Knowledge.

Renderer: no headless Hermes — a manual checklist (open the tab, create an
artifact from a chat, edit + save → new version, step + restore, each of the 5
content types renders, library vs this-chat, backend-restart resilience).

## 11. Risks

1. **Model under-uses the tools.** A non-Claude model may ignore
   `create_artifact` despite the prompt section. Mitigation: the transcript scan
   catches substantial fenced content regardless; the prompt section is tuned in
   testing.
2. **`detectArtifact` threshold drift.** The Python port can fall out of sync
   with Hermes core's `lib/artifact-detect.ts`. Mitigation: the thresholds live
   in one named constant block with a comment pointing at the upstream file;
   revisit on Hermes upgrades.
3. **Textarea, not an editor.** SP1 has no syntax highlighting while editing (the
   SDK exports no editor). Acceptable for an artifact tool; a real editor is an
   upstream ask or a later SP.
4. **`ctx.socket` unreliability.** No-op on OAuth remotes; the poll must fully
   stand alone (it does — socket is only an accelerator).
5. **Shared-store import fragility.** `cr_store.py` must stay stdlib-only and
   relative-import-free so it loads both as a package submodule (agent) and by
   explicit path (dashboard); `cr_api.py` must keep `hermes_*` imports inside
   function bodies. A slip here breaks either the agent tools or the Knowledge
   selftest. Covered by test 13 and the module-import discipline.
6. **Same-turn artifacts not captured.** SessionDB lags the live turn, so the
   scan only sees prior turns. If the model neither tool-calls nor the user
   rescans, a current-turn artifact is missed until the next turn. Acceptable for
   SP1.

## 12. Out of scope for SP1

The React/JSX runtime (SP2), standalone `.html` export and Gist publish (SP3),
the in-artifact `window.hermes` runtime (SP4), a real code editor, artifact
sharing between users, `openWorkspace` layouts beyond a single docked tab, and
any change to Hermes' built-in artifact system.

## 13. Install delta (for the README)

1. `hermes plugins enable hermes-workspace` (once — currently missing; this also
   enables the already-built Knowledge module).
2. Re-copy `plugin/hermes-workspace/` to `~/.hermes/plugins/` (now includes the
   Creator files).
3. Restart Hermes Desktop.
4. The `create_artifact` / `update_artifact` / `read_artifact` tools appear to
   the agent; "Open Creator" is in the command palette.
