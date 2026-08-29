"""Resolve a memory to a vault note, render the line, splice it in, write it safely."""
import datetime
import difflib
import os
import re

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


if __name__ == "__main__":
    _selfcheck()
    print("ok")
