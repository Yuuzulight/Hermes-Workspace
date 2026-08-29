"""SQLite FTS5 index over the vault. The one search entry point."""
import os
import re
import sqlite3
import time

import hw_notes
import hw_store

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER, sha1 TEXT,
  title TEXT, indexed_at INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes USING fts5(
  path UNINDEXED, title, headings, tags, links, frontmatter, body,
  tokenize = "unicode61 remove_diacritics 2 tokenchars '#/_-'"
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

_SKIP_DIRS = {".obsidian", ".trash", ".git", ".hermes"}
_TOKEN_RE = re.compile(r"\S+")
_DEBOUNCE_NS = 2_000_000_000


def sanitize_fts_query(s: str) -> str:
    out = []
    for tok in _TOKEN_RE.findall(s.strip())[:40]:
        if tok.startswith("#") and len(tok) > 1:
            name = re.sub(r"[^A-Za-z0-9/_-]", "", tok[1:])
            if name:
                out.append("tags:" + name)
            continue
        tok = tok.strip('[]"')
        out.append('"' + tok.replace('"', '""') + '"')
    return " ".join(t for t in out if t and t != '""')


def _sha1(b: bytes) -> str:
    import hashlib
    return hashlib.sha1(b).hexdigest()


class Index:
    def __init__(self) -> None:
        self._db_path = hw_store.data_dir() / "index" / f"{hw_store.vault_hash()}.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self._db_path, check_same_thread=False)
        self._con.executescript("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;")
        try:
            self._con.executescript(_SCHEMA)
            self._con.execute("SELECT count(*) FROM notes")  # integrity probe
        except sqlite3.DatabaseError:
            self._con.close()
            self._db_path.rename(
                self._db_path.with_suffix(f".corrupt-{int(time.time())}"))
            self._con = sqlite3.connect(self._db_path, check_same_thread=False)
            self._con.executescript("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;")
            self._con.executescript(_SCHEMA)
        vp = hw_store.vault_path()
        new_vp = str(vp.resolve()) if vp else ""
        old = self._con.execute("SELECT v FROM meta WHERE k='vault_path'").fetchone()
        if old and old[0] and old[0] != new_vp:  # moved db or stale hash collision
            self._con.execute("DELETE FROM notes")
            self._con.execute("DELETE FROM files")
        self._meta_set("vault_path", new_vp)
        self._indexing = False
        self._last_scan_ns = 0

    def _meta_set(self, k: str, v: str) -> None:
        self._con.execute("INSERT INTO meta(k,v) VALUES(?,?) "
                          "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
        self._con.commit()

    def _walk(self):
        vp = hw_store.vault_path()
        max_bytes = hw_store.get_config()["max_file_kb"] * 1024
        for root, dirs, files in os.walk(vp, followlinks=False):
            dirs[:] = [d for d in dirs
                       if not d.startswith(".") and d not in _SKIP_DIRS]
            for fn in files:
                if not fn.lower().endswith(".md"):
                    continue
                full = os.path.join(root, fn)
                if os.path.islink(full):
                    continue
                rel = os.path.relpath(full, vp).replace(os.sep, "/")
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                yield rel, full, st, max_bytes

    def _index_one(self, rel: str, full: str, st: os.stat_result, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace")
        stem = os.path.splitext(os.path.basename(rel))[0]
        parsed = hw_notes.parse_note(text, stem)
        self._con.execute("DELETE FROM notes WHERE path=?", (rel,))
        self._con.execute(
            "INSERT INTO notes(path,title,headings,tags,links,frontmatter,body) "
            "VALUES(?,?,?,?,?,?,?)",
            (rel, parsed["title"], parsed["headings"], parsed["tags"],
             parsed["links"], parsed["frontmatter"], parsed["body"]))
        self._con.execute(
            "INSERT INTO files(path,mtime_ns,size,sha1,title,indexed_at) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns,"
            "size=excluded.size,sha1=excluded.sha1,title=excluded.title,"
            "indexed_at=excluded.indexed_at",
            (rel, st.st_mtime_ns, st.st_size, _sha1(raw), parsed["title"],
             int(time.time())))

    def sync(self, full: bool = False) -> dict:
        vp = hw_store.vault_path()
        if not vp or not vp.is_dir():
            return {"indexed": 0, "removed": 0, "took_ms": 0,
                    "error": "vault_not_found"}
        t0 = time.time()
        self._indexing = True
        try:
            if full:
                self._con.execute("DELETE FROM notes")
                self._con.execute("DELETE FROM files")
            have = {r[0]: (r[1], r[2], r[3]) for r in
                    self._con.execute("SELECT path,mtime_ns,size,sha1 FROM files")}
            seen, changed = set(), 0
            for i, (rel, full_p, st, mb) in enumerate(self._walk()):
                seen.add(rel)
                prev = have.get(rel)
                if prev and prev[0] == st.st_mtime_ns and prev[1] == st.st_size:
                    continue
                raw = open(full_p, "rb").read(mb + 1)[:mb]
                if prev and prev[2] == _sha1(raw):
                    # stat touched but bytes unchanged — refresh stat, skip reparse
                    self._con.execute(
                        "UPDATE files SET mtime_ns=?, size=? WHERE path=?",
                        (st.st_mtime_ns, st.st_size, rel))
                    continue
                self._index_one(rel, full_p, st, raw)
                changed += 1
                if i % 500 == 0:
                    self._con.commit()
            removed = 0
            for gone in set(have) - seen:
                self._con.execute("DELETE FROM notes WHERE path=?", (gone,))
                self._con.execute("DELETE FROM files WHERE path=?", (gone,))
                removed += 1
            self._con.commit()
            self._last_scan_ns = time.time_ns()
            self._meta_set("last_scan_ns", str(self._last_scan_ns))
            return {"indexed": changed, "removed": removed,
                    "took_ms": int((time.time() - t0) * 1000)}
        finally:
            self._indexing = False

    def _maybe_sync(self) -> None:
        if time.time_ns() - self._last_scan_ns > _DEBOUNCE_NS:
            self.sync()

    def search(self, query: str, limit: int) -> list[dict]:
        vp = hw_store.vault_path()
        if not vp or not vp.is_dir():
            return []
        self._maybe_sync()
        q = sanitize_fts_query(query)
        if not q:
            return []
        try:
            rows = self._con.execute(
                "SELECT path, title, "
                "  bm25(notes, 0.0, 10.0, 4.0, 6.0, 8.0, 2.0, 1.0) AS rank, "
                "  snippet(notes, 6, '<b>', '</b>', ' … ', 12) AS ex "
                "FROM notes WHERE notes MATCH ? ORDER BY rank LIMIT ?",
                (q, limit)).fetchall()
        except sqlite3.OperationalError:
            return []  # sanitiser slipped a syntax error through
        out = []
        for path, title, rank, ex in rows:
            if not ex:
                body = self._con.execute("SELECT body FROM notes WHERE path=?",
                                         (path,)).fetchone()
                ex = (body[0][:200] + " … ") if body and body[0] else ""
            out.append({"path": path, "title": title or path,
                        "score": -float(rank), "excerpt": ex})
        return out

    def status(self) -> dict:
        n = self._con.execute("SELECT count(*) FROM files").fetchone()[0]
        return {"note_count": n, "indexed_count": n, "indexing": self._indexing,
                "last_scan_ts": self._last_scan_ns // 1_000_000_000}

    def rebuild(self) -> dict:
        return self.sync(full=True)


_INDEX: "Index | None" = None


def get_index() -> "Index":
    global _INDEX
    vh = hw_store.vault_hash()
    if _INDEX is None or _INDEX._db_path.stem != vh:
        _INDEX = Index()
    return _INDEX


def reset_for_tests() -> None:
    global _INDEX
    if _INDEX is not None:
        _INDEX._con.close()
    _INDEX = None


def _selfcheck() -> None:
    import tempfile

    saved_home = os.environ.get("HERMES_HOME")
    try:
        # ignore_cleanup_errors: Windows may not release the sqlite file handle
        # in the microsecond between reset_for_tests() and rmtree.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            hw_store._cache = None
            vault = os.path.join(d, "vault")
            for sub in ("Areas", "People", "Topics", ".obsidian"):
                os.makedirs(os.path.join(vault, sub))
            open(os.path.join(vault, "Areas", "Argos.md"), "w",
                 encoding="utf-8").write(
                "# Argos\n\nA desktop widget engine. #project\n\n## History\n\n"
                "- **2026-08-20** — Component 1 merged.\n")
            open(os.path.join(vault, "People", "Ada Lovelace.md"), "w",
                 encoding="utf-8").write(
                "# Ada Lovelace\n\nWrote the first algorithm. See [[Areas/Argos]].\n")
            # only tie to "argos" is the wikilink target -> exercises the links column
            open(os.path.join(vault, "Topics", "Linker.md"), "w",
                 encoding="utf-8").write("See [[Areas/Argos]].\n")
            open(os.path.join(vault, ".obsidian", "app.json"), "w").write("{}")
            # a .md inside a skipped dir: only excluded if the _walk dir filter runs
            open(os.path.join(vault, ".obsidian", "notes.md"), "w",
                 encoding="utf-8").write("# Layout\n\nvault directory layout notes.\n")
            open(os.path.join(vault, "latin1.md"), "wb").write(
                "# Café\n\nna\xefve bytes.".encode("latin-1"))
            hw_store.set_vault(vault)
            reset_for_tests()
            idx = get_index()
            idx.sync(full=True)

            assert idx.search("Argos widget engine", 5)[0]["path"] == "Areas/Argos.md"
            assert any(r["path"] == "Areas/Argos.md"
                       for r in idx.search("#project", 5))
            assert any(r["path"] == "People/Ada Lovelace.md"
                       for r in idx.search("[[Areas/Argos]]", 5)
                       + idx.search("Argos", 5))
            assert any(r["path"] == "Topics/Linker.md"
                       for r in idx.search("Argos", 9))  # matched via links column
            assert all(".obsidian" not in r["path"]
                       for r in idx.search("layout", 5))
            assert "<b>" in idx.search("algorithm", 5)[0]["excerpt"]
            idx.search("Café", 5)  # must not raise on the latin-1 note

            assert sanitize_fts_query('foo "bar AND baz*') == '"foo" "bar" "AND" "baz*"'
            assert sanitize_fts_query("#roadmap") == "tags:roadmap"
            assert sanitize_fts_query("[[Ada Lovelace]]") == '"Ada" "Lovelace"'
    finally:
        reset_for_tests()  # close the sqlite handle before temp-dir teardown
        hw_store._cache = None
        if saved_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved_home
    _selfcheck_incremental()


def _selfcheck_incremental() -> None:
    import tempfile

    saved_home = os.environ.get("HERMES_HOME")
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            hw_store._cache = None
            vault = os.path.join(d, "vault")
            os.makedirs(os.path.join(vault, "Topics"))
            a = os.path.join(vault, "Topics", "A.md")
            b = os.path.join(vault, "Topics", "B.md")
            open(a, "w", encoding="utf-8").write("# A\n\napple\n")
            open(b, "w", encoding="utf-8").write("# B\n\nbanana\n")
            hw_store.set_vault(vault)
            reset_for_tests()
            idx = get_index()
            idx.sync(full=True)

            calls = []
            real = hw_notes.parse_note
            hw_notes.parse_note = lambda t, s: calls.append(s) or real(t, s)
            try:
                time.sleep(0.01)
                open(a, "w", encoding="utf-8").write("# A\n\napricot\n")
                os.utime(a, None)
                idx._last_scan_ns = 0
                idx.sync()
                assert calls == ["A"], calls  # only the changed file reparsed
                assert idx.search("apricot", 5)[0]["path"] == "Topics/A.md"
                assert not idx.search("apple", 5)
            finally:
                hw_notes.parse_note = real

            os.remove(b)
            idx._last_scan_ns = 0
            idx.sync()
            assert not idx.search("banana", 5)

            try:
                os.symlink(a, os.path.join(vault, "Topics", "link.md"))
                idx._last_scan_ns = 0
                r = idx.sync()
                assert not any(x["path"].endswith("link.md") for x in idx.search("apricot", 9))
            except (OSError, NotImplementedError):
                # Windows without admin privilege cannot create symlinks
                pass
    finally:
        reset_for_tests()  # close the sqlite handle before temp-dir teardown
        hw_store._cache = None
        if saved_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved_home


if __name__ == "__main__":
    _selfcheck()
    print("ok")
