"""Resolve a memory to a vault note, render the line, splice it in, write it safely."""
import datetime
import difflib
import hashlib
import json
import os
import re
import shutil
import time

import hw_store

_FOLDER_HINT_RE = re.compile(r"^(People|Areas|Topics|Timeline)/", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def resolve_target(hint: str, index) -> dict:
    hint = hint.strip().strip("[]").replace("\\", "/")
    if ".." in hint.split("/") or re.match(r"^[A-Za-z]:", hint):
        hint = os.path.basename(hint)
    rel = hint if hint.endswith(".md") else hint + ".md"
    con = index._con

    row = con.execute("SELECT path FROM files WHERE path=? COLLATE NOCASE", (rel,)).fetchone()
    if row:
        return {"target_path": row[0], "action": "append",
                "resolved_from": "exact", "fuzzy_candidate": None}

    stem = os.path.splitext(os.path.basename(rel))[0]
    cands = con.execute(
        "SELECT path FROM notes WHERE title MATCH ? ",
        ('"' + stem.replace('"', '""') + '"',)).fetchall()
    exact = [p for (p,) in cands
             if os.path.splitext(os.path.basename(p))[0].lower() == stem.lower()]
    if exact:
        exact.sort(key=lambda p: (p.count("/"), p.lower()))
        return {"target_path": exact[0], "action": "append",
                "resolved_from": "title", "fuzzy_candidate": None}

    all_paths = [p for (p,) in con.execute("SELECT path FROM files")]
    scored = sorted(
        ((difflib.SequenceMatcher(None, _norm(stem),
          _norm(os.path.splitext(os.path.basename(p))[0])).ratio(), p) for p in all_paths),
        reverse=True)
    if scored and scored[0][0] >= 0.90 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05):
        return {"target_path": _create_path(hint, stem), "action": "create",
                "resolved_from": "miss", "fuzzy_candidate": scored[0][1]}

    return {"target_path": _create_path(hint, stem), "action": "create",
            "resolved_from": "miss", "fuzzy_candidate": None}


def _create_path(hint: str, stem: str) -> str:
    if hint.lower() == "profile.md" or stem.lower() == "profile":
        return "Profile.md"
    m = _FOLDER_HINT_RE.match(hint)
    folder = m.group(1) if m else "Topics"
    folder = {"people": "People", "areas": "Areas", "topics": "Topics",
              "timeline": "Timeline"}[folder.lower()]
    return f"{folder}/{stem}.md"


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LINE_RE = re.compile(r"^\s*-\s*\*\*(\d{4}-\d{2}-\d{2})\*\*\s*[—-]\s*(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s")


def _lines(text: str) -> list[str]:
    """Like str.splitlines() but only on \\r\\n / \\r / \\n (not U+2028/2029/\\x0c)."""
    out = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if out and out[-1] == "":
        out.pop()
    return out


def _valid_date(s: str, today: str) -> str:
    if DATE_RE.match(s):
        try:
            d = datetime.date.fromisoformat(s)
            if d <= datetime.date.fromisoformat(today) + datetime.timedelta(days=1):
                return s
        except ValueError:
            pass
    return today


def render_line(history_line: str, supersedes: str | None, today: str) -> str:
    raw = history_line.strip()
    m = _LINE_RE.match(raw)
    if m:
        date, prose = _valid_date(m.group(1), today), m.group(2)
    else:
        date, prose = today, re.sub(r"^-\s+", "", raw)
    prose = " ".join(prose.split())  # collapse hard wraps / tabs / space runs
    if len(prose) >= 2 and prose[0] == prose[-1] == '"':  # strip one wrapping pair only
        prose = prose[1:-1].strip()
    prose = re.sub(r"\s*\*\(supersedes:.*?\)\*\s*$", "", prose).strip()
    if not prose.endswith((".", "!", "?")):
        prose += "."
    line = f"- **{date}** — {prose}"
    if supersedes:
        claim = " ".join(supersedes.split()).replace('"', "'")
        line += f' *(supersedes: "{claim}")*'
    return line


def _is_fence(ln: str) -> bool:
    return ln.startswith("```") or ln.startswith("~~~")


