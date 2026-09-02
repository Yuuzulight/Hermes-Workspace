"""Creator store — all Creator behaviour. stdlib only; no relative imports."""
import base64, hashlib, json, os, re, sqlite3, time
from pathlib import Path


def normalize(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def sanitize_identifier(raw: str) -> str:
    """Sanitize a raw identifier per spec §5.1: lowercase, [a-z0-9._-], collapse -,
    strip leading/trailing -., reject .., no backslash, first char alnum, cap 64,
    empty → "artifact"."""
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
    finally:
        if saved is None: os.environ.pop("HERMES_HOME", None)
        else: os.environ["HERMES_HOME"] = saved

    # Task 3: sanitize_identifier
    assert sanitize_identifier("My Cool Widget!!") == "my-cool-widget"
    assert sanitize_identifier("") == "artifact"
    assert sanitize_identifier("...") == "artifact"
    assert sanitize_identifier("../etc/passwd") == "artifact" or ".." not in sanitize_identifier("../etc/passwd")
    assert sanitize_identifier("a" * 200) == "a" * 64
    assert sanitize_identifier("---a---b---") == "a-b"
    assert not sanitize_identifier("9lives")[0].isdigit()


if __name__ == "__main__":
    _selfcheck()
    print("ok")
