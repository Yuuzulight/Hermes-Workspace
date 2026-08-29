"""Resolve a memory to a vault note, render the line, splice it in, write it safely."""
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


if __name__ == "__main__":
    _selfcheck()
    print("ok")