def _find_top_level_eof_insert(lines: list[str]) -> int:
    """Insert index near EOF, before any trailing code fence / callout / `---` footer."""
    inside, f = [], False
    for ln in lines:
        if _is_fence(ln):
            inside.append(True)
            f = not f
        else:
            inside.append(f)
    # trailing `---` footer (rule + body text, no heading after it): insert before the rule
    # ponytail: assumes no YAML frontmatter (system contract); a top `---\n...\n---` is not handled
    rule = None
    for k in range(len(lines) - 1, -1, -1):
        if _HEADING_RE.match(lines[k]):
            break
        if lines[k].strip() == "---" and not inside[k]:
            rule = k
            break
    if rule is not None and rule > 0:
        while rule > 0 and lines[rule - 1].strip() == "":
            rule -= 1
        return rule
    i = len(lines)
    while i > 0 and (lines[i - 1].strip() in ("", "---")
                     or lines[i - 1].startswith(">") or inside[i - 1]):
        i -= 1
    return i


def insert_history_line(text: str, line: str) -> tuple[str, bool]:
    lines = _lines(text)
    hist = None
    for i, ln in enumerate(lines):
        if _HEADING_RE.match(ln):
            parts = ln.split(None, 1)
            if len(parts) > 1 and parts[1].strip().lower() == "history":
                hist = i
                break
    if hist is None:
        insert_at = _find_top_level_eof_insert(lines)
        block = ["", "## History", "", line]
        lines[insert_at:insert_at] = block
        return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), True

    end = len(lines)
    for j in range(hist + 1, len(lines)):
        if _HEADING_RE.match(lines[j]):
            end = j
            break
    last_bullet = hist
    for j in range(hist + 1, end):
        if lines[j].lstrip().startswith(("-", "*")):
            last_bullet = j
    lines.insert(last_bullet + 1, line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), False


def insert_timeline_line(text: str, line: str) -> str:
    lines = _lines(text)
    for i, ln in enumerate(lines):
        if _LINE_RE.match(ln):  # first dated bullet
            lines.insert(i, line)
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return text.rstrip() + "\n\n" + line + "\n"


def new_note_body(stem: str, line: str) -> str:
    prose = line.split(" — ", 1)[1] if " — " in line else line.lstrip("-").strip()
    return f"# {stem}\n\n{prose}\n\n## History\n\n{line}\n"


def _selfcheck() -> None:
    import tempfile
    import hw_store
    import hw_index

    saved_home = os.environ.get("HERMES_HOME")
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            hw_store._cache = None
            vault = os.path.join(d, "vault")
            for sub in ("Areas", "People", "Topics"):
                os.makedirs(os.path.join(vault, sub))
            open(os.path.join(vault, "Areas", "Argos.md"), "w",
                 encoding="utf-8").write("# Argos\n")
            open(os.path.join(vault, "People", "Ada Lovelace.md"), "w",
                 encoding="utf-8").write("# Ada Lovelace\n")
            hw_store.set_vault(vault)
            hw_index.reset_for_tests()
            idx = hw_index.get_index()
            idx.sync(full=True)

            assert resolve_target("Areas/Argos.md", idx)["resolved_from"] == "exact"
            r = resolve_target("Argos", idx)
            assert r["target_path"] == "Areas/Argos.md" and r["resolved_from"] == "title"
            r = resolve_target("Ada Lovlace", idx)  # typo, fuzzy
            assert r["action"] == "create" and r["fuzzy_candidate"] == "People/Ada Lovelace.md"
            r = resolve_target("Build Tooling", idx)
            assert r == {"target_path": "Topics/Build Tooling.md", "action": "create",
                         "resolved_from": "miss", "fuzzy_candidate": None}
            assert resolve_target("../../etc/passwd", idx)["target_path"] == "Topics/passwd.md"
    finally:
        hw_index.reset_for_tests()
        hw_store._cache = None
        if saved_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved_home
    _selfcheck_render()
    _selfcheck_write()
    _selfcheck_dedup()


