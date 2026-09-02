"""Creator store — all Creator behaviour. stdlib only; no relative imports."""
import base64, difflib, hashlib, json, os, re, shutil, sqlite3, time
from pathlib import Path


def normalize(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def sanitize_identifier(raw: str) -> str:
    """Sanitize a raw identifier per spec §5.1: lowercase, [a-z0-9._-], collapse -,
    strip leading/trailing -., reject .., no backslash, first char alnum, cap 64,
    empty → "artifact"."""
    # §5.1: any ".." in the raw input is rejected outright
    if ".." in raw:
        return "artifact"
    # Lowercase and replace non-allowed chars with dash
    s = re.sub(r"[^a-z0-9._-]+", "-", raw.lower())
    # Collapse multiple dashes
    s = re.sub(r"-{2,}", "-", s)
    # Replace .. with dash to reject double dots
    s = s.replace("..", "-")
    # Strip leading/trailing dashes and dots
    s = s.strip("-.")
    # If first char is digit, prefix with 'a'
    if s and s[0].isdigit():
        s = "a" + s
    # Cap at 64 chars
    s = s[:64]
    # Empty or all invalid → "artifact"
    return s or "artifact"


LANG_EXT = {
    "python": "py",
    "javascript": "js",
    "js": "js",
    "typescript": "ts",
    "ts": "ts",
    "jsx": "jsx",
    "tsx": "tsx",
    "bash": "sh",
    "sh": "sh",
    "json": "json",
    "css": "css",
    "html": "html",
    "sql": "sql",
    "go": "go",
    "rust": "rs",
    "rs": "rs",
}

TYPE_EXT = {
    "markdown": "md",
    "mermaid": "mmd",
    "html": "html",
    "svg": "svg",
    "react": "jsx",
}

VALID_TYPES = ("code", "html", "svg", "markdown", "mermaid", "react")
TRUNCATION_NOTE = "… (full content in the artifact)"
OVERSIZE_NOTE = "open the Creator pane for the full artifact"
MAX_BYTES = 1_000_000  # §5.6 write cap, shared by the tool path and the HTTP path
_RETRIES = 3


def sha256_of(content: str) -> str:
    """Hash normalized content with SHA-256, hex-encoded."""
    normalized = normalize(content)
    return hashlib.sha256(normalized.encode()).hexdigest()


def ext_for(type_: str, language: str | None) -> str:
    """Return file extension (no leading dot) for artifact type and optional language."""
    if type_ == "code":
        return LANG_EXT.get(language, "txt") if language else "txt"
    return TYPE_EXT.get(type_, "txt")


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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
  identifier    TEXT PRIMARY KEY,
  dir           TEXT NOT NULL UNIQUE,
  type          TEXT NOT NULL,
  language      TEXT,
  title         TEXT NOT NULL,
  origin        TEXT NOT NULL,
  created_at    REAL NOT NULL,
  updated_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
  identifier    TEXT NOT NULL REFERENCES artifacts(identifier) ON DELETE CASCADE,
  n             INTEGER NOT NULL,
  ext           TEXT NOT NULL,
  sha256        TEXT NOT NULL,
  bytes         INTEGER NOT NULL,
  source        TEXT NOT NULL,
  restored_from INTEGER,
  created_at    REAL NOT NULL,
  PRIMARY KEY (identifier, n)
);
CREATE TABLE IF NOT EXISTS artifact_sessions (
  identifier    TEXT NOT NULL REFERENCES artifacts(identifier) ON DELETE CASCADE,
  session_id    TEXT NOT NULL,
  first_seen    REAL NOT NULL,
  PRIMARY KEY (identifier, session_id)
);
CREATE INDEX IF NOT EXISTS ix_versions_sha    ON versions(sha256);
CREATE INDEX IF NOT EXISTS ix_artifacts_updated ON artifacts(updated_at);
CREATE INDEX IF NOT EXISTS ix_artsess_session ON artifact_sessions(session_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


class StoreBusy(Exception):
    """Raised when a write op can't get the DB lock after retries (§3.4)."""


def _fsync_dir(path) -> None:
    """fsync a directory so a rename is durable. No-op on Windows (unsupported)."""
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _alloc_dir(conn: sqlite3.Connection, base: str) -> str:
    """Lowest free `<base>` / `<base>-<k>` slot (§5.3)."""
    taken = {r[0] for r in conn.execute(
        "SELECT dir FROM artifacts WHERE dir = ?1 OR dir GLOB ?1 || '-[0-9]*'",
        (base,))}
    if base not in taken:
        return base
    k = 2
    while f"{base}-{k}" in taken:
        k += 1
    return f"{base}-{k}"


def _add_version_once(identifier, *, type_, title, language, content,
                      origin, source, session_id, restored_from):
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = time.time()
        row = conn.execute(
            "SELECT dir, type, language FROM artifacts WHERE identifier = ?",
            (identifier,)).fetchone()
        if row:
            dir_, otype, olang = row
            ext = ext_for(otype, olang)
            n = conn.execute(
                "SELECT COALESCE(MAX(n), 0) FROM versions WHERE identifier = ?",
                (identifier,)).fetchone()[0] + 1
            action = "appended"
        else:
            dir_ = _alloc_dir(conn, sanitize_identifier(identifier))
            ext = ext_for(type_, language)
            n = 1
            action = "created"

        # File write happens INSIDE the txn, BEFORE the row inserts: a pre-COMMIT
        # raise rolls back the rows and the orphan .<ext> file is ignored by all
        # readers (they go through the index).
        d = _creator_dir() / dir_
        d.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        final = d / f"v{n}.{ext}"
        tmp = d / f"v{n}.{ext}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
        _fsync_dir(d)
        sha = sha256_of(content)

        if row:
            sets, params = ["updated_at = ?"], [now]
            if title:
                sets.append("title = ?"); params.append(title)
            if language:
                sets.append("language = ?"); params.append(language)
            params.append(identifier)
            conn.execute(
                f"UPDATE artifacts SET {', '.join(sets)} WHERE identifier = ?",
                params)
        else:
            conn.execute(
                "INSERT INTO artifacts(identifier, dir, type, language, title, "
                "origin, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (identifier, dir_, type_, language, title or "", origin, now, now))

        conn.execute(
            "INSERT INTO versions(identifier, n, ext, sha256, bytes, source, "
            "restored_from, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (identifier, n, ext, sha, len(data), source,
             restored_from, now))

        if session_id:
            conn.execute(
                "INSERT INTO artifact_sessions(identifier, session_id, first_seen)"
                " VALUES(?,?,?) ON CONFLICT(identifier, session_id) DO NOTHING",
                (identifier, session_id, now))

        conn.execute("COMMIT")
        return {"identifier": identifier, "dir": dir_, "version": n,
                "sha256": sha, "action": action}
    finally:
        conn.close()


def add_version(identifier, *, type_, title, language, content, origin, source,
                session_id, restored_from=None) -> dict:
    """Transactional version write (§5.3). Retries the whole op on lock
    contention, then raises StoreBusy."""
    for attempt in range(_RETRIES):
        try:
            return _add_version_once(
                identifier, type_=type_, title=title, language=language,
                content=content, origin=origin, source=source,
                session_id=session_id, restored_from=restored_from)
        except (sqlite3.OperationalError, sqlite3.IntegrityError):
            if attempt == _RETRIES - 1:
                raise StoreBusy("store busy, retry")
            time.sleep(0.1)


def latest(identifier) -> dict | None:
    """Newest version of an artifact, or None."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT v.n, v.sha256, a.type, v.ext, a.title, a.language "
            "FROM versions v JOIN artifacts a ON a.identifier = v.identifier "
            "WHERE v.identifier = ? ORDER BY v.n DESC LIMIT 1",
            (identifier,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {"version": row[0], "sha256": row[1], "type": row[2],
            "ext": row[3], "title": row[4], "language": row[5]}


class StoreNotFound(Exception):
    """Raised by a tool op that targets an identifier with no artifact."""


class StoreBadInput(Exception):
    """Raised on missing/invalid tool args (§5.7 error text)."""


def _meta(identifier):
    """(dir, type, title, language, updated_at, version_count, max_n, max_n_ext) or None."""
    conn = _connect()
    try:
        return conn.execute(
            "SELECT a.dir, a.type, a.title, a.language, a.updated_at, "
            "(SELECT COUNT(*) FROM versions WHERE identifier = a.identifier), "
            "(SELECT MAX(n) FROM versions WHERE identifier = a.identifier), "
            "(SELECT ext FROM versions WHERE identifier = a.identifier "
            " ORDER BY n DESC LIMIT 1) "
            "FROM artifacts a WHERE a.identifier = ?", (identifier,)).fetchone()
    finally:
        conn.close()


def _cap(s: str, limit: int, note: str | None = None) -> str:
    """Truncate `s` to `limit` bytes on a char boundary; append `note` if cut."""
    if len(s.encode("utf-8")) <= limit:
        return s
    out = s.encode("utf-8")[:limit].decode("utf-8", "ignore")
    return out + ("\n" + note if note else "")


def _check_size(content: str) -> None:
    """Shared §5.6 write cap: any write (tool or HTTP), content > 1 MB → error."""
    if len(content.encode("utf-8")) > MAX_BYTES:
        raise StoreBadInput(f"content too large (max {MAX_BYTES:,} bytes)")


def record_session(identifier, session_id) -> None:
    """Link a session to an artifact without adding a version (§5.7 unchanged)."""
    if not session_id:
        return
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO artifact_sessions(identifier, session_id, first_seen) "
            "VALUES(?,?,?) ON CONFLICT(identifier, session_id) DO NOTHING",
            (identifier, session_id, time.time()))
    finally:
        conn.close()


def do_create(args: dict, session_id: str) -> dict:
    """`create_artifact` dispatch (§5.7). No content echoed in the result."""
    ident = (args.get("identifier") or "").strip()
    type_ = args.get("type") or ""
    title = args.get("title") or ""
    content = args.get("content")
    language = args.get("language") or None
    if not (ident and type_ and title and content):
        raise StoreBadInput("create_artifact requires identifier, type, title, content")
    if type_ not in VALID_TYPES:
        raise StoreBadInput("type must be one of " + ", ".join(VALID_TYPES))
    _check_size(content)

    exists = latest(ident) is not None
    source = "update" if exists else "create"
    r = add_version(ident, type_=type_, title=title, language=language,
                    content=content, origin="tool", source=source,
                    session_id=session_id)
    cur = latest(ident)
    if not exists:
        return {"identifier": ident, "version": r["version"], "type": cur["type"],
                "title": cur["title"], "action": "created", "note": ""}
    note = f"identifier exists; kept original type '{cur['type']}' (ext .{cur['ext']})"
    if type_ != cur["type"]:
        note += f"; ignored requested type '{type_}'"
    return {"identifier": ident, "version": r["version"], "type": cur["type"],
            "title": cur["title"], "action": "updated", "note": note}


def do_update(args: dict, session_id: str) -> dict:
    """`update_artifact` dispatch (§5.6/§5.7)."""
    ident = (args.get("identifier") or "").strip()
    content = args.get("content")
    if content is None:
        raise StoreBadInput("update_artifact requires identifier, content")
    _check_size(content)
    prev = latest(ident)
    if prev is None:
        raise StoreNotFound(f"no artifact '{ident}' — call create_artifact first")
    if OVERSIZE_NOTE in content:
        raise StoreBadInput(
            "content still contains the oversize-artifact note; "
            "open the Creator pane and copy the full artifact first")
    if sha256_of(content) == prev["sha256"]:
        record_session(ident, session_id)
        return {"action": "unchanged", "version": prev["version"]}

    prev_n, ext = prev["version"], prev["ext"]
    r = add_version(ident, type_=prev["type"], title=prev["title"],
                    language=prev["language"], content=content, origin="tool",
                    source="update", session_id=session_id)
    prev_text = (_creator_dir() / r["dir"] / f"v{prev_n}.{ext}").read_text(
        encoding="utf-8")
    diff = "".join(difflib.unified_diff(
        prev_text.splitlines(keepends=True), content.splitlines(keepends=True),
        fromfile=f"v{prev_n}", tofile=f"v{r['version']}"))
    return {"action": "updated", "version": r["version"],
            "diff": _cap(diff, 4096),
            "content": _cap(content, 10240, TRUNCATION_NOTE)}


def do_read(args: dict) -> dict:
    """`read_artifact` dispatch (§5.6). Full content unless > 256 KB. Reuses
    get_version() for the file read so a row-exists/file-missing artifact
    raises StoreGone (like get_version's own callers) instead of an unguarded
    FileNotFoundError."""
    ident = (args.get("identifier") or "").strip()
    row = _meta(ident)
    if row is None:
        raise StoreNotFound(f"no artifact '{ident}' — call create_artifact first")
    dir_, type_, title, language, updated_at, vcount, maxn, maxn_ext = row
    text = get_version(ident, maxn)["content"]
    out = {"identifier": ident, "version": maxn, "type": type_, "title": title,
           "version_count": vcount, "updated_at": updated_at}
    if len(text.encode("utf-8")) > 256 * 1024:
        # The note must live INSIDE content (not just a sibling key) so
        # do_update's `OVERSIZE_NOTE in content` guard still fires if this
        # truncated read is fed straight back into update_artifact.
        out["content"] = _cap(text, 256 * 1024, OVERSIZE_NOTE)
        out["truncated"] = True
    else:
        out["content"] = text
    return out


class StoreGone(Exception):
    """Raised when a version row exists but its backing file is missing (§5.10 → HTTP 410)."""


def get_version(identifier, n) -> dict:
    """One version's stored content (§5.10 `GET /v/{n}`). StoreNotFound if no row,
    StoreGone if the row exists but the file is gone. Reads by the row's stored ext."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT a.dir, a.type, v.ext FROM versions v "
            "JOIN artifacts a ON a.identifier = v.identifier "
            "WHERE v.identifier = ? AND v.n = ?", (identifier, n)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise StoreNotFound(f"no version {n} of '{identifier}'")
    dir_, type_, ext = row
    p = _creator_dir() / dir_ / f"v{n}.{ext}"
    if not p.exists():
        raise StoreGone(f"v{n} of '{identifier}' is missing on disk")
    return {"identifier": identifier, "n": n, "type": type_,
            "content": p.read_text(encoding="utf-8")}


def restore(identifier, n, session_id) -> dict:
    """Restore v<n> as a new version (§5.10). StoreGone if the v<n> file is gone;
    normalize-equal to latest → unchanged; else a new version, source='restore'."""
    v = get_version(identifier, n)
    cur = latest(identifier)
    if sha256_of(v["content"]) == cur["sha256"]:
        record_session(identifier, session_id)
        return {"action": "unchanged", "version": cur["version"]}
    r = add_version(identifier, type_=cur["type"], title=cur["title"],
                    language=cur["language"], content=v["content"], origin="tool",
                    source="restore", session_id=session_id, restored_from=n)
    return {"action": "restored", "version": r["version"]}


def edit_version(identifier, content) -> dict:
    """Pane user-edit write (§5.10 `POST /versions {content}`). Like do_update but
    `source='user-edit'` and a slim result; no session link (the pane has none)."""
    if content is None:
        raise StoreBadInput("edit_version requires content")
    _check_size(content)
    prev = latest(identifier)
    if prev is None:
        raise StoreNotFound(f"no artifact '{identifier}'")
    if sha256_of(content) == prev["sha256"]:
        return {"identifier": identifier, "version": prev["version"], "action": "unchanged"}
    r = add_version(identifier, type_=prev["type"], title=prev["title"],
                    language=prev["language"], content=content, origin="tool",
                    source="user-edit", session_id="")
    return {"identifier": identifier, "version": r["version"], "action": "updated"}


def list_artifacts(session_id: str | None) -> list[dict]:
    """Every artifact with its latest version (§5.10 `GET /artifacts`). In-session
    artifacts first, then by `updated_at` desc. session_id=None → all in_session=False."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT a.identifier, a.type, a.title, "
            "(SELECT MAX(n) FROM versions WHERE identifier = a.identifier), "
            "a.updated_at, a.origin, "
            "EXISTS(SELECT 1 FROM artifact_sessions s "
            "       WHERE s.identifier = a.identifier AND s.session_id = ?) AS in_session "
            "FROM artifacts a "
            "ORDER BY in_session DESC, a.updated_at DESC", (session_id,)).fetchall()
    finally:
        conn.close()
    return [{"identifier": r[0], "type": r[1], "title": r[2], "version": r[3],
             "updated_at": r[4], "origin": r[5], "in_session": bool(r[6])}
            for r in rows]


def get_artifact(identifier) -> dict:
    """Artifact metadata + its version list ascending by n (§5.10 `GET /artifacts/{id}`)."""
    row = _meta(identifier)
    if row is None:
        raise StoreNotFound(f"no artifact '{identifier}' — call create_artifact first")
    dir_, type_, title, language, updated_at, vcount, maxn, maxn_ext = row
    conn = _connect()
    try:
        vs = conn.execute(
            "SELECT n, source, restored_from, created_at, bytes FROM versions "
            "WHERE identifier = ? ORDER BY n", (identifier,)).fetchall()
    finally:
        conn.close()
    return {"identifier": identifier, "type": type_, "language": language,
            "title": title, "version_count": vcount, "updated_at": updated_at,
            "versions": [{"n": v[0], "source": v[1], "restored_from": v[2],
                          "created_at": v[3], "bytes": v[4]} for v in vs]}


def delete_artifact(identifier) -> None:
    """Delete an artifact, its rows (FK cascade), and its directory (§5.10 `DELETE`)."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT dir FROM artifacts WHERE identifier = ?", (identifier,)).fetchone()
        if row is None:
            return
        d = _creator_dir() / row[0]
        base = _creator_dir()
        if not os.path.realpath(d).startswith(os.path.realpath(base) + os.sep):
            raise StoreBadInput("refusing to delete outside creator dir")
        conn.execute("DELETE FROM artifacts WHERE identifier = ?", (identifier,))
    finally:
        conn.close()
    shutil.rmtree(d, ignore_errors=True)


def get_config() -> dict:
    """Read config from _creator_dir()/config.json. Missing file defaults to
    {"project_root": None, "github_token_set": False}."""
    cfg_path = _creator_dir() / "config.json"
    if not cfg_path.exists():
        return {"project_root": None, "github_token_set": False}
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        # Always return github_token_set as bool, never the token value
        return {
            "project_root": data.get("project_root"),
            "github_token_set": bool(data.get("github_token")),
        }
    except Exception:
        return {"project_root": None, "github_token_set": False}


def set_config(patch: dict) -> dict:
    """Merge patch into on-disk config, write atomically. Validates project_root
    (absolute, exists, is dir, or None). Stores github_token, never echoes it.
    Returns get_config()."""
    cfg_path = _creator_dir() / "config.json"

    # Read current config
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}

    # Merge patch
    if "project_root" in patch:
        pr = patch["project_root"]
        if pr is not None:
            # Validate: absolute, exists, is a dir
            pr_path = Path(pr)
            if not pr_path.is_absolute():
                raise StoreBadInput("project_root must be an absolute path or None")
            if not pr_path.exists():
                raise StoreBadInput("project_root must exist")
            if not pr_path.is_dir():
                raise StoreBadInput("project_root must be a directory")
        data["project_root"] = pr

    if "github_token" in patch:
        data["github_token"] = patch["github_token"]

    # Write atomically
    tmp = cfg_path.with_name(cfg_path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, cfg_path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise

    return get_config()


# ── Transcript scan (§5.9) ────────────────────────────────────────────────────

NON_ARTIFACT_LANGS = frozenset({
    "", "console", "diff", "log", "logs", "markdown", "md", "mermaid", "output",
    "patch", "plain", "plaintext", "shell-session", "stdout", "text", "txt",
})

_FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)\n?```", re.DOTALL)
_DECL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:def|class|function|const|let|var|type|interface|struct|fn|func|public|private)\s+"
    r"([A-Za-z_$][\w$]*)", re.M)
_TAG_RE = re.compile(r"<[a-zA-Z/][^>]*>")

_SCAN_DEBOUNCE = 10.0
_scan_seen: dict[str, float] = {}

# SCAN_ROWS mirrors the §5.9 precedence table for readers / spec cross-ref;
# _classify() below is its executable form (first matching row decides).
SCAN_ROWS = (
    ("svg",      "lang svg or body starts <svg",            2000, "chars"),
    ("html",     "lang html/htm/xhtml or empty+doc wrapper", 160,  "chars"),
    ("html",     "lang empty, tag-dense, no wrapper",        1200, "chars"),
    ("mermaid",  "lang mermaid",                             0,    "any"),
    ("markdown", "lang md/markdown",                         600,  "chars"),
    ("code",     "other lang not in NON_ARTIFACT_LANGS",     48,   "lines or 3000 chars"),
)


def _read_assistant_messages(session_id: str) -> list[str]:
    """Assistant message texts for a session — the monkeypatchable scan seam
    (§5.9). Lazy-imports the host `SessionDB`; returns [] if it is unavailable."""
    try:
        from hermes_state import SessionDB
    except Exception:
        return []
    try:
        msgs = SessionDB().get_messages(session_id, include_compacted=True)
    except Exception:
        return []
    out = []
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, list):
            out.append(" ".join(
                b.get("text", "") for b in c if isinstance(b, dict)))
        elif isinstance(c, str):
            out.append(c)
    return out


def _fenced_blocks(text: str) -> list[tuple[str, str]]:
    """(lang, body) for every ```lang\\n…\\n``` fence; lang stripped/lowercased."""
    return [(m.group(1).strip().lower(), m.group(2))
            for m in _FENCE_RE.finditer(text)]


def _scan_slug(body: str, lang: str) -> str:
    """Slug source: <title> → <h1> → first declaration → lang (§5.9)."""
    for pat in (r"<title[^>]*>(.*?)</title>", r"<h1[^>]*>(.*?)</h1>"):
        m = re.search(pat, body, re.I | re.S)
        if m and m.group(1).strip():
            return m.group(1).strip()
    m = _DECL_RE.search(body)
    if m:
        return m.group(1)
    return lang or "artifact"


def _classify(lang: str, body: str) -> tuple[str, str] | None:
    """Fenced block → (type, slug) per the §5.9 precedence table, else None.
    First matching row decides: a signal match below its threshold returns None
    and does NOT fall through to a lower row — this is what makes a `<svg`-body
    in an `html` fence classify as svg rather than html."""
    b = body.strip()
    low = b.lower()
    if lang == "svg" or low.startswith("<svg"):
        return ("svg", _scan_slug(body, lang)) if len(body) >= 2000 else None
    wrapper = low.startswith("<!doctype") or low.startswith("<html")
    if lang in ("html", "htm", "xhtml") or (lang == "" and wrapper):
        return ("html", _scan_slug(body, lang)) if len(body) >= 160 else None
    if lang == "" and not wrapper and len(_TAG_RE.findall(b)) >= 3:
        return ("html", _scan_slug(body, lang)) if len(body) >= 1200 else None
    if lang == "mermaid":
        return ("mermaid", _scan_slug(body, lang))
    if lang in ("md", "markdown"):
        return ("markdown", _scan_slug(body, lang)) if len(body) >= 600 else None
    if lang not in NON_ARTIFACT_LANGS:
        if body.count("\n") + 1 >= 48 or len(body) >= 3000:
            return ("code", _scan_slug(body, lang))
    return None


def _scan_identifier(slug: str, content: str) -> str:
    """`slug[:55] + '-' + hash8` — the hash suffix is appended AFTER the 55-char
    slug cut and BEFORE the 64-char cap, so the deterministic suffix survives."""
    return sanitize_identifier(slug)[:55].rstrip("-") + "-" + sha256_of(content)[:8]


def scan(session_id: str) -> dict:
    """Scan a session's assistant transcript for fenced artifacts (§5.9).
    Debounced ~10 s per session; idempotent — dedups candidates by content hash
    against every existing version, linking the session to the matched artifact."""
    now = time.time()
    last = _scan_seen.get(session_id)
    if last is not None and now - last < _SCAN_DEBOUNCE:
        return {"found": 0, "skipped": 0}
    _scan_seen[session_id] = now

    found = skipped = 0
    for text in _read_assistant_messages(session_id):
        for lang, body in _fenced_blocks(text):
            hit = _classify(lang, body)
            if hit is None:
                continue
            type_, slug = hit
            sha = sha256_of(body)
            conn = _connect()
            try:
                owner = conn.execute(
                    "SELECT identifier FROM versions WHERE sha256 = ? LIMIT 1",
                    (sha,)).fetchone()
            finally:
                conn.close()
            if owner:
                skipped += 1
                record_session(owner[0], session_id)
                continue
            add_version(
                _scan_identifier(slug, body), type_=type_, title=slug,
                language=(lang if type_ == "code" else None), content=body,
                origin="scan", source="scan", session_id=session_id)
            found += 1
    return {"found": found, "skipped": skipped}


def _selfcheck() -> None:
    # skeleton assertions grow every task
    assert normalize("a\r\nb\n") == "a\nb"

    # Task 2: cr_store connection, schema, and paths
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

            # Task 5: add_version — transactional write path
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
            assert latest("w") == {"version": 2, "sha256": sha256_of("print(2)"),
                                   "type": "code", "ext": "py", "title": "W2", "language": "python"}
            assert latest("nope") is None

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

            # Task 6: do_create / do_update / do_read dispatch
            c = do_create({"identifier": "doc", "type": "markdown", "title": "Doc", "content": "# hi"}, "s1")
            assert c["action"] == "created" and c["version"] == 1 and "content" not in c
            c2 = do_create({"identifier": "doc", "type": "code", "title": "Doc2", "content": "# hi\n\nmore"}, "s1")
            assert c2["action"] == "updated" and c2["type"] == "markdown"  # original type kept
            u_same = do_update({"identifier": "doc", "content": "# hi\n\nmore\n"}, "s-unchanged")  # only trailing \n differs
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

            # Fix 2: MAX_BYTES cap enforced on every write path, not just HTTP
            big = "x" * (MAX_BYTES + 1)
            try:
                do_create({"identifier": "toobig", "type": "code", "title": "T",
                          "content": big}, "s1"); assert False
            except StoreBadInput: pass
            try:
                do_update({"identifier": "doc", "content": big}, "s1"); assert False
            except StoreBadInput: pass
            try:
                edit_version("doc", big); assert False
            except StoreBadInput: pass

            # Fix 1: oversize truncation note lives IN content, not a sibling
            # key — so a truncated read fed straight back into update_artifact
            # is caught by do_update's own `OVERSIZE_NOTE in content` guard.
            do_create({"identifier": "huge", "type": "code", "title": "Huge",
                      "content": "y" * (300 * 1024)}, "s1")
            hr = do_read({"identifier": "huge"})
            assert hr["truncated"] is True
            assert OVERSIZE_NOTE in hr["content"]
            try:
                do_update({"identifier": "huge", "content": hr["content"]}, "s1"); assert False
            except StoreBadInput: pass

            # Fix 5: do_read on a row-exists/file-missing artifact raises
            # StoreGone (like get_version), not a raw FileNotFoundError.
            do_create({"identifier": "vanish", "type": "markdown", "title": "V",
                      "content": "body"}, "s1")
            meta = _meta("vanish")
            os.remove(_creator_dir() / meta[0] / f"v{meta[6]}.{meta[7]}")
            try:
                do_read({"identifier": "vanish"}); assert False
            except StoreGone: pass

            # session recorded even on unchanged (fresh id, not touched by v1-v3 writes)
            conn = _connect()
            try:
                assert conn.execute(
                    "SELECT COUNT(*) FROM artifact_sessions "
                    "WHERE identifier='doc' AND session_id='s-unchanged'").fetchone()[0] == 1
            finally:
                conn.close()

            # Task 7: get_version / restore / list_artifacts / get_artifact / delete_artifact
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
            # Task 11: edit_version — user-edit source, slim result, no-op detect
            assert edit_version("doc", "pane edit") == {"identifier": "doc", "version": 6, "action": "updated"}
            assert edit_version("doc", "pane edit")["action"] == "unchanged"
            assert get_artifact("doc")["versions"][-1]["source"] == "user-edit"
            # missing-file -> StoreGone
            delete_artifact("doc")
            try:
                get_artifact("doc"); assert False
            except StoreNotFound: pass
            assert not (_creator_dir() / "doc").exists()

            # focused StoreGone: row exists, file gone
            add_version("gone-t", type_="markdown", title="G", language=None,
                        content="body", origin="tool", source="create", session_id="s1")
            assert get_version("gone-t", 1)["content"] == "body"
            os.remove(_creator_dir() / "gone-t" / "v1.md")
            try:
                get_version("gone-t", 1); assert False
            except StoreGone: pass
            try:
                get_version("gone-t", 9); assert False
            except StoreNotFound: pass
            # session_id=None -> nothing in_session
            assert all(not a["in_session"] for a in list_artifacts(None))

            # Task 8: get_config / set_config
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
    finally:
        if saved is None: os.environ.pop("HERMES_HOME", None)
        else: os.environ["HERMES_HOME"] = saved

    # Task 10: transcript scan — classifier + dedup + debounce (own clean DB)
    saved = os.environ.get("HERMES_HOME")
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            _scan_seen.clear()
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
                assert not any(types[i] == "code" and "1" in i for i in ids)  # js_short skipped (not enough lines)
                assert any(t == "markdown" for t in types.values())   # md_big
                assert sum(t == "markdown" for t in types.values()) == 1  # md_small skipped
                r2 = scan("sess-x2")
                assert r2["found"] == 0 and r2["skipped"] >= 4  # dedup by hash, links the new session
                assert any(a["in_session"] for a in list_artifacts("sess-x2"))
                long_slug = _scan_identifier("a" * 90, "body")
                assert len(long_slug) <= 64 and long_slug.endswith(sha256_of("body")[:8])
            finally:
                globals()["_read_assistant_messages"] = _orig
    finally:
        if saved is None: os.environ.pop("HERMES_HOME", None)
        else: os.environ["HERMES_HOME"] = saved

    # Task 3: sanitize_identifier
    assert sanitize_identifier("My Cool Widget!!") == "my-cool-widget"
    assert sanitize_identifier("") == "artifact"
    assert sanitize_identifier("...") == "artifact"
    assert sanitize_identifier("../etc/passwd") == "artifact"
    assert sanitize_identifier("a" * 200) == "a" * 64
    assert sanitize_identifier("---a---b---") == "a-b"
    assert not sanitize_identifier("9lives")[0].isdigit()

    # Task 4: sha256_of, TYPE_EXT, LANG_EXT, ext_for
    assert sha256_of("x\r\n") == sha256_of("x\n") == sha256_of("x")
    assert ext_for("markdown", None) == "md"
    assert ext_for("mermaid", None) == "mmd"
    assert ext_for("code", "python") == "py"
    assert ext_for("code", "brainfuck") == "txt"
    assert ext_for("react", None) == "jsx"

    # Task 23: type: react
    assert "react" in VALID_TYPES
    saved = os.environ.get("HERMES_HOME")
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            r = do_create({"identifier": "app", "type": "react", "title": "App",
                           "content": "export default () => null"}, "s1")
            assert r["version"] == 1 and r["type"] == "react"
            assert do_read({"identifier": "app"})["content"] == "export default () => null"
            dir_ = _meta("app")[0]
            assert (_creator_dir() / dir_ / "v1.jsx").is_file()
    finally:
        if saved is None: os.environ.pop("HERMES_HOME", None)
        else: os.environ["HERMES_HOME"] = saved


if __name__ == "__main__":
    _selfcheck()
    print("ok")
