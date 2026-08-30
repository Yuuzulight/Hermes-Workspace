"""Creator store — all Creator behaviour. stdlib only; no relative imports."""
import base64, hashlib, json, os, re, sqlite3, time
from pathlib import Path


def normalize(content: str) -> str:
    """Content normalization for hashing and dedup."""
    return content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def _hermes_home() -> Path:
    """Resolve Hermes home path with fallback."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        base = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
        return Path(base)


def _creator_dir() -> Path:
    """Resolve Creator data directory. Always fresh, never cached."""
    try:
        from plugins.plugin_storage import plugin_data_dir
        base = Path(plugin_data_dir("hermes-workspace"))
    except Exception:
        base = _hermes_home() / "plugin-data" / "hermes-workspace"
    d = base / "creator"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_path() -> Path:
    """SQLite database path."""
    return _creator_dir() / "creator-index.db"


# Schema from spec §5.2 (all with IF NOT EXISTS)
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
CREATE INDEX IF NOT EXISTS ix_versions_sha ON versions(sha256);
CREATE INDEX IF NOT EXISTS ix_artifacts_updated ON artifacts(updated_at);
CREATE INDEX IF NOT EXISTS ix_artsess_session ON artifact_sessions(session_id);
"""


def _connect() -> sqlite3.Connection:
    """Open connection with PRAGMA settings for cross-process safety."""
    conn = sqlite3.connect(_db_path(), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def _fsync_dir(path: Path):
    """Synchronize directory to disk."""
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class StoreBusy(Exception):
    """Raised when store lock times out after retries."""
    pass


def _selfcheck() -> None:
    """Self-test for cr_store.py. Run via python cr_store.py"""
    saved = os.environ.get("HERMES_HOME")
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            
            # Test _creator_dir()
            cd = _creator_dir()
            assert cd.is_dir(), f"_creator_dir() returned {cd}, not a directory"
            assert cd.name == "creator", f"Expected 'creator', got '{cd.name}'"
            
            # Test _connect() and schema
            conn = _connect()
            try:
                names = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                assert {"artifacts", "versions", "artifact_sessions"} <= names, f"Missing tables: {names}"
                
                idx = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'")}
                assert "ix_artsess_session" in idx, f"Missing index ix_artsess_session: {idx}"
                
                # Test PRAGMA settings
                assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1, "foreign_keys not ON"
                assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000, "busy_timeout != 5000"
                
            finally:
                conn.close()
    finally:
        if saved is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved

import tempfile
_selfcheck()
print("ok")


def sanitize_identifier(raw: str) -> str:
    """Sanitize artifact identifier per spec §5.1."""
    if not raw or not isinstance(raw, str):
        return "artifact"
    
    # Lowercase and keep only [a-z0-9._-]
    s = re.sub(r"[^a-z0-9._-]+", "-", raw.lower())
    # Collapse multiple dashes
    s = re.sub(r"-{2,}", "-", s)
    # Strip leading/trailing dashes
    s = s.strip("-") or "artifact"
    # Reject .. and backslashes (path traversal protection)
    if ".." in s or "\\" in s:
        return "artifact"
    # First char must be alphanumeric
    if not s[0].isalnum():
        s = "a" + s
    # Cap at 64 chars
    s = s[:64]
    # Empty after sanitization -> artifact
    return s or "artifact"


def compute_sha256(content: str) -> str:
    """Compute SHA-256 of normalized content."""
    return hashlib.sha256(normalize(content).encode("utf-8")).hexdigest()


def extension_for_type(type_name: str) -> str:
    """Map artifact type to file extension per spec §5.3."""
    # Core types from design doc
    mapping = {
        "react": ".html",
        "markdown": ".md",
        "text": ".txt",
        "code": ".ts",  # TypeScript source
        "json": ".json",
        "css": ".css",
        "js": ".js",
    }
    return mapping.get(type_name.lower(), ".art")


def content_hash_for_identifier(identifier: str, sha256_hex: str) -> str:
    """Create a stable hash key for the artifact."""
    return hashlib.sha1(f"{identifier}:{sha256_hex}".encode("utf-8")).hexdigest()[:32]


def _write_version_atomic(identifier: str, n: int, ext: str, sha256_hex: str, bytes_size: int, source: Path):
    """Write a single version atomically using temp file + rename."""
    dir_path = _creator_dir() / identifier
    if not dir_path.exists():
        dir_path.mkdir(parents=True)

    # Write to .tmp first
    tmp_path = dir_path / f"{n}.{ext}.tmp"
    with open(tmp_path, "wb") as f:
        for chunk in source.chunks(64 * 1024):
            f.write(chunk)

    # Verify size before rename
    actual_size = tmp_path.stat().st_size
    assert actual_size == bytes_size, f"Size mismatch: expected {bytes_size}, got {actual_size}"

    # Atomic rename (cross-platform safe)
    dest_path = dir_path / f"{n}.{ext}"
    if dest_path.exists():
        dest_path.unlink()  # Replace old version directly
    tmp_path.rename(dest_path)

    # Fsync directory to disk
    _fsync_dir(dir_path)


def _read_version(identifier: str, n: int) -> Path | None:
    """Read a specific version path."""
    dir_path = _creator_dir() / identifier
    if not (dir_path.exists() and (dir_path / f"{n}.art").exists()):
        return None
    return dir_path / f"{n}.art"


def _list_versions(identifier: str) -> list[tuple[int, Path]]:
    """List all versions for an identifier."""
    dir_path = _creator_dir() / identifier
    if not dir_path.exists():
        return []

    versions = []
    for f in sorted(dir_path.iterdir()):
        match = re.match(r"^(\d+)\.art$", f.name)
        if match:
            n = int(match.group(1))
            versions.append((n, f))
    return versions


def _delete_version(identifier: str, n: int):
    """Delete a specific version."""
    path = _read_version(identifier, n)
    if path and path.exists():
        path.unlink()
        # Fsync parent directory
        _fsync_dir(_creator_dir() / identifier)


def _delete_identifier(identifier: str):
    """Delete all versions for an identifier."""
    dir_path = _creator_dir() / identifier
    if dir_path.exists():
        import shutil
        shutil.rmtree(dir_path)