def _selfcheck_render() -> None:
    assert render_line("migrated to X", None, "2026-08-30") == "- **2026-08-30** — migrated to X."
    assert render_line('- **2026-01-02** — "quoted claim"', None, "2026-08-30") \
        == "- **2026-01-02** — quoted claim."
    assert render_line("did a thing", "old thing", "2026-08-30") \
        == '- **2026-08-30** — did a thing. *(supersedes: "old thing")*'
    assert render_line("- **2099-01-01** — future", None, "2026-08-30") \
        .startswith("- **2026-08-30**")  # implausible future date -> today
    assert "<!--" not in render_line("x", None, "2026-08-30")
    # fix 1: hard-wrapped model claim collapses to one physical line
    assert render_line("line one\nline two", None, "2026-08-30") \
        == "- **2026-08-30** — line one line two."
    # fix 2: leading hyphen in prose (e.g. a negative number) is preserved
    assert render_line("-5C drop today", None, "2026-08-30") \
        == "- **2026-08-30** — -5C drop today."
    # bundle: nested quote in supersedes claim is neutralised
    assert render_line("did x", 'the "old" way', "2026-08-30") \
        == '- **2026-08-30** — did x. *(supersedes: "the \'old\' way")*'
    # bundle: only ONE wrapping quote pair stripped (not strip('"') which eats both)
    assert render_line('""already quoted""', None, "2026-08-30") \
        == '- **2026-08-30** — "already quoted".'

    t, created = insert_history_line("# A\n\nprose\n", "- **2026-08-30** — new.")
    assert created and t.endswith("## History\n\n- **2026-08-30** — new.\n")

    t, created = insert_history_line(
        "# A\n\np\n\n## History\n\n- **2026-08-01** — old.\n\n## Notes\n\nn\n",
        "- **2026-08-30** — new.")
    assert not created
    assert t.index("- **2026-08-30** — new.") < t.index("## Notes")
    assert t.index("- **2026-08-01** — old.") < t.index("- **2026-08-30** — new.")

    t, _ = insert_history_line("# A\n\np\n\n```\ncode\n```\n", "- **2026-08-30** — x.")
    assert t.index("- **2026-08-30** — x.") < t.index("```\ncode")

    # fix 3: `## History` goes ABOVE a trailing `---` footer that has body text
    t, _ = insert_history_line("# A\n\nbody\n\n---\n\nSee also [[X]]\n", "- **2026-08-30** — x.")
    assert t.index("- **2026-08-30** — x.") < t.index("---")

    tl = insert_timeline_line("# Timeline 2026\n\nintro\n\n- **2026-08-01** — old.\n",
                              "- **2026-08-30** — new.")
    assert tl.index("- **2026-08-30** — new.") < tl.index("- **2026-08-01** — old.")
    # fix 4: only DATED bullets count; `---` block and plain `- ` lines are skipped
    tl = insert_timeline_line("---\ntag: x\n---\n\n- **2026-08-01** — old.\n",
                              "- **2026-08-30** — new.")
    assert tl.startswith("---\ntag: x\n---\n")
    assert tl.index("- **2026-08-30** — new.\n- **2026-08-01** — old.") > 0

    assert new_note_body("Foo", "- **2026-08-30** — a fact.") == \
        "# Foo\n\na fact.\n\n## History\n\n- **2026-08-30** — a fact.\n"


# --- write safety: sha, atomic write, backup, journal, undo ------------------
#
# LF-space contract: every sha in this pipeline is computed over LF-normalized,
# BOM-inclusive UTF-8 bytes -- the same basis the Task 12 caller gets from
# pathlib.Path(p).read_text("utf-8").encode("utf-8"). Raw CRLF bytes are only
# ever used to detect the on-disk EOL and BOM so the write round-trips them.

