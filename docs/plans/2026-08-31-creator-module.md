# Creator Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Hermes Workspace **Creator** module — a Claude-Artifacts equivalent, added alongside the shipped Knowledge module inside the same `hermes-workspace` plugin: agent tools that create durable versioned artifacts, a docked pane that previews/edits/versions them, a React runtime, standalone export + Gist publish, and a `window.hermes` in-artifact runtime.

**Architecture:** Backend (Python) is the source of truth. `cr_store.py` (plugin root, stdlib only) owns all behaviour — a SQLite index it opens directly (not via `plugin_db`) plus per-version content files under `<plugin-data>/hermes-workspace/creator/`. `cr_tools.py` (plugin root) registers 3 agent tools + a system-prompt section. `dashboard/cr_api.py` exposes the same behaviour over HTTP under `/api/plugins/hermes-workspace/creator/`. The renderer half is a new `// ===== CREATOR =====` block in the existing single-file `desktop/plugin.js`, reached from Knowledge's `register(ctx)` by one guarded `crRegister(ctx)` call. Phase 2+ vendors libraries in `dashboard/creator-libs/` (a committed, pinned build) and ships them to the renderer as JSON envelopes because `ctx.rest` is JSON-only.

**Tech Stack:** Python 3.11+ stdlib (`sqlite3`, `hashlib`, `json`, `re`, `pathlib`, `base64`, `subprocess`), FastAPI `APIRouter` (already a Hermes dependency), `starlette.testclient` for backend tests. Renderer: ESM + React via `@hermes/plugin-sdk`, `react`, `react/jsx-runtime` only — no build step. Vendored libs (Phase 2+): esbuild-wasm, a curated React import set, a Tailwind compiler, CodeMirror 6 — each pre-bundled offline into a zero-import ESM file by `dashboard/creator-libs/build.mjs` (pinned toolchain + lockfile).

**Spec:** `docs/design-creator.md` — read it first. This plan argues from that spec; executors read both. Section references below (§5.3, §6.2, …) point into that file for verbatim blocks (the SQL schema, the esbuild config, threshold tables, the asset envelope shape).

## Global Constraints

Copied from the spec. Every task's requirements implicitly include this section.

