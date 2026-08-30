"""Run every module self-check plus the full HTTP read + write + reversible round-trip. Usage: python selftest.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

MODULES = ["hw_store", "hw_notes", "hw_index", "hw_context", "hw_merge", "hw_extract"]  # grows each task


def run_module_checks() -> None:
    for name in MODULES:
        mod = __import__(name)
        if hasattr(mod, "_selfcheck"):
            mod._selfcheck()
            print(f"  {name}._selfcheck ok")


def _client():
    import warnings
    from fastapi import FastAPI
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Using `httpx` with", category=UserWarning)
        from fastapi.testclient import TestClient
    import plugin_api
    app = FastAPI()
    app.include_router(plugin_api.router)
    return TestClient(app)


def _selfcheck_http_read() -> None:
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
            open(os.path.join(vault, "Areas", "Argos.md"), "w", encoding="utf-8").write(
                "# Argos\n\nwidget engine\n\n## History\n\n- **2026-08-20** — merged.\n")

            hw_index.reset_for_tests()
            c = _client()
            # no vault configured yet: /status must not construct an Index
            assert c.get("/status").status_code == 200
            assert hw_index._INDEX is None

            assert c.post("/config", json={"vault": vault}).json()["vault_exists"]
            c.post("/reindex", json={"full": True})
            assert c.post("/search", json={"query": "widget engine"}).json()["results"][0]["path"] \
                == "Areas/Argos.md"
            assert "Areas" in c.get("/tree").json()["dirs"]
            assert c.get("/note", params={"path": "Areas/Argos.md"}).json()["markdown"].startswith("# Argos")
            assert c.get("/note", params={"path": "../x"}).status_code == 400
            assert c.get("/tree", params={"path": "../x"}).status_code == 400
            assert c.get("/note", params={"path": "Areas/Nope.md"}).status_code == 404
    finally:
        hw_index.reset_for_tests()
        hw_store._cache = None
        if saved_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved_home
    print("  http read round-trip ok")


def _selfcheck_http_write() -> None:
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
            argos = os.path.join(vault, "Areas", "Argos.md")
            open(argos, "w", encoding="utf-8").write(
                "# Argos\n\nwidget engine\n\n## History\n\n- **2026-08-20** — merged.\n")
            original = open(argos, "rb").read()

            hw_index.reset_for_tests()
            c = _client()
            assert c.post("/config", json={"vault": vault}).json()["vault_exists"]
            c.post("/reindex", json={"full": True})

            cands = [{"target": "Argos", "history_line": "Component 4 shipped.",
                      "supersedes": None, "quote": "we shipped component 4"}]
            resolved = c.post("/extract/resolve",
                              json={"candidates": cands, "source_session_id": "sess-1"}).json()
            assert resolved["candidates"][0]["target_path"] == "Areas/Argos.md"

            items = [{"target_path": "Areas/Argos.md", "history_line": "Component 4 shipped.",
                      "supersedes": None, "candidate_index": 0}]
            prev = c.post("/memories/preview", json={"items": items}).json()
            assert "Component 4 shipped." in prev[0]["diff"]
            assert open(argos, "rb").read() == original  # preview writes nothing

            # a hostile target_path is refused before touching the filesystem
            assert c.post("/memories/preview",
                          json={"items": [{"target_path": "../evil.md",
                                           "history_line": "x."}]}).status_code == 400

            items[0]["pre_sha"] = prev[0]["pre_sha"]
            commit = c.post("/memories/commit",
                            json={"items": items, "source_session_id": "sess-1"}).json()
            assert commit[0]["status"] == "written", commit
            assert "Component 4 shipped." in open(argos, encoding="utf-8").read()
            batch = commit[0]["batch_id"]

            undo = c.post("/memories/undo", json={"batch_id": batch}).json()
            assert undo[0]["result"] in ("restored", "removed"), undo
            assert open(argos, "rb").read() == original  # fully reversible
    finally:
        hw_index.reset_for_tests()
        hw_store._cache = None
        if saved_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved_home
    print("  http write round-trip ok")


def _selfcheck_http_write_edges() -> None:
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
            a = os.path.join(vault, "Areas", "A.md")
            b = os.path.join(vault, "Areas", "B.md")
            open(a, "w", encoding="utf-8").write("# A\n\n## History\n\n- **2026-08-01** — one.\n")
            open(b, "w", encoding="utf-8").write("# B\n\n## History\n\n- **2026-08-01** — one.\n")

            hw_index.reset_for_tests()
            c = _client()
            c.post("/config", json={"vault": vault})
            c.post("/reindex", json={"full": True})

            # /extract/prepare flattens list-style content blocks
            prep = c.post("/extract/prepare", json={"messages": [
                {"role": "user", "content": [{"type": "text", "text": "hello"},
                                             {"type": "text", "text": "world"}]},
                {"role": "assistant", "content": "ok"}]}).json()
            tt = prep["transcript_text"]
            assert "hello world" in tt and "ok" in tt and "[object" not in tt and "{'" not in tt

            # 3-item batch: item 2 has a wrong pre_sha -> conflict, 1 & 3 still written
            items = [
                {"target_path": "Areas/A.md", "history_line": "alpha added.", "candidate_index": 0},
                {"target_path": "Areas/B.md", "history_line": "beta added.",
                 "candidate_index": 1, "pre_sha": "deadbeef"},
                {"target_path": "Areas/A.md", "history_line": "gamma added.", "candidate_index": 2},
            ]
            res = c.post("/memories/commit",
                         json={"items": items, "source_session_id": "s"}).json()
            assert [r["status"] for r in res] == ["written", "conflict", "written"], res
            atext = open(a, encoding="utf-8").read()
            assert "alpha added." in atext and "gamma added." in atext
            assert "beta added." not in open(b, encoding="utf-8").read()
            batch = res[0]["batch_id"]
            hist = c.get("/memories/history").json()
            entry = next(h for h in hist if h["batch_id"] == batch)
            assert entry["counts"] == 2, entry  # item 2 not journaled

            # the pending journal entry was rewritten with the real post-write
            # facts, and the conflicted item left no trace
            import hw_merge
            jitems = hw_merge._read_journal()[-1]["items"]
            assert len(jitems) == 2, jitems
            assert all(it["sha_after"] and it["bak"] for it in jitems), jitems
            assert all(it["path"] == "Areas/A.md" for it in jitems), jitems

            # dedup at commit: a line already in the note is skipped, not written
            atext_now = open(a, encoding="utf-8").read()
            dup = c.post("/memories/commit", json={
                "items": [{"target_path": "Areas/A.md", "history_line": "alpha added.",
                           "candidate_index": 9}], "source_session_id": "s2"}).json()
            assert dup[0]["status"] == "skipped" and dup[0]["detail"] == "near_dup", dup
            assert open(a, encoding="utf-8").read() == atext_now, "skipped item still wrote"
            assert all(h["batch_id"] != dup[0]["batch_id"]
                       for h in c.get("/memories/history").json()), "skip was journaled"

            # preview surfaces the same verdict without writing
            pv = c.post("/memories/preview", json={
                "items": [{"target_path": "Areas/A.md", "history_line": "alpha added.",
                           "candidate_index": 9}], "source_session_id": "s2"}).json()
            assert pv[0]["duplicate"] and pv[0]["reason"] == "near_dup", pv
            assert "alpha added." in (pv[0]["colliding_line"] or ""), pv

            # Timeline entries carry no supersedes clause (§6.6)
            tl = c.post("/memories/commit", json={"items": [
                {"target_path": "Timeline/2026.md", "history_line": "launched the thing.",
                 "supersedes": "the old claim", "candidate_index": 20}],
                "source_session_id": "s3"}).json()
            assert tl[0]["status"] == "written", tl
            tltext = open(os.path.join(vault, "Timeline", "2026.md"), encoding="utf-8").read()
            assert "supersedes" not in tltext, tltext

            # commit-side guard rejection
            bad = c.post("/memories/commit", json={"items": [
                {"target_path": "../evil.md", "history_line": "x."}]}).json()
            assert bad[0]["status"] == "error" and bad[0]["detail"] == "invalid path"
            assert not os.path.exists(os.path.join(d, "evil.md"))
            assert not os.path.exists(os.path.join(vault, "..", "evil.md"))

            # a dedup blowup on one item (a locked note -> PermissionError) must
            # not sink the batch: the item errors, the rest still commit.
            real_dedup = hw_merge.dedup_entry

            def _boom(line, *a, **k):
                if "boom" in line:
                    raise PermissionError("note is locked")
                return real_dedup(line, *a, **k)

            hw_merge.dedup_entry = _boom
            try:
                out = c.post("/memories/commit", json={"items": [
                    {"target_path": "Areas/A.md", "history_line": "boom item.",
                     "candidate_index": 30},
                    {"target_path": "Areas/B.md", "history_line": "safe item.",
                     "candidate_index": 31}], "source_session_id": "s4"}).json()
                assert [r["status"] for r in out] == ["error", "written"], out
                assert "locked" in out[0]["detail"], out
                assert "safe item." in open(b, encoding="utf-8").read()
                # same blowup through preview is guarded into a clean 500
                pvr = c.post("/memories/preview", json={"items": [
                    {"target_path": "Areas/A.md", "history_line": "boom in preview.",
                     "candidate_index": 32}]})
                assert pvr.status_code == 500, pvr.status_code
            finally:
                hw_merge.dedup_entry = real_dedup
    finally:
        hw_index.reset_for_tests()
        hw_store._cache = None
        if saved_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved_home
    print("  http write edges ok")


if __name__ == "__main__":
    run_module_checks()
    _selfcheck_http_read()
    _selfcheck_http_write()
    _selfcheck_http_write_edges()
    print("ok")