_BOM = b"\xef\xbb\xbf"


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _lf(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def atomic_write(abspath: str, new_text: str, pre_sha: str | None) -> dict:
    """Write new_text to abspath atomically. new_text arrives LF-normalized.

    Returns {"status": "written"|"conflict"|"error", "detail", "sha_after"}.
    On conflict/error nothing is written. sha_after is over the LF-normalized,
    BOM-inclusive new content (journal-basis, not the CRLF bytes on disk).
    """
    exists = os.path.exists(abspath)
    eol, bom = "\n", b""
    if exists:
        try:
            cur = open(abspath, "rb").read()
        except OSError as e:
            return {"status": "error", "detail": str(e), "sha_after": None}
        if pre_sha is not None and sha256(_lf(cur)) != pre_sha:
            return {"status": "conflict", "detail": "file changed since preview",
                    "sha_after": None}
        try:
            cur.decode("utf-8")
        except UnicodeDecodeError:
            try:
                cur.decode("utf-8-sig")
            except UnicodeDecodeError:
                return {"status": "error", "detail": "not UTF-8, edit manually",
                        "sha_after": None}
        eol = "\r\n" if b"\r\n" in cur else "\n"
        bom = _BOM if cur.startswith(_BOM) else b""

    payload = new_text.replace("\r\n", "\n").replace("\r", "\n")
    if payload.startswith("\ufeff"):
        payload = payload[1:]
    sha_after = sha256(bom + payload.encode("utf-8"))
    disk = payload.replace("\n", "\r\n") if eol == "\r\n" else payload
    data = bom + disk.encode("utf-8")

    tmp = f"{abspath}.hw-{os.getpid()}.tmp"
    for attempt in (1, 2):
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, abspath)
            return {"status": "written", "detail": "", "sha_after": sha_after}
        except PermissionError as e:
            _safe_unlink(tmp)
            if attempt == 1:
                time.sleep(0.15)
                continue
            return {"status": "error", "detail": f"permission denied: {e}",
                    "sha_after": None}
        except OSError as e:
            _safe_unlink(tmp)
            return {"status": "error", "detail": str(e), "sha_after": None}


def _backup_dir():
    return hw_store.data_dir() / "backups" / hw_store.vault_hash()


def backup(abspath: str) -> None:
    """Copy abspath to data_dir/backups/<vault_hash>/<relpath>.<stamp>.bak.

    Keeps the last 20 backups per relpath; prunes any .bak whose filename
    stamp is older than 30 days.
    """
    vp = hw_store.vault_path()
    rel = os.path.relpath(abspath, str(vp)).replace(os.sep, "/")
    name = os.path.basename(rel)
    dest_dir = _backup_dir() / os.path.dirname(rel)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(abspath, dest_dir / f"{name}.{stamp}.bak")
    for old in sorted(dest_dir.glob(f"{name}.*.bak"))[:-20]:
        _safe_unlink(str(old))
    # prune by the stamp in the filename, NOT st_mtime -- copy2 copies the source
    # note's mtime onto the .bak, so an mtime prune would nuke a fresh backup of
    # any note last edited >30 days ago (i.e. most of them).
    cutoff = datetime.datetime.now() - datetime.timedelta(days=30)
    for f in _backup_dir().rglob("*.bak"):
        m = re.search(r"\.(\d{8}-\d{6})\.bak$", f.name)
        if not m:
            continue
        try:
            when = datetime.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
        except ValueError:
            continue
        if when < cutoff:
            _safe_unlink(str(f))


def _journal_path():
    d = hw_store.vault_path() / ".hermes"
    d.mkdir(parents=True, exist_ok=True)
    return d / "journal.json"


def _read_journal() -> list[dict]:
    p = _journal_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_journal(log: list[dict]) -> None:
    p = _journal_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(log, indent=2), "utf-8")
    os.replace(tmp, p)


def journal_append(batch_id: str, items: list[dict]) -> None:
    log = _read_journal()
    log.append({"ts": time.time(), "batch_id": batch_id,
                "vault": str(hw_store.vault_path()), "items": items})
    _write_journal(log[-500:])


def journal_seen(session_id: str, candidate_index: int) -> bool:
    for batch in _read_journal():
        for it in batch.get("items", []):
            if it.get("source_session_id") == session_id and \
               it.get("candidate_index") == candidate_index:
                return True
    return False