- **Renderer imports:** `desktop/plugin.js` may import only `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`. Any other bare/relative/URL specifier is an up-front load error. `plugin.js` stays **one file**; Creator adds only `cr*`-prefixed top-level symbols and references **zero** Knowledge symbols. It captures its own `const crCtx = ctx` inside `crRegister` — never Knowledge's module-level `CTX`.
- **`crRegister(ctx)` call site:** exactly one line, `try { crRegister(ctx) } catch (e) { host.notifyError?.(e, 'Creator failed to load') }`, added to Knowledge's **synchronous** `register(ctx)` body immediately after the `palette-reindex` `ctx.register({...})` call and before the async `rpcAvailable().then(...)` tail (`desktop/plugin.js:1189`).
- **`ctx.rest` is JSON-only.** Every response body is `JSON.parse`d; HTML/`text/html` is rejected; there is no `responseType`/`arraybuffer`. All binary/large assets cross as a JSON envelope `{name, encoding: "utf8"|"base64", data, sha256}`; the renderer decodes. GET params are baked into the path (no `query` option). Pass an explicit `timeoutMs` for payloads over a few MB (default is 30 s).
- **Store:** `cr_store` never uses `plugin_storage.plugin_db()` (it rejects nested filenames). It resolves `plugin_data_dir("hermes-workspace")` via a **function-body** `try: from plugins.plugin_storage import plugin_data_dir except Exception:` with a local `get_hermes_home` fallback, then opens `sqlite3.connect(<data>/creator/creator-index.db)` itself and applies, per connection: `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`, `isolation_level=None`, `check_same_thread=False`; `try/finally: conn.close()`. It never caches a `Path`. It never writes under the install dir `<HERMES_HOME>/plugins/hermes-workspace/`.
- **Module import safety:** `cr_store.py` and `dashboard/cr_api.py` have **module-level imports = stdlib + FastAPI only**. `plugin_data_dir`, `SessionDB`, and Knowledge-module imports live in function bodies, try/except-guarded. `cr_store.py` has **no relative imports** (it is loaded both as a package submodule by `__init__.py` and by explicit path from `dashboard/`). `dashboard/cr_api.py` loads `cr_store.py` by explicit path (`importlib.util.spec_from_file_location`). The plugin root is **never** added to `sys.path`.
- **Defensive dashboard mount:** `dashboard/plugin_api.py` gains `import logging` and wraps the Creator wiring: `try: import cr_api; router.include_router(cr_api.router) except Exception as e: logging.getLogger(__name__).warning("creator API not mounted: %s", e)`. Any import-time throw in a Creator file must leave every `hw_*` route mounted.
- **Route namespace:** `cr_api.router = APIRouter(prefix="/creator")`. Every Creator route is under `/api/plugins/hermes-workspace/creator/…`.
- **`plugin.yaml`:** add `kind: standalone` and `provides_tools: [create_artifact, update_artifact, read_artifact]`. No `provides_hooks`. The tool file is **`cr_tools.py`**, never `tools.py`.
- **Session ids:** the renderer sends `host.state.focusedStoredSessionId` (the stored `YYYYMMDD_HHMMSS_xxxxxx` key, equal to a tool handler's `kwargs["session_id"]`) for the scan POST, `GET /creator/artifacts?session_id=`, and auto-follow. It sends `host.state.focusedSessionId || host.state.activeSessionId` (the runtime id; `||` not `??` because it can be `""`) only for `llm.oneshot` (Phase 4).
- **`read_artifact` returns full content** — never truncated. Only the `create_artifact` / `update_artifact` *result* echo is capped (10 KB `content`, 4 KB `diff`). `update_artifact` rejects a payload containing the pathological-size note string.
- **`versions.source`** is exactly one of `create | update | user-edit | restore | scan`, each emitted by exactly one writer (spec §5.2.1). No SQL `CHECK` on `artifacts.type` (Phase 2 adds `react` with no migration).
- **Tests are framework-free.** `cr_store.py` ends with `def _selfcheck()` using `assert` + `tempfile` with `HERMES_HOME` → a temp dir, and `if __name__ == "__main__": _selfcheck(); print("ok")`. `dashboard/selftest.py` loads `cr_store` by explicit path, runs its `_selfcheck()`, and adds Creator HTTP round-trips via `TestClient`. Renderer changes are gated by `node --check desktop/plugin.js` plus a manual checklist (spec §9.2) — there is no headless Hermes.
- **No trace of AI authorship** anywhere: repo, code, comments, commit messages. Commit messages are plain and factual.
- **Phases are independently shippable.** Each phase ends with `selftest.py` green + `node --check` clean + its spec §9.2 manual checklist. A phase may be run as its own subagent-driven-development pass. Later phases consume earlier phases' Interfaces exactly as written below.

---

## File Structure

```
plugin/hermes-workspace/
├── plugin.yaml                 # Task 1  — + kind: standalone, + provides_tools
├── __init__.py                 # Task 1  — NEW, plugin root: def register(ctx)
├── cr_tools.py                 # Tasks 1, 9, 23 — NEW, plugin root: agent tools + prompt section
├── cr_store.py                 # Tasks 1–10, 23, 29, 31, 33 — NEW, plugin root: ALL behaviour, stdlib only
├── desktop/
│   └── plugin.js               # Tasks 13, 17, 19–22, 24, 27–28, 30, 32, 35–37 — the CREATOR block
└── dashboard/
    ├── plugin_api.py           # Task 12 — + import logging + guarded cr_api mount
    ├── cr_api.py               # Tasks 11, 16, 29, 31, 34 — NEW: APIRouter(prefix="/creator")
    ├── selftest.py             # Tasks 12, 16, 29, 31, 34 — + explicit-path cr_store + Creator round-trips
    └── creator-libs/           # Tasks 15, 18, 26 — NEW, committed ~17 MB
        ├── package.json        #   pinned exact versions + node engine
        ├── package-lock.json   #   committed lockfile
        ├── build.mjs           #   pinned esbuild; emits every asset deterministically
        ├── MANIFEST.json       #   { "<specifier>": { "file": "...", "subdeps": [...] } }
        ├── esbuild.wasm
        ├── esbuild.js
        ├── tailwind.js
        ├── codemirror.js       #   Phase 3
        └── <lib>.js …          #   the curated React import set, each zero-import ESM
docs/plans/2026-08-31-creator-module.md
README.md                       # Tasks 14, 25 — install + gate notes
```

---

# PHASE 1 — core artifact loop

Backend is fully buildable and testable (Tasks 1–12) before any renderer code (Task 13). Phase 1 delivers durable versioned artifacts with a pane you can edit and preview in, for types `code | html | svg | markdown | mermaid`.

---

### Task 1: Plugin skeleton — manifest, package init, module stubs

**Files:**
- Modify: `plugin/hermes-workspace/plugin.yaml`
- Create: `plugin/hermes-workspace/__init__.py`
- Create: `plugin/hermes-workspace/cr_tools.py`
- Create: `plugin/hermes-workspace/cr_store.py`
- Test: `cr_store.py` `_selfcheck()` (skeleton), run via `python cr_store.py`

**Interfaces:**
- Produces: `cr_store._selfcheck() -> None`; `cr_tools.register(ctx) -> None`; `register(ctx) -> None` (in `__init__.py`).

- [ ] **Step 1: Write the failing test** — append to `cr_store.py` (create the file with just this + imports):

```python
"""Creator store — all Creator behaviour. stdlib only; no relative imports."""
import base64, hashlib, json, os, re, sqlite3, time
from pathlib import Path


def _selfcheck() -> None:
    # skeleton assertions grow every task
    assert normalize("a\r\nb\n") == "a\nb"


if __name__ == "__main__":
    _selfcheck()
    print("ok")
```

- [ ] **Step 2: Run it, verify it fails** — `python plugin/hermes-workspace/cr_store.py` → `NameError: name 'normalize' is not defined`.

- [ ] **Step 3: Minimal implementation** — add to `cr_store.py`:

```python
def normalize(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
```

- [ ] **Step 4: Run it, verify pass** — `python plugin/hermes-workspace/cr_store.py` → `ok`.

- [ ] **Step 5: `plugin.yaml`** — add two keys (leave the existing four):

```yaml
kind: standalone
provides_tools:
  - create_artifact
  - update_artifact
  - read_artifact
```

- [ ] **Step 6: `__init__.py`** (plugin root):

```python
"""Hermes Workspace plugin — agent-side entry. Knowledge has no agent half yet; Creator registers here."""


def register(ctx) -> None:
    from . import cr_tools
    cr_tools.register(ctx)
```

- [ ] **Step 7: `cr_tools.py`** (plugin root) — stub:

```python
"""Creator agent tools + system-prompt section. Thin: parse args, delegate to cr_store."""
from . import cr_store  # always loaded as a package submodule here


def register(ctx) -> None:
    pass  # Task 9 fills this in
```

- [ ] **Step 8: Commit** — `git add plugin/hermes-workspace/{plugin.yaml,__init__.py,cr_tools.py,cr_store.py} && git commit -m "Add Creator plugin skeleton and manifest keys"`

---

### Task 2: `cr_store` — data dir, connection, schema

**Files:**
- Modify: `plugin/hermes-workspace/cr_store.py`
- Test: `cr_store._selfcheck()`

**Interfaces:**
- Produces: `_creator_dir() -> Path` (resolves fresh every call, mkdir -p); `_connect() -> sqlite3.Connection` (PRAGMAs applied, schema ensured); `_db_path() -> Path`.

- [ ] **Step 1: Write the failing test** — add to `_selfcheck()`:

```python
import tempfile
saved = os.environ.get("HERMES_HOME")
try:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        os.environ["HERMES_HOME"] = os.path.join(d, "home")
        cd = _creator_dir()
        assert cd.is_dir() and cd.name == "creator"
        conn = _connect()
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"artifacts", "versions", "artifact_sessions"} <= names, names
            idx = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
            assert "ix_artsess_session" in idx
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        finally:
            conn.close()
finally:
    if saved is None: os.environ.pop("HERMES_HOME", None)
    else: os.environ["HERMES_HOME"] = saved
```

- [ ] **Step 2: Run, verify fail** — `NameError: _creator_dir`.

- [ ] **Step 3: Implement** — add to `cr_store.py`. `_hermes_home()` mirrors the Knowledge `hw_store` fallback; `_connect()` applies the Global-Constraints PRAGMA set and runs the schema DDL from spec §5.2 verbatim (all `CREATE TABLE`/`CREATE INDEX` with `IF NOT EXISTS`):

```python
def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _creator_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir
        base = Path(plugin_data_dir("hermes-workspace"))
    except Exception:
        base = _hermes_home() / "plugin-data" / "hermes-workspace"
    d = base / "creator"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_path() -> Path:
    return _creator_dir() / "creator-index.db"


_SCHEMA = """<paste the full DDL block from spec §5.2, each statement with IF NOT EXISTS>"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn
```

- [ ] **Step 4: Run, verify pass** — `ok`.

- [ ] **Step 5: Commit** — `"Add Creator store connection and schema"`

---

### Task 3: `cr_store.sanitize_identifier`

**Files:** Modify `cr_store.py`; Test `_selfcheck()`.

**Interfaces:** Produces `sanitize_identifier(raw: str) -> str` (per spec §5.1: lowercase, keep `[a-z0-9._-]`, collapse `-` runs, strip leading/trailing `-.`, reject `..` and `\`, first char alnum, cap 64, empty → `"artifact"`).

- [ ] **Step 1: Failing test** — add to `_selfcheck()`:

```python
assert sanitize_identifier("My Cool Widget!!") == "my-cool-widget"
assert sanitize_identifier("") == "artifact"
assert sanitize_identifier("...") == "artifact"
assert sanitize_identifier("../etc/passwd") == "artifact" or ".." not in sanitize_identifier("../etc/passwd")
assert sanitize_identifier("a" * 200) == "a" * 64
assert sanitize_identifier("---a---b---") == "a-b"
assert not sanitize_identifier("9lives")[0].isdigit()
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `sanitize_identifier` per §5.1 (regex `re.sub(r"[^a-z0-9._-]+", "-", raw.lower())`, then `re.sub(r"-{2,}", "-", …)`, strip, `..` → replace, prefix `a` if first char not alnum, `[:64]`, `or "artifact"`).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `"Add Creator identifier sanitizer"`

---

### Task 4: `cr_store` — normalization, hashing, type→ext

**Files:** Modify `cr_store.py`; Test `_selfcheck()`.

**Interfaces:** Produces `sha256_of(content: str) -> str` (over `normalize(content)`); `TYPE_EXT: dict`; `ext_for(type_: str, language: str | None) -> str`; `LANG_EXT: dict` (`python`→`py`, `javascript`/`js`→`js`, `typescript`/`ts`→`ts`, `jsx`→`jsx`, `tsx`→`tsx`, `bash`/`sh`→`sh`, `json`→`json`, `css`→`css`, `html`→`html`, `sql`→`sql`, `go`→`go`, `rust`/`rs`→`rs`, else `txt`).

- [ ] **Step 1: Failing test:**

```python
assert sha256_of("x\r\n") == sha256_of("x\n") == sha256_of("x")
assert ext_for("markdown", None) == "md"
assert ext_for("mermaid", None) == "mmd"
assert ext_for("code", "python") == "py"
assert ext_for("code", "brainfuck") == "txt"
assert ext_for("react", None) == "jsx"
```

- [ ] **Step 2–4:** implement per §5.5, run.
- [ ] **Step 5: Commit** — `"Add Creator content hashing and extension mapping"`

---

### Task 5: `cr_store.add_version` — the transactional write path

**Files:** Modify `cr_store.py`; Test `_selfcheck()` (concurrency + crash).

**Interfaces:**
- Consumes: `_connect`, `sanitize_identifier`, `sha256_of`, `ext_for`, `normalize`.
- Produces: `add_version(identifier, *, type_, title, language, content, origin, source, session_id, restored_from=None) -> dict` returning `{identifier, dir, version, sha256, action}` where `action` is `"created"` (new artifact, n=1) or `"appended"`. Implements spec §5.3 exactly: resolve `_creator_dir()`, `_connect()`, `BEGIN IMMEDIATE`, allocate `<dir>` with the `dir = ?1 OR dir GLOB ?1 || '-[0-9]*'` query, compute `N = max(n)+1`, write `v<N>.<ext>.tmp` + `fsync(file)` + `os.replace` + `fsync(dir)`, upsert `artifacts` (keep original `type`/`dir` on an existing row; update `title`/`language` when non-empty), insert `versions`, upsert `artifact_sessions` (skip when `session_id` falsy), bump `updated_at`, `COMMIT`. Retry the whole op ≤ 3× / ~100 ms on `sqlite3.OperationalError`; then raise `StoreBusy`. `try/finally: conn.close()`.
- Produces: `class StoreBusy(Exception)`; `latest(identifier) -> dict | None` (`{version, sha256, type, ext, title, language}` from the newest row); `_fsync_dir(path)`.

- [ ] **Step 1: Failing test** — add to `_selfcheck()` (inside the temp-HERMES_HOME block):

```python
r1 = add_version("w", type_="code", title="W", language="python",
                 content="print(1)", origin="tool", source="create", session_id="s1")
assert r1["version"] == 1 and r1["action"] == "created"
r2 = add_version("w", type_="code", title="W2", language="python",
                 content="print(2)", origin="tool", source="update", session_id="s1")
assert r2["version"] == 2
conn = _connect()
try:
    assert conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 2
    assert (_creator_dir() / r1["dir"] / "v2.py").read_text() == "print(2)"
    assert conn.execute("SELECT title FROM artifacts").fetchone()[0] == "W2"
finally:
    conn.close()

# different identifiers colliding on <dir>
a = add_version("Report!", type_="markdown", title="A", language=None,
                content="# a", origin="tool", source="create", session_id="s1")
b = add_version("report", type_="markdown", title="B", language=None,
                content="# b", origin="tool", source="create", session_id="s1")
assert a["dir"] != b["dir"]

# concurrency: two threads, same identifier
import threading
errs = []
def _w(i):
    try:
        add_version("race", type_="code", title="R", language="python",
                    content=f"x={i}", origin="tool", source="update", session_id="s1")
    except Exception as e:  # noqa
        errs.append(e)
ts = [threading.Thread(target=_w, args=(i,)) for i in range(2)]
[t.start() for t in ts]; [t.join() for t in ts]
assert not errs, errs
conn = _connect()
try:
    rows = conn.execute("SELECT n, sha256 FROM versions WHERE identifier='race' ORDER BY n").fetchall()
    assert len(rows) == 2
    for n, sha in rows:
        disk = (_creator_dir() / "race" / f"v{n}.py").read_text()
        assert sha256_of(disk) == sha, (n, disk)
finally:
    conn.close()
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `add_version` + helpers per §5.3. For the crash-safety guarantee: the file write + `os.replace` happen **before** the row inserts, both inside the open transaction, so a raise before `COMMIT` rolls back the rows and the orphan file is ignored by all readers (they go through the index).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `"Add Creator transactional version-write path"`

---

### Task 6: `cr_store` — create / update / read (tool-facing)

**Files:** Modify `cr_store.py`; Test `_selfcheck()`.

**Interfaces:**
- Consumes: `add_version`, `latest`, `normalize`, `sha256_of`.
- Produces:
  - `VALID_TYPES = ("code", "html", "svg", "markdown", "mermaid")` (Task 23 adds `"react"`).
  - `TRUNCATION_NOTE = "… (full content in the artifact)"` and `OVERSIZE_NOTE = "open the Creator pane for the full artifact"`.
  - `do_create(args: dict, session_id: str) -> dict` — validates `identifier/type/title/content` present, `type in VALID_TYPES`; new id → `add_version(..., source="create")`; existing id → `add_version(..., source="update")` keeping original type. Result `{identifier, version, type, title, action: "created"|"updated", note}` (no content).
  - `do_update(args: dict, session_id: str) -> dict` — id must exist (`StoreNotFound` else); reject if `OVERSIZE_NOTE` in content (`StoreBadInput`); `normalize`-equal to `latest().sha256` → `{action:"unchanged", version}` (still record session via a bare `add_version`-less session upsert — add `record_session(identifier, session_id)`); else `add_version(..., source="update")` + `{action:"updated", version, diff, content}` with `diff` ≤ 4 KB (`difflib.unified_diff`) and `content` ≤ 10 KB (truncate + `TRUNCATION_NOTE`).
  - `do_read(args: dict) -> dict` — `{identifier, version, type, title, version_count, updated_at, content}`; `content` full unless > 256 KB → head + `{truncated:true, note: OVERSIZE_NOTE}`.
  - `record_session(identifier, session_id) -> None`; `class StoreNotFound(Exception)`, `class StoreBadInput(Exception)`.

- [ ] **Step 1: Failing test:**

```python
c = do_create({"identifier": "doc", "type": "markdown", "title": "Doc", "content": "# hi"}, "s1")
assert c["action"] == "created" and c["version"] == 1 and "content" not in c
c2 = do_create({"identifier": "doc", "type": "code", "title": "Doc2", "content": "# hi\n\nmore"}, "s1")
assert c2["action"] == "updated" and c2["type"] == "markdown"  # original type kept
u_same = do_update({"identifier": "doc", "content": "# hi\n\nmore\n"}, "s1")  # only trailing \n differs
assert u_same["action"] == "unchanged"
u = do_update({"identifier": "doc", "content": "# hi\n\nchanged"}, "s2")
assert u["action"] == "updated" and "changed" in u["content"] and "@@" in u["diff"]
try:
    do_update({"identifier": "nope", "content": "x"}, "s1"); assert False
except StoreNotFound: pass
try:
    do_update({"identifier": "doc", "content": "x " + OVERSIZE_NOTE}, "s1"); assert False
except StoreBadInput: pass
r = do_read({"identifier": "doc"})
assert r["version"] == 3 and r["content"] == "# hi\n\nchanged" and r["version_count"] == 3
# session recorded even on unchanged
conn = _connect()
try:
    assert conn.execute(
        "SELECT COUNT(*) FROM artifact_sessions WHERE identifier='doc'").fetchone()[0] == 2
finally:
    conn.close()
```

- [ ] **Step 2–4:** implement per §5.6–§5.7, run.
- [ ] **Step 5: Commit** — `"Add Creator create/update/read dispatch"`

---

### Task 7: `cr_store` — versions, restore, list, get, delete

**Files:** Modify `cr_store.py`; Test `_selfcheck()`.

**Interfaces:**
- Produces:
  - `get_version(identifier, n) -> dict` → `{identifier, n, type, content}`; `StoreNotFound` if no row, `class StoreGone(Exception)` if the row exists but the file is missing.
  - `restore(identifier, n, session_id) -> dict` — read `v<n>` (→ `StoreGone` on missing file); if `normalize`-equal to latest → `{action:"unchanged", version}`; else `add_version(..., source="restore", restored_from=n)` → `{action:"restored", version}`.
  - `list_artifacts(session_id: str | None) -> list[dict]` — every artifact: `{identifier, type, title, version: <latest n>, updated_at, origin, in_session}` (`in_session` via the `ix_artsess_session` index), this-session first then by `updated_at` desc.
  - `get_artifact(identifier) -> dict` — `{identifier, type, language, title, version_count, updated_at, versions: [{n, source, restored_from, created_at, bytes}]}`.
  - `delete_artifact(identifier) -> None` — `DELETE FROM artifacts` (cascades) then `shutil.rmtree(<dir>)`; assert the resolved dir is under `_creator_dir()`.

- [ ] **Step 1: Failing test** (after Task 6 state — `doc` at v3):

```python
add_version("doc", type_="markdown", title="", language=None, content="v4 body",
            origin="tool", source="update", session_id="s1")
rr = restore("doc", 1, "s1")
assert rr["action"] == "restored" and rr["version"] == 5
assert get_version("doc", 5)["content"] == "# hi"
assert restore("doc", 5, "s1")["action"] == "unchanged"
lst = list_artifacts("s1")
assert any(a["identifier"] == "doc" and a["in_session"] for a in lst)
ga = get_artifact("doc")
assert ga["version_count"] == 5 and ga["versions"][-1]["source"] == "restore" \
    and ga["versions"][-1]["restored_from"] == 1
# missing-file -> StoreGone
(_creator_dir() / ga["identifier"] if False else None)
import os as _os
_os.remove(_creator_dir() / list_artifacts(None)[0]["identifier"] / "v1.md") if False else None
delete_artifact("doc")
try:
    get_artifact("doc"); assert False
except StoreNotFound: pass
assert not (_creator_dir() / "doc").exists()
```

- [ ] **Step 2–4:** implement per §5.10, run. (Add a focused `StoreGone` test: write an artifact, `os.remove` its `v1` file, assert `get_version` raises `StoreGone`.)
- [ ] **Step 5: Commit** — `"Add Creator version read, restore, list, delete"`

---

### Task 8: `cr_store` — config

**Files:** Modify `cr_store.py`; Test `_selfcheck()`.

**Interfaces:**
- Produces: `get_config() -> dict` → `{"project_root": str|None, "github_token_set": bool}`; `set_config(patch: dict) -> dict` — writes `_creator_dir()/config.json` atomically (`tmp` + `os.replace`), accepts `project_root` (validated: absolute, exists, is a dir, or `None` to clear) and `github_token` (stored, never returned). Returns `get_config()`.

- [ ] **Step 1: Failing test:**

```python
assert get_config() == {"project_root": None, "github_token_set": False}
set_config({"github_token": "ghp_x"})
assert get_config()["github_token_set"] is True
assert "ghp_x" not in json.dumps(get_config())
import tempfile as _t
with _t.TemporaryDirectory() as pr:
    set_config({"project_root": pr})
    assert get_config()["project_root"] == pr
set_config({"project_root": None})
assert get_config()["project_root"] is None
```

- [ ] **Step 2–4:** implement, run.
- [ ] **Step 5: Commit** — `"Add Creator config store"`

---

### Task 9: `cr_tools` — register the 3 agent tools + system-prompt section

**Files:**
- Modify: `plugin/hermes-workspace/cr_tools.py`
- Test: `cr_tools.py` `_selfcheck()` (a fake ctx), added to selftest MODULES-style call in Task 12.

**Interfaces:**
- Consumes: `cr_store.do_create/do_update/do_read`, `cr_store.StoreNotFound/StoreBadInput/StoreBusy`.
- Produces: `SCHEMAS: dict[str, dict]` (JSON-schema for each tool per §5.7); `PROMPT_SECTION: str` (spec §5.8 verbatim — **no leading `##`**, asserted `< 3900` chars); `register(ctx)` calling `ctx.register_tool("create_artifact", "creator", SCHEMAS["create_artifact"], _h_create, ...)` ×3 and `ctx.register_system_prompt_section("creator-artifacts", PROMPT_SECTION)`; handlers `_h_create/_h_update/_h_read(args, **kwargs)` that call `cr_store` with `kwargs.get("session_id", "")` and return `tools.registry.tool_result(json.dumps(result))` / `tool_error(str(e))` on the three store exceptions. Handlers never raise.

- [ ] **Step 1: Failing test** — add `_selfcheck()` to `cr_tools.py`:

```python
def _selfcheck() -> None:
    import tempfile
    assert len(PROMPT_SECTION) < 3900 and not PROMPT_SECTION.lstrip().startswith("#")
    saved = os.environ.get("HERMES_HOME")
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            out = _h_create({"identifier": "t", "type": "code", "title": "T",
                             "content": "x=1"}, session_id="s1")
            assert "created" in out
            bad = _h_update({"identifier": "missing", "content": "y"}, session_id="s1")
            assert "no artifact" in bad.lower()
    finally:
        if saved is None: os.environ.pop("HERMES_HOME", None)
        else: os.environ["HERMES_HOME"] = saved
```

(`import os` at top of `cr_tools.py`; `tool_result`/`tool_error` — import `from tools.registry import tool_result, tool_error` inside `register`/handlers guarded, or define thin local fallbacks for the selftest: `def _result(s): return s` when the import fails.)

- [ ] **Step 2: Run** — `python -c "import sys; sys.path.insert(0,'plugin/hermes-workspace'); import cr_tools; cr_tools._selfcheck()"` → fails (`_h_create` undefined). Note: for the selftest, `cr_tools.py`'s `from . import cr_store` must degrade to a path import when run outside the package — guard it:

```python
try:
    from . import cr_store
except ImportError:
    import cr_store  # selftest / explicit-path context
```

- [ ] **Step 3: Implement** `SCHEMAS`, `PROMPT_SECTION` (paste §5.8), `_h_*`, `register`.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `"Register Creator agent tools and prompt section"`

---

### Task 10: `cr_store` — transcript scan

**Files:** Modify `cr_store.py`; Test `_selfcheck()`.

**Interfaces:**
- Produces:
  - `_read_assistant_messages(session_id: str) -> list[str]` — lazy `from hermes_state import SessionDB`; `db.get_messages(session_id, include_compacted=True)`; keep `role == "assistant"`; flatten `content` (if `list`, `" ".join(b.get("text","") for b in content if isinstance(b, dict))`; if `str`, as-is). **Monkeypatchable** — tests replace it.
  - `_fenced_blocks(text: str) -> list[tuple[str, str]]` — `(lang, body)` from ```` ```lang\n…\n``` ````.
  - `NON_ARTIFACT_LANGS: frozenset` (spec §5.9, 16 entries) — used only for the "any other lang" row.
  - `SCAN_ROWS` — the ordered classifier from §5.9 (svg → html → markdown → code); `_classify(lang, body) -> tuple[str, str] | None` returning `(type_, slug)`.
  - `_scan_identifier(slug, content) -> str` — `sanitize_identifier(slug)[:55].rstrip("-") + "-" + sha256_of(content)[:8]`.
  - `scan(session_id: str) -> dict` — `{found, skipped}`; debounce via a module dict `_scan_seen: dict[str, float]` (~10 s); for each block: `_classify` → skip `None`; `sha = sha256_of(body)`; if `sha` matches any `versions.sha256` → `skipped += 1`, `record_session(<owner>, session_id)`; else `add_version(_scan_identifier(...), type_, title=slug, language=(lang if type_=="code" else None), content=body, origin="scan", source="scan", session_id=session_id)` → `found += 1`.

- [ ] **Step 1: Failing test:**

```python
BLOCKS = {
  "html_doc": "```html\n<!doctype html><html><body>" + "x" * 200 + "</body></html>\n```",
  "svg_in_html": "```html\n<svg width='9'>" + "p" * 2100 + "</svg>\n```",
  "mermaid": "```mermaid\ngraph TD; A-->B\n```",
  "js_short": "```js\nconst a = 1\n```",
  "js_long": "```js\n" + "\n".join(f"const x{i} = {i}" for i in range(60)) + "\n```",
  "diff": "```diff\n- a\n+ b\n```",
  "md_small": "```md\n" + "word " * 20 + "\n```",
  "md_big": "```markdown\n" + "word " * 200 + "\n```",
}
_orig = _read_assistant_messages
globals()["_read_assistant_messages"] = lambda sid: ["".join(BLOCKS.values())]
try:
    r = scan("sess-x")
    ids = [a["identifier"] for a in list_artifacts("sess-x")]
    types = {a["identifier"]: a["type"] for a in list_artifacts("sess-x")}
    assert any(t == "svg" for t in types.values())        # precedence: svg wins in an html fence
    assert sum(t == "html" for t in types.values()) == 1
    assert any(t == "mermaid" for t in types.values())
    assert any(t == "code" for t in types.values())       # js_long
    assert not any(t == "code" and "1" in i for i in ids)  # js_short skipped (not enough lines)
    assert any(t == "markdown" for t in types.values())   # md_big
    assert sum(t == "markdown" for t in types.values()) == 1  # md_small skipped
    r2 = scan("sess-x2")
    assert r2["found"] == 0 and r2["skipped"] >= 4  # dedup by hash, links the new session
    assert any(a["in_session"] for a in list_artifacts("sess-x2"))
    long_slug = _scan_identifier("a" * 90, "body")
    assert len(long_slug) <= 64 and long_slug.endswith(sha256_of("body")[:8])
finally:
    globals()["_read_assistant_messages"] = _orig
```

- [ ] **Step 2–4:** implement per §5.9, run.
- [ ] **Step 5: Commit** — `"Add Creator transcript scan"`

---

### Task 11: `dashboard/cr_api.py` — router + Phase 1 endpoints

**Files:**
- Create: `plugin/hermes-workspace/dashboard/cr_api.py`
- Test: added in Task 12's `selftest.py` round-trip.

**Interfaces:**
- Produces: `router: APIRouter` (`prefix="/creator"`), `cr_store` module handle (explicit-path load). Endpoints exactly as spec §5.10 Phase-1 subset:
  `GET /artifacts`, `GET /artifacts/{id}`, `GET /artifacts/{id}/v/{n}`, `POST /artifacts/{id}/versions` (`{content}` → `user-edit`; `{restore_from}` → `restore`), `POST /scan`, `DELETE /artifacts/{id}`, `GET /config`, `POST /config`. Store exceptions map: `StoreNotFound` → 404, `StoreGone` → 410, `StoreBadInput` → 400, `StoreBusy` → 503, oversize content → 400 `{error:"too_large"}`.

- [ ] **Step 1: Write `cr_api.py`** (this task's deliverable is the file; the test is Task 12). Structure:

```python
"""Creator HTTP surface. stdlib + FastAPI only at module scope."""
import importlib.util
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_p = Path(__file__).resolve().parent.parent / "cr_store.py"
_s = importlib.util.spec_from_file_location("hw_cr_store", _p)
cr_store = importlib.util.module_from_spec(_s)
_s.loader.exec_module(cr_store)

router = APIRouter(prefix="/creator")

MAX_BYTES = 1_000_000


class VersionBody(BaseModel):
    content: str | None = None
    restore_from: int | None = None

class ScanBody(BaseModel):
    session_id: str

class ConfigBody(BaseModel):
    project_root: str | None = None
    github_token: str | None = None


def _guard(fn):
    # wraps a call, translating cr_store exceptions to HTTPException
    ...
```

Implement each route. `POST /artifacts/{id}/versions`: exactly one of `content`/`restore_from` (400 else); `content` over `MAX_BYTES` → 400 `{error:"too_large"}`.

- [ ] **Step 2: Run** — `python -c "import sys;sys.path.insert(0,'plugin/hermes-workspace/dashboard');import cr_api;print(len(cr_api.router.routes))"` → prints a route count ≥ 8.
- [ ] **Step 3: Commit** — `"Add Creator HTTP router and Phase 1 endpoints"`

---

### Task 12: Dashboard wiring — `plugin_api.py` + `selftest.py`

**Files:**
- Modify: `plugin/hermes-workspace/dashboard/plugin_api.py`
- Modify: `plugin/hermes-workspace/dashboard/selftest.py`
- Test: `python dashboard/selftest.py` green, including the defensive-mount case.

**Interfaces:**
- Produces: `selftest._selfcheck_cr_store()` (explicit-path load + `cr_store._selfcheck()`), `selftest._selfcheck_creator_http()` (a full round-trip), both called from `__main__`.

- [ ] **Step 1: Failing test** — add to `selftest.py`:

```python
def _selfcheck_cr_store() -> None:
    import importlib.util
    p = os.path.join(os.path.dirname(__file__), "..", "cr_store.py")
    s = importlib.util.spec_from_file_location("cr_store_probe", p)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    m._selfcheck()
    print("  cr_store._selfcheck ok")


def _selfcheck_creator_http() -> None:
    import tempfile
    saved = os.environ.get("HERMES_HOME")
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            import cr_api
            cr_api.cr_store._scan_seen.clear()
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            app = FastAPI(); app.include_router(cr_api.router)
            c = TestClient(app)
            r = c.post("/creator/artifacts/my-doc/versions", json={"content": "# v1"})
            assert r.status_code == 404  # create is a tool path, not HTTP; see note
            # seed via cr_store directly, then exercise HTTP
            cr_api.cr_store.do_create({"identifier": "my-doc", "type": "markdown",
                                       "title": "Doc", "content": "# v1"}, "sess-1")
            lst = c.get("/creator/artifacts?session_id=sess-1").json()["artifacts"]
            assert lst and lst[0]["identifier"] == "my-doc" and lst[0]["in_session"]
            assert c.get("/creator/artifacts/my-doc/v/1").json()["content"] == "# v1"
            v2 = c.post("/creator/artifacts/my-doc/versions", json={"content": "# v2"}).json()
            assert v2["version"] == 2
            g = c.get("/creator/artifacts/my-doc").json()
            assert g["versions"][1]["source"] == "user-edit"
            rest = c.post("/creator/artifacts/my-doc/versions", json={"restore_from": 1}).json()
            assert rest["action"] == "restored"
            assert c.get("/creator/artifacts/my-doc/v/3").json()["content"] == "# v1"
            assert c.post("/creator/artifacts/my-doc/versions",
                          json={"content": "x" * 1_000_001}).status_code == 400
            assert c.delete("/creator/artifacts/my-doc").json()["ok"]
            assert c.get("/creator/artifacts/my-doc").status_code == 404
            # config
            assert c.post("/creator/config", json={"github_token": "t"}).json()["github_token_set"]
            # defensive mount: a broken cr_api import must not unmount hw_* routes
            import plugin_api, importlib
            assert any(r.path == "/status" for r in plugin_api.router.routes)
    finally:
        if saved is None: os.environ.pop("HERMES_HOME", None)
        else: os.environ["HERMES_HOME"] = saved
    print("  creator http round-trip ok")
```

(Drop the `r.status_code == 404` line if create-via-HTTP is not wanted — Phase 1 has no `POST /artifacts` create endpoint; artifacts are born from the tool or the scan. Keep the comment.)

- [ ] **Step 2: Run** — `python dashboard/selftest.py` fails at the new functions.
- [ ] **Step 3: Implement** — (a) `plugin_api.py`: add `import logging` near the top; after the `hw_*` imports add the guarded block from Global Constraints. (b) `selftest.py`: add the two functions; call them in `__main__` after the existing checks; add a defensive-mount sub-check that monkeypatches `builtins.__import__` to raise on `"cr_api"`, re-execs `plugin_api` via `importlib.reload`, and asserts `/status` still present + a warning logged.
- [ ] **Step 4: Run** — `python dashboard/selftest.py` → `ok`.
- [ ] **Step 5: Commit** — `"Wire Creator API into the dashboard mount and selftest"`

---

### Task 13: `plugin.js` — `crRegister` + the Creator pane (Phase 1 UI)

**Files:**
- Modify: `plugin/hermes-workspace/desktop/plugin.js`
- Test: `node --check`; manual checklist (spec §9.2 Phase 1).

**Interfaces:**
- Consumes: `@hermes/plugin-sdk` (`PANES_AREA`, `PALETTE_AREA`, `STATUSBAR_AREAS`, `Button`, `CopyButton`, `ConfirmDialog`, `Streamdown`, `EmptyState`, `atom`, `useValue`, `host`), `react`, `react/jsx-runtime`.
- Produces (all `cr`-prefixed, module scope): `crCtx`, `crApi(path, opts)`, `crQs(obj)`, atoms `crOpen$` (identifier|null), `crList$`, `crViewVersion$` (n|null = latest), `crDirty$`, `crBusy$`; components `CreatorPane`, `CrHeader`, `CrEditor`, `CrPreview`; `crRegister(ctx)`; `crPoll()` (2 s interval while pane mounted, `session_id = host.state.focusedStoredSessionId`); `crScan()` (POST `/creator/scan`).

- [ ] **Step 1: Add the CREATOR block** to `plugin.js` (after the Knowledge components, before `export default`). Guard the shape against `node --check`. Pane registration mirrors Knowledge's `pane` entry:

```js
// ===== CREATOR =====
let crCtx = null
const crApi = (p, o) => crCtx.rest(p, o)
const crQs = (obj) => Object.entries(obj).filter(([, v]) => v != null && v !== '')
  .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')

const crOpen$ = atom(null)
const crList$ = atom([])
const crViewVersion$ = atom(null)
const crDirty$ = atom(false)

// … CreatorPane: <select> picker + version stepper + Copy + Delete(ConfirmDialog)
//   + CrEditor (Textarea + Save, ⌘S) + CrPreview (per-type table, spec §5.11)

function crRegister(ctx) {
  crCtx = ctx
  ctx.registerMany([
    { id: 'cr-pane', area: PANES_AREA, title: 'Creator',
      data: { placement: 'right', width: '440px', hideOnly: true, collapsible: true,
              dock: { pane: 'workspace', pos: 'right' } },
      render: () => jsx(CrErrorBoundary, { children: jsx(CreatorPane, {}) }) },
    { id: 'cr-status', area: STATUSBAR_AREAS.right, order: 71,
      render: () => jsx(CrStatusItem, {}) },
    { id: 'cr-palette-open', area: PALETTE_AREA,
      data: { id: 'hermes-workspace.open-creator', label: 'Open Creator',
              keywords: ['creator', 'artifact', 'preview', 'code'],
              run: () => host.panes?.reveal?.('hermes-workspace.cr-pane') } },
    { id: 'cr-palette-scan', area: PALETTE_AREA,
      data: { id: 'hermes-workspace.creator-rescan', label: 'Creator: rescan this chat',
              keywords: ['creator', 'scan', 'artifact', 'rescan'],
              run: () => crScan().then(() => host.notify({ kind: 'info', message: 'Rescanned' }))
                .catch((e) => host.notifyError(e, 'Rescan failed')) } },
  ])
}
```

- [ ] **Step 2: Wire the call** — in Knowledge's `register(ctx)`, after the `palette-reindex` `ctx.register({...})` block and before `rpcAvailable().then(`, add:

```js
try { crRegister(ctx) } catch (e) { host.notifyError?.(e, 'Creator failed to load') }
```

- [ ] **Step 3: `CrErrorBoundary`** — a minimal React class component (Creator's own inner boundary, spec §3.6) rendering a one-line fallback.
- [ ] **Step 4: Run** — `node --check plugin/hermes-workspace/desktop/plugin.js` → clean.
- [ ] **Step 5: Manual** — copy the plugin to `~/.hermes/plugins/`, restart Hermes, run the spec §9.2 Phase-1 checklist (create an artifact from a chat via a tool call; the pane shows it; edit + Save → v2; step + restore; each of the 5 types renders; picker works; kill the dashboard and confirm the pane retries).
- [ ] **Step 6: Commit** — `"Add the Creator pane and renderer registration"`

---

### Task 14: Phase 1 integration + install docs

**Files:**
- Modify: `README.md`
- Test: full `python dashboard/selftest.py` + `node --check` + spec §9.2 Phase-1 checklist.

- [ ] **Step 1** — `README.md`: add a "Creator" section and correct the install steps for **both** modules per spec §12: (1) copy `plugin/hermes-workspace/` to `~/.hermes/plugins/hermes-workspace/`; (2) `hermes plugins enable hermes-workspace` (adds `plugins.enabled`; enables the agent + dashboard halves of Knowledge **and** Creator); (3) restart Hermes Desktop — the renderer half auto-loads on discovery, no Settings toggle needed (it can be *disabled* in Settings → Plugins); (4) tools + "Open Creator" appear.
- [ ] **Step 2** — run `python dashboard/selftest.py` → `ok`; `node --check desktop/plugin.js` → clean.
- [ ] **Step 3** — walk the full spec §9.2 Phase-1 manual checklist once more end to end.
- [ ] **Step 4: Commit** — `"Document Creator install and the plugin enable/gate split"`

---

# PHASE 2 — React/JSX runtime

Opens with a **proof-of-life spike** (Tasks 15–17): prove the JSON-envelope transport + `WebAssembly.compile` + `esbuild.build` + one iife bundle rendering in the srcdoc iframe, in the real renderer, before the rest builds on it. Delivers `type: react` running live with per-artifact Tailwind, render-error surfacing, and a plain console pane.

---

### Task 15: `creator-libs/` build harness — esbuild only (spike scope)

**Files:**
- Create: `plugin/hermes-workspace/dashboard/creator-libs/package.json`
- Create: `plugin/hermes-workspace/dashboard/creator-libs/package-lock.json` (committed)
- Create: `plugin/hermes-workspace/dashboard/creator-libs/build.mjs`
- Create (build output, committed): `esbuild.wasm`, `esbuild.js`, `MANIFEST.json`
- Test: a `dashboard/creator-libs/verify.mjs` that `node --check`s each `.js` output and checks the wasm magic bytes; run in Step 4.

**Interfaces:**
- Produces: `MANIFEST.json` shape `{ "<specifier>": { "file": "<name>.js", "subdeps": ["<specifier>", …] } }`; every `.js` asset is a **zero-import** ESM (no bare or relative `import`/`export … from`).
- `build.mjs` — pinned `esbuild` (exact version from the lockfile); for each entry in a `LIBS` array it runs `esbuild.build({ entryPoints, bundle: true, format: 'esm', platform: 'browser', minify: true, outfile, external: [] })`, then post-checks the output has no residual import statements; copies `esbuild-wasm/esbuild.wasm` and `esbuild-wasm/lib/browser.min.js` → `esbuild.js`; writes `MANIFEST.json`.

- [ ] **Step 1: Failing test** — write `verify.mjs`:

```js
import { readFileSync } from 'node:fs'
import { execSync } from 'node:child_process'
const man = JSON.parse(readFileSync(new URL('./MANIFEST.json', import.meta.url)))
for (const { file } of Object.values(man)) {
  execSync(`node --check "${new URL(file, import.meta.url).pathname}"`)
  const src = readFileSync(new URL(file, import.meta.url), 'utf8')
  if (/^\s*import\s.+\sfrom\s/m.test(src) || /^\s*export\s.+\sfrom\s/m.test(src))
    throw new Error(`${file} has a bare import`)
}
const wasm = readFileSync(new URL('./esbuild.wasm', import.meta.url))
if (wasm[0] !== 0x00 || wasm[1] !== 0x61) throw new Error('bad wasm magic')
console.log('verify ok')
```

- [ ] **Step 2: Run** — `node dashboard/creator-libs/verify.mjs` → fails (no MANIFEST).
- [ ] **Step 3: Implement** `package.json` (deps: `esbuild-wasm` + `esbuild` pinned), `npm install` to generate the lockfile, `build.mjs` with `LIBS = []` for now (just the esbuild copy + an empty MANIFEST). Run `node dashboard/creator-libs/build.mjs`.
- [ ] **Step 4: Run** — `node dashboard/creator-libs/verify.mjs` → `verify ok`.
- [ ] **Step 5: `.gitignore`** — ensure `node_modules/` under `creator-libs/` is ignored; the `.wasm`/`.js` outputs and the lockfile are **committed**.
- [ ] **Step 6: Commit** — `"Add the creator-libs build harness and vendor esbuild-wasm"`

---

### Task 16: `cr_api` — the JSON asset envelope route

**Files:**
- Modify: `plugin/hermes-workspace/dashboard/cr_api.py`
- Modify: `plugin/hermes-workspace/dashboard/selftest.py`
- Test: `selftest.py` — envelope shape + decode round-trip.

**Interfaces:**
- Produces: `GET /creator/asset/{name}` → `{name, encoding: "utf8"|"base64", data, sha256}`. `name` matched against `^[a-zA-Z0-9._-]+$` and a suffix allowlist `{.js, .json, .wasm}`; resolved under `creator-libs/`, path-guarded; `.wasm` → base64, else utf8; `sha256` over the raw bytes; 404 if missing.

- [ ] **Step 1: Failing test** — add to `_selfcheck_creator_http()`:

```python
env = c.get("/creator/asset/MANIFEST.json").json()
assert env["encoding"] == "utf8" and json.loads(env["data"]) is not None
w = c.get("/creator/asset/esbuild.wasm").json()
import base64 as _b
assert w["encoding"] == "base64" and _b.b64decode(w["data"])[:2] == b"\x00a"
assert c.get("/creator/asset/../plugin_api.py").status_code == 400
assert c.get("/creator/asset/nope.js").status_code == 404
```

- [ ] **Step 2–4:** implement the route, run `python dashboard/selftest.py` → `ok`.
- [ ] **Step 5: Commit** — `"Serve creator-libs assets as JSON envelopes"`

---

### Task 17: `plugin.js` — asset fetch/decode + esbuild init + the spike smoke test

**Files:** Modify `plugin.js`; Test `node --check` + **manual proof-of-life**.

**Interfaces:**
- Produces: `crAsset(name) -> Promise<Uint8Array|string>` (fetch the envelope with `timeoutMs: 120000`, decode by `encoding`, verify `sha256`, cache in a `Map`); `crEsbuild() -> Promise<esbuild>` (once: `crAsset('esbuild.js')` → blob-import → `esbuild.initialize({ wasmModule: await WebAssembly.compile(await crAsset('esbuild.wasm')), worker: true })`, falling back to `worker: false` on failure); a dev palette command `hermes-workspace.creator-esbuild-smoke`.

- [ ] **Step 1: Implement** `crAsset`, `crEsbuild`, and the smoke command:

```js
{ id: 'cr-palette-smoke', area: PALETTE_AREA,
  data: { id: 'hermes-workspace.creator-esbuild-smoke', label: 'Creator: esbuild smoke test',
    run: async () => {
      const es = await crEsbuild()
      const r = await es.build({ stdin: { contents: 'export default () => 42', loader: 'js' },
        bundle: true, format: 'iife', globalName: '__Smoke', write: false })
      host.notify({ kind: 'info', message: `bundle ${r.outputFiles[0].text.length} bytes` })
    } } }
```

- [ ] **Step 2: Run** — `node --check` → clean.
- [ ] **Step 3: Manual proof-of-life gate** — in the real renderer, run "Creator: esbuild smoke test" → a toast with a byte count. Check the dev console: no CSP error, the blob worker started (or the `worker:false` path logged). **If this fails, stop and reassess Phase 2 before continuing.**
- [ ] **Step 4: Commit** — `"Add the Creator asset loader and esbuild-wasm bootstrap"`

---

### Task 18: `build.mjs` — the full vendored import set

**Files:**
- Modify: `creator-libs/package.json`, `package-lock.json`, `build.mjs`, `MANIFEST.json`
- Create (committed outputs): one `.js` per specifier
- Test: `verify.mjs` extended — every `MANIFEST` specifier resolves, shared deps appear once.

**Interfaces:**
- Produces: `MANIFEST.json` covering the spec §6.1 list: `react`, `react-dom`, `react-dom/client`, `react/jsx-runtime`, `recharts`, `lucide-react`, `d3`, `three`, `@react-three/fiber`, `@react-three/drei`, `papaparse`, `xlsx`, `mathjs`, `tone`, `@tanstack/react-table`, `lodash`, `date-fns`, `framer-motion`, `clsx`, `tailwind-merge`, `class-variance-authority`, and the shadcn/ui + Radix set. Each built with `external: ['react', 'react-dom', 'react/jsx-runtime']` so React is shared (the vfs resolver in Task 19 supplies it once); `subdeps` in `MANIFEST` lists those externals per lib.

- [ ] **Step 1: Failing test** — extend `verify.mjs`:

```js
const need = ['react','react-dom','recharts','lucide-react','d3','three','@react-three/fiber',
  'papaparse','xlsx','mathjs','tone','@tanstack/react-table','lodash','date-fns',
  'framer-motion','clsx','tailwind-merge','class-variance-authority']
for (const s of need) if (!man[s]) throw new Error(`MANIFEST missing ${s}`)
```

- [ ] **Step 2: Run** — fails.
- [ ] **Step 3: Implement** — add every package to `package.json` at an exact version, `npm install`, extend `LIBS` in `build.mjs`, run it. Total output ~15–16 MB.
- [ ] **Step 4: Run** — `node dashboard/creator-libs/verify.mjs` → `verify ok`.
- [ ] **Step 5: Commit** — `"Vendor the full Creator React import set"`

---

### Task 19: `plugin.js` — the vfs resolver + `crBundle`

**Files:** Modify `plugin.js`; Test `node --check` + manual.

**Interfaces:**
- Consumes: `crEsbuild`, `crAsset`, the `MANIFEST` (fetched once via `crAsset('MANIFEST.json')`).
- Produces: `crBundle(source: string) -> Promise<{ok: true, code: string} | {ok: false, errors: string}>` — parse bare specifiers, fetch each lib + its `subdeps` (cache by name), build an in-memory vfs esbuild plugin (`onResolve`/`onLoad` serving the fetched text, breaking cycles by marking in-flight specifiers), then `esbuild.build` with **exactly** the config in spec §6.2 step 3 (`format: 'iife'`, `globalName: '__CreatorArtifact'`, `loader: 'tsx'`, `jsx: 'automatic'`, `jsxImportSource: 'react'`, `write: false`); cache the result by `sha256(source)`.

- [ ] **Step 1: Implement** `crBundle` + the resolver.
- [ ] **Step 2: Run** — `node --check` → clean.
- [ ] **Step 3: Manual** — a temporary palette command that calls `crBundle` on a fixture (`import { LineChart } from 'recharts'; export default () => <LineChart/>`) and logs `ok` + length; then on a syntax-error fixture and logs the diagnostics. Remove the temp command before commit (or keep it dev-gated).
- [ ] **Step 4: Commit** — `"Add the Creator esbuild bundler and vfs resolver"`

---

### Task 20: `plugin.js` — renderer-side Tailwind compile

**Files:**
- Modify: `creator-libs/build.mjs`, `MANIFEST.json`, add `tailwind.js` (committed)
- Modify: `plugin.js`
- Test: `verify.mjs` (`tailwind.js` present, zero-import) + manual.

**Interfaces:**
- Produces: `tailwind.js` — a zero-import ESM exposing `compile(baseCss: string, opts: { candidates: string[] }) -> string` (built from `@tailwindcss/browser` or the `tailwindcss` v4 API, whichever exposes candidate-driven compilation offline).
- `crTailwind(bundleText: string, themeBlock: string | null) -> Promise<string>` — load `tailwind.js`, extract class candidates from `bundleText` (the compiler's own extractor, or a `class(?:Name)?\s*[:=]\s*["'\`]([^"'\`]+)` sweep), `compile('@tailwind utilities;' + (themeBlock||''), { candidates })`, cache by `sha256(candidates.join(' ') + (themeBlock||''))`.

- [ ] **Step 1: Failing test** (verify.mjs): assert `man['tailwind']` (or a known key) resolves and `tailwind.js` `node --check`s.
- [ ] **Step 2–4:** add to `build.mjs`, run; implement `crTailwind`; `node --check`.
- [ ] **Step 5: Manual** — temp command: `crTailwind("<div className='grid grid-cols-[1fr_2fr] p-4'>", "@theme { --color-brand: #0af; }")` → CSS containing `grid-template-columns` and the custom prop.
- [ ] **Step 6: Commit** — `"Add renderer-side Tailwind compilation for Creator previews"`

---

### Task 21: `plugin.js` — the React preview iframe + bootstrap

**Files:** Modify `plugin.js`; Test `node --check` + manual.

**Interfaces:**
- Produces: `crThemePrelude() -> string` (copied from `inline-preview-directive.tsx`; resolves the tokens named in spec §3.6: `--ui-text-primary`, `--ui-text-tertiary`, `--ui-accent`, `--ui-stroke-tertiary`, `--ui-bg-editor`); `crReactSrcdoc({bundle, css, nonce, injectRuntime}) -> string` building the srcdoc per spec §6.3 (`<style>` = `css`; `<div id="root">`; classic `<script>` with the iife `bundle`; a bootstrap `<script>` doing `ReactDOM.createRoot(root).render(React.createElement(ErrorBoundary,null,React.createElement(window.__CreatorArtifact.default)))` in try/catch); the bridge script placeholder (Task 22 fills it)); `CrReactFrame` React component (an `<iframe sandbox="allow-scripts" srcDoc={…}>`, opaque origin, re-keyed on bundle hash).

- [ ] **Step 1: Implement** `crThemePrelude`, `crReactSrcdoc`, `CrReactFrame`.
- [ ] **Step 2: Run** — `node --check` → clean.
- [ ] **Step 3: Manual** — wire a fixture `type=react` artifact through `crBundle` → `crTailwind` → `crReactSrcdoc` → `CrReactFrame` (temporary in the pane); confirm a `recharts` chart renders with Tailwind spacing.
- [ ] **Step 4: Commit** — `"Add the Creator React preview iframe"`

---

### Task 22: `plugin.js` — error + console bridge

**Files:** Modify `plugin.js`; Test `node --check` + manual.

**Interfaces:**
- Produces: `crBridgeScript(nonce) -> string` — patches `window.onerror`/`onunhandledrejection` and `console.*`, `postMessage({type:'cr-error'|'cr-console', token: nonce, …}, '*')`; a top-level `ErrorBoundary` class in the srcdoc that also posts `cr-error`.
- `crUseFrameBridge(frameRef, nonce, {onError, onConsole})` — a hook adding a `window` `message` listener that accepts only `msg.type in {cr-error, cr-console}` **and** `msg.token === nonce` **and** `event.source === frameRef.current?.contentWindow`, re-validating/clamping every field.
- `CrErrorStrip`, `CrConsolePane` components (plain `<pre>` scrollback, per-level color, Clear button, ~300-line ring; `console.error` also feeds `CrErrorStrip`).

- [ ] **Step 1: Implement** the bridge + hook + components; splice `crBridgeScript(nonce)` into `crReactSrcdoc`.
- [ ] **Step 2: Run** — `node --check` → clean.
- [ ] **Step 3: Manual** — a `type=react` fixture that throws on mount → the error strip shows "Render error: …" not a blank frame; one that `console.log`s → the console pane shows it; a re-edit that fixes it clears the strip.
- [ ] **Step 4: Commit** — `"Add the Creator preview error and console bridge"`

---

### Task 23: `cr_store` + `cr_tools` — `type: react`

**Files:** Modify `cr_store.py`, `cr_tools.py`; Test both `_selfcheck()`.

**Interfaces:**
- Modifies: `cr_store.VALID_TYPES` → append `"react"`; `ext_for("react", …)` already returns `jsx` (Task 4).
- Modifies: `cr_tools.SCHEMAS["create_artifact"]` — add `react` to the `type` enum; `PROMPT_SECTION` — append the spec §6.5 paragraph + import list; re-assert `< 3900` chars (if it would exceed, trim the import list to comma-separated names only).

- [ ] **Step 1: Failing test** — `cr_store._selfcheck()`: `assert "react" in VALID_TYPES`; `r = do_create({"identifier":"app","type":"react","title":"App","content":"export default () => null"}, "s1"); assert do_read({"identifier":"app"})` works and the on-disk file is `v1.jsx`. `cr_tools._selfcheck()`: `assert "react" in SCHEMAS["create_artifact"]["properties"]["type"]["enum"]` and `len(PROMPT_SECTION) < 3900`.
- [ ] **Step 2–4:** implement, run both selfchecks + `python dashboard/selftest.py`.
- [ ] **Step 5: Commit** — `"Add the react artifact type"`

---

### Task 24: `plugin.js` — wire `type=react` into the pane preview

**Files:** Modify `plugin.js`; Test `node --check` + spec §9.2 Phase-2 checklist.

**Interfaces:**
- Modifies: `CrPreview` — for `type === 'react'`: a debounced pipeline (`crBundle` → on `ok` `crTailwind` → `crReactSrcdoc` → `CrReactFrame` + `CrConsolePane`; on `!ok` a `CrDiagnostics` panel, no iframe). Debounce ~400 ms; cache by content hash so version-stepping is instant.
- Produces: `CrDiagnostics` component.

- [ ] **Step 1: Implement** the `react` branch in `CrPreview`.
- [ ] **Step 2: Run** — `node --check` → clean.
- [ ] **Step 3: Manual** — the full spec §9.2 Phase-2 checklist: a React artifact runs; a syntax error shows diagnostics; a runtime error shows the error strip; the console pane works; arbitrary Tailwind classes render; editing re-renders.
- [ ] **Step 4: Commit** — `"Render react artifacts in the Creator pane"`

---

### Task 25: Phase 2 integration

**Files:** Modify `README.md`; Test full `selftest.py` + `node --check` + spec §9.2 Phase-2 checklist.

- [ ] **Step 1** — `README.md`: note the `creator-libs/` vendored assets (~16 MB, rebuilt by `build.mjs`) and the `type=react` capability.
- [ ] **Step 2** — `python dashboard/selftest.py` → `ok`; `node dashboard/creator-libs/verify.mjs` → `verify ok`; `node --check desktop/plugin.js` → clean.
- [ ] **Step 3** — full spec §9.2 Phase-2 manual checklist end to end.
- [ ] **Step 4: Commit** — `"Document the Creator React runtime"`

---

# PHASE 3 — editor, export, publish

Swaps the textarea for CodeMirror 6, adds standalone `.html` export for every type, and a Publish-to-Gist pane button.

---

### Task 26: `build.mjs` — vendor CodeMirror 6

**Files:** Modify `creator-libs/{package.json,package-lock.json,build.mjs,MANIFEST.json}`; add `codemirror.js` (committed); Test `verify.mjs`.

**Interfaces:**
- Produces: `codemirror.js` — a zero-import ESM bundling `@codemirror/{state,view,commands,language,search,autocomplete}`, `@codemirror/lang-{javascript,html,css,python,markdown}`, `@lezer/*`, one light + one dark theme; exports `{ EditorView, EditorState, basicExtensions(lang, dark), readOnly }` (a small hand-written facade so the renderer wrapper stays tiny).

- [ ] **Step 1: Failing test** (verify.mjs): `man['codemirror']` present, `codemirror.js` `node --check`s, zero-import.
- [ ] **Step 2–4:** add deps + a facade entry file to `build.mjs`, run.
- [ ] **Step 5: Commit** — `"Vendor CodeMirror 6 for the Creator editor"`

---

### Task 27: `plugin.js` — CodeMirror loader + wrapper

**Files:** Modify `plugin.js`; Test `node --check` + manual.

**Interfaces:**
- Produces: `crCodeMirror() -> Promise<module|null>` (`crAsset('codemirror.js')` → blob-import → cache; returns `null` on failure); `CrCmEditor({value, language, readOnly, onChange, onSave})` — ~50-line wrapper: a `useRef` div, one `EditorView` on mount, `dispatch` on external `value` change, `updateListener` → `onChange` + dirty, `EditorState.readOnly` binding, a `Mod-s` keymap → `onSave`. If `crCodeMirror()` returns `null`, render the Phase 1 `Textarea` with a one-line "rich editor unavailable" notice.

- [ ] **Step 1: Implement** `crCodeMirror`, `CrCmEditor`.
- [ ] **Step 2: Run** — `node --check` → clean.
- [ ] **Step 3: Manual** — mount `CrCmEditor` in isolation: highlights JS, ⌘F opens search, editing raises dirty, ⌘S fires `onSave`; then force `crAsset` to 404 and confirm the textarea fallback + notice.
- [ ] **Step 4: Commit** — `"Add the CodeMirror editor wrapper"`

---

### Task 28: `plugin.js` — swap `CrCmEditor` into the pane

**Files:** Modify `plugin.js`; Test `node --check` + spec §9.2 Phase-3 checklist (editor rows).

**Interfaces:**
- Modifies: `CrEditor` — use `CrCmEditor`; map `artifact.language`/`type` → CM language (`react`/`jsx`/`tsx` → javascript+jsx; `html`/`css`/`python`/`markdown`; else none). Keep the identical dirty-dot / Save (button + ⌘S) → `POST /creator/artifacts/{id}/versions` / read-only-on-old-version contract from Phase 1.

- [ ] **Step 1: Implement** the swap.
- [ ] **Step 2: Run** — `node --check` → clean.
- [ ] **Step 3: Manual** — edit + Save → new version; step to an old version → read-only; the fallback still works.
- [ ] **Step 4: Commit** — `"Use CodeMirror in the Creator pane editor"`

---

### Task 29: `cr_store` + `cr_api` — export

**Files:** Modify `cr_store.py`, `cr_api.py`, `selftest.py`; also add a small self-contained markdown/mermaid render shim to `creator-libs/` (`viewer.js`, committed) via `build.mjs`.

**Interfaces:**
- Produces: `cr_store.export_artifact(identifier, dest: str | None) -> str` (path) — for `html`: the content, wrapped in a minimal doc if it is a fragment; `svg`: the SVG inside a minimal HTML doc; `code`: the raw content as `<pre>` in a minimal doc (or the raw file if `dest` ends with the code ext); `markdown`/`mermaid`: a self-contained doc embedding the raw source + an inlined `viewer.js` (vendored `marked` + `mermaid`) that renders on load. Writes to `_creator_dir()/exports/<dir>-v<N>.html` or `dest`. For `react` it raises `StoreBadInput("react export is assembled in the pane")`.
- `cr_store.write_export_bundle(identifier, html: str, dest: str | None) -> str` — persists a pane-assembled file.
- `cr_api`: `POST /creator/artifacts/{id}/export {dest?}` and `POST /creator/artifacts/{id}/export/bundle {html, dest?}`.

- [ ] **Step 1: Failing test** — `selftest`: seed an `html`, `svg`, `code`, `markdown` artifact; `POST /creator/artifacts/{id}/export` → a path; assert the file exists, is non-empty, contains the content, and (markdown) contains the inlined viewer. `react` export → 400.
- [ ] **Step 2–4:** add `viewer.js` to `build.mjs`; implement; run `python dashboard/selftest.py` → `ok`.
- [ ] **Step 5: Commit** — `"Add standalone HTML export for Creator artifacts"`

---

### Task 30: `plugin.js` — export UI + react export assembly

**Files:** Modify `plugin.js`; Test `node --check` + spec §9.2 Phase-3 checklist (export rows).

**Interfaces:**
- Produces: an "Export" pane button. For non-react types → `POST /creator/artifacts/{id}/export`. For `react` → build the minified bundle (`crBundle` already `minify:true` via §6.2) + `crTailwind` CSS + `crThemePrelude`, assemble the full HTML (no bridge/runtime script), `POST /creator/artifacts/{id}/export/bundle {html}`. On success show the path with "Reveal in folder" / "Open in browser" calling `crCtx.os.revealPath` / `crCtx.os.openExternal` and branching on the boolean return; else show the path copyable.

- [ ] **Step 1–2:** implement; `node --check` → clean.
- [ ] **Step 3: Manual** — export each type; open each standalone in a browser; confirm the react export is styled and has no compiler/bridge script.
- [ ] **Step 4: Commit** — `"Add the Creator export action"`

---

### Task 31: `cr_store` + `cr_api` — Publish to Gist

**Files:** Modify `cr_store.py`, `cr_api.py`, `selftest.py`.

**Interfaces:**
- Produces: `cr_store.publish_artifact(identifier, html: str | None) -> dict` — produce the standalone HTML (`export_artifact` for non-react; require `html` for react, else `{error:"needs_pane"}`); if `shutil.which("gh")` and `gh auth status` succeeds → `subprocess.run(["gh","gist","create","--public",<file>,"--desc",<title>])`, parse the URL from stdout → `{url, raw_url}` (`raw_url` = a `githack`-style URL for `html`); else `{error:"github_not_configured", how:"install gh and run gh auth login, or set a token in Creator settings"}`. A `creator.github_token` REST path is a `# TODO(later)` one-liner comment, not implemented.
- `cr_api`: `POST /creator/artifacts/{id}/publish {html?}`.

- [ ] **Step 1: Failing test** — `selftest`: monkeypatch `shutil.which` to return a fake `gh` and `subprocess.run` to echo a gist URL → `publish_artifact` returns `{url: "...gist.github.com/..."}`; with `which` → `None` → `{error:"github_not_configured"}`.
- [ ] **Step 2–4:** implement, run `python dashboard/selftest.py` → `ok`.
- [ ] **Step 5: Commit** — `"Add Publish to Gist for Creator artifacts"`

---

### Task 32: `plugin.js` — Publish button + Phase 3 integration

**Files:** Modify `plugin.js`, `README.md`; Test full suite + spec §9.2 Phase-3 checklist.

- [ ] **Step 1** — a "Publish" pane button → `POST /creator/artifacts/{id}/publish` (for react, send the assembled `html`); on success a toast with the URL + a `CopyButton`; on `github_not_configured` a notice with the `how` text.
- [ ] **Step 2** — `node --check` → clean; `python dashboard/selftest.py` → `ok`; `node dashboard/creator-libs/verify.mjs` → `verify ok`.
- [ ] **Step 3** — `README.md`: CodeMirror editor, export, Gist publish; note `gh` is optional.
- [ ] **Step 4** — full spec §9.2 Phase-3 manual checklist.
- [ ] **Step 5: Commit** — `"Document the Creator editor, export, and publish"`

---

# PHASE 4 — the `window.hermes` in-artifact runtime

Injects `window.hermes` into `type=react` and `type=html` previews: `complete`, `storage`, `readFile`/`readdir` (three read-only scopes).

---

### Task 33: `cr_store` — storage + `.cr-meta`

**Files:** Modify `cr_store.py`; Test `_selfcheck()`.

**Interfaces:**
- Produces:
  - `storage_op(identifier, op: str, key: str | None, value=None) -> dict` — `op in {get, set, remove, keys}`; reads/writes `_creator_dir()/<dir>/storage.json` (one JSON object, atomic write, raise `StoreBadInput` if the serialized object would exceed 256 KB). Returns `{value}` / `{keys}` / `{ok}`.
  - `meta_get(identifier, key, default=None)` / `meta_set(identifier, key, value)` — `_creator_dir()/<dir>/.cr-meta.json` (Creator-internal; e.g. `complete_ok`).

- [ ] **Step 1: Failing test:**

```python
add_version("app", type_="react", title="A", language=None,
            content="export default()=>null", origin="tool", source="create", session_id="s")
assert storage_op("app", "set", "n", 1) == {"ok": True}
assert storage_op("app", "get", "n") == {"value": 1}
assert storage_op("app", "keys", None) == {"keys": ["n"]}
storage_op("app", "remove", "n")
assert storage_op("app", "get", "n") == {"value": None}
try:
    storage_op("app", "set", "big", "x" * 300_000); assert False
except StoreBadInput: pass
assert meta_get("app", "complete_ok") is None
meta_set("app", "complete_ok", True)
assert meta_get("app", "complete_ok") is True
assert not (_creator_dir() / "app" / "storage.json").read_text().__contains__("complete_ok")
```

- [ ] **Step 2–4:** implement, run.
- [ ] **Step 5: Commit** — `"Add Creator artifact storage and internal meta"`

---

### Task 34: `cr_api` — storage + readFile + readdir

**Files:** Modify `cr_api.py`, `selftest.py`.

**Interfaces:**
- Produces:
  - `GET /creator/artifacts/{id}/storage` (returns the whole object) and `POST /creator/artifacts/{id}/storage {op, key?, value?}` — **key in the body**.
  - `GET /creator/readfile?scheme=&path=` → JSON envelope (§3.6). Schemes: `artifact` (`_creator_dir()/<dir>/files/<path>`), `vault` (explicit-path import of the Knowledge note-read helper — `importlib.util.spec_from_file_location` on `dashboard/hw_context.py` or the relevant `hw_*`; call its path-guarded read; **no HTTP to Knowledge**), `project` (`cr_store.get_config()["project_root"] / <path>`). All read-only, path-guarded (`resolve().is_relative_to(root)`, refuse `islink`), 1 MB cap. Unconfigured scheme → 400 `{error:"scheme_unavailable"}`.
  - `GET /creator/readdir?scheme=&path=` → `{entries: [{name, dir: bool}]}`, same guards.
- Produces in `cr_store`: `read_scoped(scheme, path, project_root, vault_reader) -> bytes` and `list_scoped(...)` — the pure path-guard + read logic, so it is `_selfcheck`-able without HTTP.

- [ ] **Step 1: Failing test** — `selftest`: seed an `app` artifact + drop a file into `creator/<dir>/files/data.csv`; `GET /creator/readfile?scheme=artifact&path=data.csv` → envelope decoding to the CSV. Configure a `project_root` with a file → `scheme=project` reads it; `path=../escape` → 400. `scheme=vault` with no Knowledge vault configured → 400 `scheme_unavailable`; with one configured (seed via `hw_store`) → reads a note. `scheme=bogus` → 400. Storage: `POST .../storage {op:"set", key:"a/b", value:1}` then `{op:"get", key:"a/b"}` → `1` (slash key survives because it's in the body).
- [ ] **Step 2–4:** implement, run `python dashboard/selftest.py` → `ok`.
- [ ] **Step 5: Commit** — `"Add Creator storage and scoped file-read endpoints"`

---

### Task 35: `plugin.js` — the bridge request/response layer

**Files:** Modify `plugin.js`; Test `node --check` + manual.

**Interfaces:**
- Produces: `crRuntimeBridge(frameRef, nonce, { artifactId }) -> { dispose }` — extends the Task 22 listener to also accept `msg.type === 'cr-req'`; keeps a `Map<id, {resolve, reject}>`; dispatches `msg.op`:
  - `complete` → Task 36
  - `storage.get|set|remove|keys` → `crApi('POST /creator/artifacts/{id}/storage', {op, key, value})`
  - `readFile` / `readdir` → `crApi('GET /creator/readfile?…' | '…/readdir?…')` then decode the envelope
  - unknown `op` → `postMessage({type:'cr-res', token, id, ok:false, error:'unknown op'})`
  Every inbound field validated/clamped; oversize prompt/value rejected. Replies `postMessage({type:'cr-res', token: nonce, id, ok, value|error}, '*')`.
- Produces: `crRuntimeScript(nonce) -> string` — the in-iframe half: builds `window.hermes = { complete, storage:{get,set,remove,keys}, readFile, readdir }`, each `postMessage({type:'cr-req', token: nonce, id, op, …})` + a promise resolved by the matching `cr-res`.

- [ ] **Step 1: Implement**; splice `crRuntimeScript(nonce)` into `crReactSrcdoc` when `injectRuntime`.
- [ ] **Step 2: Run** — `node --check` → clean.
- [ ] **Step 3: Manual** — a fixture calling `await window.hermes.storage.set('x',1)` then `.get('x')` → `1`; a bad-token postMessage from the devtools console is ignored.
- [ ] **Step 4: Commit** — `"Add the Creator in-artifact runtime bridge"`

---

### Task 36: `plugin.js` — `window.hermes.complete`

**Files:** Modify `plugin.js`; Test `node --check` + manual.

**Interfaces:**
- Produces: `crComplete(prompt, artifactId) -> Promise<string>` in the bridge:
  - once per plugin session: probe `llm.oneshot` (reuse Knowledge's `rpcAvailable`/`MISSING_RPC` pattern — a `cr`-local copy, not a Knowledge symbol). If unavailable → reject `"model calls are unavailable on this Hermes build"` and mark the pane.
  - first call for `artifactId`: if `meta_get(artifactId,"complete_ok")` is not set, `ConfirmDialog` (*"'<title>' wants to call the model. Allow?"*); on allow → `crApi('POST /creator/artifacts/{id}/storage'...)`? no — `meta_set` is server-side; add `POST /creator/artifacts/{id}/meta {key,value}` in Task 34's endpoint set, or fold into storage with a reserved prefix. **Decision: a tiny `POST /creator/artifacts/{id}/meta` added in Task 34.** On deny → reject.
  - throttle 1/sec (a timestamp in the bridge); per-mount budget (default 50, a "reset" affordance in the pane); on exhaustion → reject `"model-call budget reached"`.
  - `host.request('llm.oneshot', { instructions: CR_HELPER_PROMPT, input: prompt, session_id: host.state.focusedSessionId || host.state.activeSessionId, temperature: 0.4, max_tokens: 1500 })` → resolve `text`.
- Add `POST /creator/artifacts/{id}/meta {key, value}` to Task 34's endpoints (retro-note: implement it in Task 34; this task only consumes it).

- [ ] **Step 1: Implement** `crComplete` + the probe + guards.
- [ ] **Step 2: Run** — `node --check` → clean.
- [ ] **Step 3: Manual** — a fixture with a "Ask" button calling `window.hermes.complete('say hi')`: first call prompts once, then works; rapid calls are throttled; after 50 the budget message shows; "reset budget" in the pane clears it.
- [ ] **Step 4: Commit** — `"Add window.hermes.complete"`

---

### Task 37: `plugin.js` — storage/readFile clients + exposure indicator + drop-zone

**Files:** Modify `plugin.js`; Test `node --check` + spec §9.2 Phase-4 checklist.

**Interfaces:**
- Produces: `CrExposure` component — "Exposed to this artifact:" listing the live scopes (`files/` always; `vault:` if Knowledge has a vault; `project:` if `get_config().project_root`); a "Set project folder…" action (`POST /creator/config`); a drop-zone that uploads into `creator/<dir>/files/` (add `POST /creator/artifacts/{id}/files` — multipart — to `cr_api`, retro-noted here, implemented in Task 34).
- Wires `window.hermes` injection into **both** `type=react` and `type=html` srcdoc (`injectRuntime: true`).

- [ ] **Step 1: Implement** `CrExposure`, the drop-zone, the html-preview runtime injection.
- [ ] **Step 2: Run** — `node --check` → clean.
- [ ] **Step 3: Manual** — the full spec §9.2 Phase-4 checklist: `complete` confirms once then works; `storage` survives a pane close/reopen; `readFile` sees only the listed scopes; dropping a file makes it readable; `vault:` reads a note; `project:` reads a file after setting the folder; an unset scope rejects.
- [ ] **Step 4: Commit** — `"Add the Creator exposure indicator and file drop-zone"`

---

### Task 38: Phase 4 integration + final

**Files:** Modify `README.md`; Test the whole suite + every phase's §9.2 checklist.

- [ ] **Step 1** — `README.md`: document `window.hermes` (`complete`, `storage`, `readFile`/`readdir`), the three read scopes, the exposure indicator, and the `complete` guards.
- [ ] **Step 2** — `python dashboard/selftest.py` → `ok`; `node dashboard/creator-libs/verify.mjs` → `verify ok`; `node --check desktop/plugin.js` → clean.
- [ ] **Step 3** — walk spec §9.2 checklists for Phases 1–4 end to end.
- [ ] **Step 4: Commit** — `"Document the Creator in-artifact runtime"`

---

## Self-Review

**Spec coverage:** every spec section maps to tasks — §3 constraints → Global Constraints + Tasks 1–2, 11–12; §4 layout → Task 1 + the File Structure block; §5.1–5.8 → Tasks 2–9; §5.9 scan → Task 10; §5.10 API → Tasks 11, 16, 29, 31, 34; §5.11 pane → Task 13; §6 React runtime → Tasks 15–25; §7 editor/export/publish → Tasks 26–32; §8 window.hermes → Tasks 33–37; §9 testing → every task's Steps + the integration tasks; §10 risks → the Phase-2 spike (Task 17) gates the biggest one; §12 install → Tasks 14, 25, 32, 38.

**Placeholder scan:** the only deferred item is the `creator.github_token` REST publish path (spec §7.3 explicitly a "one-line note"); it is a commented `# TODO(later)` in Task 31, not a plan gap. `_SCHEMA` in Task 2 and `PROMPT_SECTION` in Task 9 say "paste from spec §X" — the verbatim text lives in the committed spec that travels with this plan, per the header contract.

**Type consistency:** `add_version(...)` signature is fixed in Task 5 and consumed unchanged in Tasks 6, 7, 10, 23. `crBundle`/`crTailwind`/`crReactSrcdoc`/`crAsset`/`crEsbuild` are defined in Tasks 17–22 and reused by name in 24, 30, 35–37. Store exception classes (`StoreNotFound/StoreGone/StoreBadInput/StoreBusy`) are defined in Tasks 5–7 and mapped to HTTP once, in Task 11. `POST /creator/artifacts/{id}/meta` and `POST /creator/artifacts/{id}/files` are surfaced as retro-notes in Tasks 36–37 but **implemented in Task 34** — Task 34's Interfaces list is the source of truth; add them there when executing.

**Scope:** four phases, each independently green and shippable; a phase is a valid subagent-driven-development run on its own.

## Execution Handoff

**Plan complete and saved to `docs/plans/2026-08-31-creator-module.md`.** Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, task review + fix loop between tasks, a whole-branch review at the end of each phase. Run phase by phase.
2. **Inline Execution** — execute tasks in this session with checkpoints.

Which approach?
