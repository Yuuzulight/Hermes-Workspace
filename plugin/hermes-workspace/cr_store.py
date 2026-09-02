"""Creator store — all Creator behaviour. stdlib only; no relative imports."""
import base64, hashlib, json, os, re, sqlite3, time
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
            (identifier, n, ext, sha256_of(content), len(data), source,
             restored_from, now))

        if session_id:
            conn.execute(
                "INSERT INTO artifact_sessions(identifier, session_id, first_seen)"
                " VALUES(?,?,?) ON CONFLICT(identifier, session_id) DO NOTHING",
                (identifier, session_id, now))

        conn.execute("COMMIT")
        return {"identifier": identifier, "dir": dir_, "version": n,
                "sha256": sha256_of(content), "action": action}
    finally:
        conn.close()


def add_version(identifier, *, type_, title, language, content, origin, source,
                session_id, restored_from=None) -> dict:
    """Transactional version write (§5.3). Retries the whole op on lock
    contention, then raises StoreBusy."""
    for attempt in range(3):
        try:
            return _add_version_once(
                identifier, type_=type_, title=title, language=language,
                content=content, origin=origin, source=source,
                session_id=session_id, restored_from=restored_from)
        except (sqlite3.OperationalError, sqlite3.IntegrityError):
            if attempt == 2:
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


if __name__ == "__main__":
    _selfcheck()
    print("ok")