def undo(batch_id: str | None) -> list[dict]:
    """Undo one batch (latest if batch_id is None). One level deep -- no redo,
    and undoing consumes the .bak it restores from."""
    log = _read_journal()
    if not log:
        return []
    if batch_id is None:
        batch = log[-1]
    else:
        batch = next((b for b in reversed(log) if b.get("batch_id") == batch_id), None)
    if batch is None:
        return []

    vp = hw_store.vault_path()
    restored: set[str] = set()  # paths already reverted whole from a .bak
    results: list[dict] = []
    for it in batch.get("items", []):
        relp = it.get("path")
        line = it.get("line", "")
        if not relp:
            continue
        abspath = str(vp / relp)
        if relp in restored:  # an earlier item already restored the whole note
            results.append({"path": relp, "result": "restored"})
            continue
        bdir = _backup_dir() / os.path.dirname(relp)
        baks = sorted(bdir.glob(f"{os.path.basename(relp)}.*.bak"))
        if baks:
            try:
                # shutil.move (not os.replace): backups live under HERMES_HOME,
                # the vault may be on another drive -> os.replace can't cross it.
                shutil.move(str(baks[-1]), abspath)
                restored.add(relp)
                results.append({"path": relp, "result": "restored"})
                continue
            except OSError:
                pass  # fall through to line removal
        try:
            raw = open(abspath, "rb").read()
        except OSError:
            results.append({"path": relp, "result": "skipped",
                            "detail": "unreadable", "line": line})
            continue
        marker = line + "\n"
        text = _lf(raw).decode("utf-8", "replace")
        if sha256(_lf(raw)) == it.get("sha_after") and marker in text:
            # hash verified -> reuse atomic_write for the atomic, EOL/BOM-safe rewrite
            r = atomic_write(abspath, text.replace(marker, "", 1), it.get("sha_after"))
            if r["status"] == "written":
                results.append({"path": relp, "result": "removed"})
            else:
                results.append({"path": relp, "result": "skipped",
                                "detail": r["detail"], "line": line})
        else:
            results.append({"path": relp, "result": "skipped",
                            "detail": "changed since write; remove manually",
                            "line": line})

    if batch_id is None:
        log.pop()
    else:
        log.remove(batch)
    _write_journal(log)
    return results


def _selfcheck_write() -> None:
    import tempfile
    import pathlib
    import stat

    saved_home = os.environ.get("HERMES_HOME")
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            hw_store._cache = None
            vault = os.path.join(d, "vault")
            os.makedirs(os.path.join(vault, "Areas"))
            hw_store.set_vault(vault)

            p = os.path.join(vault, "Areas", "X.md")
            open(p, "wb").write(
                "# X\r\n\r\n## History\r\n\r\n- **2026-08-01** — a.\r\n".encode("utf-8"))
            # pre_sha basis == Task 12 caller: read_text("utf-8") then encode
            pre = sha256(pathlib.Path(p).read_text("utf-8").encode("utf-8"))

            backup(p)
            bak_dir = hw_store.data_dir() / "backups" / hw_store.vault_hash() / "Areas"
            assert list(bak_dir.glob("X.md.*.bak")), "backup .bak missing"

            # a note last touched >30 days ago must still get a durable backup
            # (copy2 stamps the .bak with the note's mtime -> an mtime prune kills it)
            old_note = os.path.join(vault, "Areas", "Old.md")
            open(old_note, "wb").write(b"# Old\n\nstale.\n")
            long_ago = time.time() - 40 * 86400
            os.utime(old_note, (long_ago, long_ago))
            backup(old_note)
            assert list(bak_dir.glob("Old.md.*.bak")), "backup of >30d-old note was pruned"

            before = pathlib.Path(p).read_text("utf-8")  # LF-normalized
            new_text = before.replace(
                "- **2026-08-01** — a.\n",
                "- **2026-08-01** — a.\n- **2026-08-30** — b.\n")
            r = atomic_write(p, new_text, pre)
            assert r["status"] == "written", r
            assert b"\r\n" in open(p, "rb").read(), "CRLF EOL not preserved"
            assert r["sha_after"] == sha256(
                pathlib.Path(p).read_text("utf-8").encode("utf-8")), "sha_after basis wrong"

            # conflict: stale pre_sha writes nothing
            snap = open(p, "rb").read()
            assert atomic_write(p, "whatever", "deadbeef")["status"] == "conflict"
            assert open(p, "rb").read() == snap, "conflict must not touch the file"

            # os.replace blows up -> error, original intact, tmp cleaned
            good = sha256(pathlib.Path(p).read_text("utf-8").encode("utf-8"))
            real = os.replace
            os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
            try:
                e = atomic_write(p, "brand new\n", good)
            finally:
                os.replace = real
            assert e["status"] == "error", e
            assert open(p, "rb").read() == snap, "failed replace changed the original"
            assert not list(pathlib.Path(vault, "Areas").glob("X.md.hw-*.tmp")), "tmp leaked"

            # non-UTF-8 target -> error, nothing written
            bad = os.path.join(vault, "Areas", "Bad.md")
            open(bad, "wb").write("# café".encode("latin-1"))
            bad_bytes = open(bad, "rb").read()
            assert atomic_write(bad, "x", None)["status"] == "error"
            assert open(bad, "rb").read() == bad_bytes

            # read-only file -> error (skip cleanly if the runner ignores the flag)
            ro = os.path.join(vault, "Areas", "RO.md")
            open(ro, "wb").write(b"ro\n")
            ro_pre = sha256(pathlib.Path(ro).read_text("utf-8").encode("utf-8"))
            os.chmod(ro, stat.S_IREAD)
            try:
                ro_r = atomic_write(ro, "changed\n", ro_pre)
                if ro_r["status"] == "error":
                    assert open(ro, "rb").read() == b"ro\n"
                else:
                    print("  _selfcheck_write: read-only flag not enforced here, skipped")
            finally:
                os.chmod(ro, stat.S_IWRITE | stat.S_IREAD)

            # BOM + CRLF fixture: BOM preserved, sha_after on the plain-utf-8 basis
            bp = os.path.join(vault, "Areas", "Bom.md")
            open(bp, "wb").write(
                "\ufeff# T\r\n\r\n## History\r\n\r\n- **2026-08-01** — a.\r\n".encode("utf-8"))
            bpre = sha256(pathlib.Path(bp).read_text("utf-8").encode("utf-8"))
            bbefore = pathlib.Path(bp).read_text("utf-8")
            br = atomic_write(bp, bbefore.replace("— a.\n", "— a.\n- **2026-08-30** — b.\n"), bpre)
            assert br["status"] == "written", br
            braw = open(bp, "rb").read()
            assert braw.startswith(b"\xef\xbb\xbf") and braw.count(b"\xef\xbb\xbf") == 1, "BOM"
            assert b"\r\n" in braw, "BOM fixture EOL"
            assert br["sha_after"] == sha256(
                pathlib.Path(bp).read_text("utf-8").encode("utf-8")), "BOM sha_after basis"

            # journal + dedupe
            journal_append("batch-1", [{"path": "Areas/X.md", "sha_before": pre,
                                        "sha_after": r["sha_after"],
                                        "line": "- **2026-08-30** — b.",
                                        "source_session_id": "s1", "candidate_index": 0}])
            assert journal_seen("s1", 0) and not journal_seen("s1", 1)
            assert _read_journal()[-1]["batch_id"] == "batch-1"

            # undo with a .bak present -> whole-note restore
            res = undo("batch-1")
            assert res and res[0]["result"] == "restored", res
            assert "- **2026-08-30** — b." not in pathlib.Path(p).read_text("utf-8")
            assert b"\r\n" in open(p, "rb").read(), "restore lost CRLF"
            assert _read_journal() == []

            # undo with NO .bak -> exact-line removal via atomic_write
            for stale in bak_dir.glob("X.md.*.bak"):
                stale.unlink()
            r2 = atomic_write(p, pathlib.Path(p).read_text("utf-8").replace(
                "— a.\n", "— a.\n- **2026-08-31** — c.\n"),
                sha256(pathlib.Path(p).read_text("utf-8").encode("utf-8")))
            assert r2["status"] == "written"
            journal_append("batch-2", [{"path": "Areas/X.md",
                                        "sha_after": r2["sha_after"],
                                        "line": "- **2026-08-31** — c.",
                                        "source_session_id": "s2", "candidate_index": 0}])
            res2 = undo("batch-2")
            assert res2 and res2[0]["result"] == "removed", res2
            final = pathlib.Path(p).read_text("utf-8")
            assert "- **2026-08-31** — c." not in final
            assert "- **2026-08-01** — a." in final and final.endswith("\n"), final
            assert b"\r\n" in open(p, "rb").read(), "line-removal lost CRLF"
    finally:
        hw_store._cache = None
        if saved_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved_home


# --- content-based dedup ----------------------------------------------------


def _line_prose(line: str) -> str:
    m = _LINE_RE.match(line.strip())
    body = m.group(2) if m else line.lstrip("-").strip()
    body = re.sub(r"\*\(supersedes:.*?\)\*", "", body)
    return _norm(body)


def dedup_entry(line, target_text, session_id, candidate_index, index, is_timeline=False):
    if journal_seen(session_id, candidate_index):
        return {"duplicate": True, "reason": "already_written",
                "colliding_line": None, "warning": None}
    want = _line_prose(line)
    if not want:
        return {"duplicate": False, "reason": "new", "colliding_line": None, "warning": None}
    for existing in target_text.splitlines():
        if not existing.lstrip().startswith(("-", "*")):
            continue
        if difflib.SequenceMatcher(None, want, _line_prose(existing)).ratio() >= 0.90:
            return {"duplicate": True, "reason": "near_dup",
                    "colliding_line": existing.strip(), "warning": None}
    warning = None
    if not is_timeline:
        for hit in index.search(want, 3):
            body = index._con.execute("SELECT body FROM notes WHERE path=?",
                                      (hit["path"],)).fetchone()
            if body and any(
                difflib.SequenceMatcher(None, want, _line_prose(b)).ratio() >= 0.90
                for b in body[0].splitlines() if b.lstrip().startswith(("-", "*"))):
                warning = f"similar text already in {hit['path']}"
                break
    return {"duplicate": False, "reason": "new", "colliding_line": None, "warning": warning}


def _selfcheck_dedup() -> None:
    import tempfile
    import hw_store
    import hw_index

    saved_home = os.environ.get("HERMES_HOME")
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            hw_store._cache = None
            vault = os.path.join(d, "vault")
            os.makedirs(os.path.join(vault, "Areas"))
            open(os.path.join(vault, "Areas", "X.md"), "w",
                 encoding="utf-8").write("# X\nprose\n")
            open(os.path.join(vault, "Areas", "A.md"), "w", encoding="utf-8").write(
                "# A\n\n## History\n\n- **2026-08-15** — The user deployed Argos to staging.\n")
            hw_store.set_vault(vault)
            hw_index.reset_for_tests()
            idx = hw_index.get_index()
            idx.sync(full=True)

            target = "## History\n\n- **2026-08-01** — the user prefers tabs over spaces.\n"

            # exact normalized dup of an existing bullet -> near_dup, names the line
            r = dedup_entry("- **2026-08-30** — The user prefers tabs over spaces.",
                            target, "s9", 0, idx)
            assert r["duplicate"] and r["reason"] == "near_dup"
            assert r["colliding_line"] == "- **2026-08-01** — the user prefers tabs over spaces."

            # a genuinely different statement -> new
            r = dedup_entry("- **2026-08-30** — the user switched editors to Helix.",
                            target, "s9", 1, idx)
            assert not r["duplicate"] and r["reason"] == "new" and r["colliding_line"] is None

            # cross-note: prose near-identical to a bullet in another note (NOT in
            # target_text) -> not a dup, but warns and names that note
            r = dedup_entry("- **2026-08-30** — the user deployed Argos to staging.",
                            target, "s9", 10, idx)
            assert r["duplicate"] is False and r["reason"] == "new"
            assert r["warning"] and "Areas/A.md" in r["warning"]

            # candidate that normalizes to empty prose -> new, never a spurious near_dup
            r = dedup_entry("---", "## History\n\n---\n", "s9", 11, idx)
            assert r["reason"] == "new" and r["colliding_line"] is None

            # (session_id, candidate_index) already in the journal -> already_written
            journal_append("b", [{"path": "Areas/X.md", "sha_before": "", "sha_after": "",
                                  "line": "x", "source_session_id": "s9",
                                  "candidate_index": 2}])
            r = dedup_entry("anything", target, "s9", 2, idx)
            assert r["duplicate"] and r["reason"] == "already_written"
            # a different index the journal has NOT seen still evaluates normally
            assert dedup_entry("anything new", target, "s9", 3, idx)["reason"] == "new"
    finally:
        hw_index.reset_for_tests()
        hw_store._cache = None
        if saved_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved_home


if __name__ == "__main__":
    _selfcheck()
    print("ok")
