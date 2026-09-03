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


def _selfcheck_cr_store() -> None:
    import importlib.util
    p = os.path.join(os.path.dirname(__file__), "..", "cr_store.py")
    s = importlib.util.spec_from_file_location("cr_store_probe", p)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    m._selfcheck()
    print("  cr_store._selfcheck ok")


def _selfcheck_cr_tools() -> None:
    """cr_tools.py's own agent-tool layer (_h_create/_h_update/_h_read, the
    prompt-section assert, error mapping) — never run by this file before
    Fix 6. Same explicit-path-load pattern as _selfcheck_cr_store(); cr_tools
    does `try: from . import cr_store except ImportError: import cr_store`,
    so cr_store's parent dir goes on sys.path first so the fallback import
    resolves outside a package."""
    import importlib.util
    import sys
    plugin_dir = os.path.join(os.path.dirname(__file__), "..")
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    p = os.path.join(plugin_dir, "cr_tools.py")
    s = importlib.util.spec_from_file_location("cr_tools_probe", p)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    m._selfcheck()
    print("  cr_tools._selfcheck ok")


def _selfcheck_read_assistant_messages() -> None:
    """_read_assistant_messages' real (non-monkeypatched) code path: a lazy
    `from hermes_state import SessionDB` import, then flattening a couple of
    realistic message shapes (str content, and list-of-text-block content)."""
    import importlib.util
    import sys
    import types

    class _FakeSessionDB:
        def get_messages(self, session_id, include_compacted=True):
            assert session_id == "sess-1" and include_compacted is True
            return [
                {"role": "user", "content": "hi there"},
                {"role": "assistant", "content": "plain string reply"},
                {"role": "assistant", "content": [
                    {"type": "text", "text": "block one"},
                    {"type": "text", "text": "block two"},
                ]},
            ]

    fake_mod = types.ModuleType("hermes_state")
    fake_mod.SessionDB = _FakeSessionDB
    saved_mod = sys.modules.get("hermes_state")
    sys.modules["hermes_state"] = fake_mod
    try:
        p = os.path.join(os.path.dirname(__file__), "..", "cr_store.py")
        s = importlib.util.spec_from_file_location("cr_store_ram_probe", p)
        m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
        out = m._read_assistant_messages("sess-1")
        assert out == ["plain string reply", "block one block two"], out
    finally:
        if saved_mod is None:
            sys.modules.pop("hermes_state", None)
        else:
            sys.modules["hermes_state"] = saved_mod
    print("  _read_assistant_messages real path ok")


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
            # asset envelope route (Task 16)
            import json
            env = c.get("/creator/asset/MANIFEST.json").json()
            assert env["encoding"] == "utf8" and json.loads(env["data"]) is not None
            w = c.get("/creator/asset/esbuild.wasm").json()
            import base64 as _b
            assert w["encoding"] == "base64" and _b.b64decode(w["data"])[:2] == b"\x00a"
            # HTTP-level traversal attempts never reach the route: httpx's
            # TestClient normalizes `../` out of the URL client-side before
            # sending, so the guard must be exercised as a direct call instead.
            from fastapi import HTTPException
            for bad in ("../plugin_api.py", "..\\plugin_api.py", "....//plugin_api.py"):
                try:
                    cr_api.get_asset(bad)
                    assert False, f"{bad!r} was not rejected"
                except HTTPException as e:
                    assert e.status_code == 400, (bad, e.status_code)
            assert c.get("/creator/asset/nope.js").status_code == 404
            # defensive mount: a broken cr_api import must not unmount hw_* routes
            import plugin_api, importlib
            assert any(r.path == "/status" for r in plugin_api.router.routes)
    finally:
        if saved is None: os.environ.pop("HERMES_HOME", None)
        else: os.environ["HERMES_HOME"] = saved
    print("  creator http round-trip ok")


def _selfcheck_creator_export() -> None:
    """Task 29: standalone `.html` export — html/svg/code/markdown each produce
    a non-empty file on disk containing the artifact's content; markdown's file
    also carries the inlined marked-only viewer-md.js bundle (the
    `renderMarkdown` call the bootstrap script makes into it) but, per
    final-review Fix 4, NOT the ~3.5MB mermaid renderer it never calls — so the
    file stays small; `react` export returns 400 (assembled client-side in the
    pane, not reproducible server-side)."""
    import tempfile
    saved = os.environ.get("HERMES_HOME")
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            import cr_api
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            app = FastAPI(); app.include_router(cr_api.router)
            c = TestClient(app)

            seeds = {
                "exp-html": ("html", "<h1>Hi There</h1>"),
                "exp-svg": ("svg", "<svg><circle r='5'/></svg>"),
                "exp-code": ("code", "print(1)\nprint(2)"),
                "exp-md": ("markdown", "# Hello World"),
            }
            for ident, (type_, content) in seeds.items():
                cr_api.cr_store.do_create(
                    {"identifier": ident, "type": type_, "title": ident, "content": content},
                    "sess-export")

            paths = {}
            for ident, (type_, content) in seeds.items():
                r = c.post(f"/creator/artifacts/{ident}/export")
                assert r.status_code == 200, (ident, r.status_code, r.text)
                path = r.json()["path"]
                assert os.path.isfile(path), path
                text = open(path, encoding="utf-8").read()
                assert text, f"{ident} export is empty"
                assert content in text, (ident, content, text[:200])
                paths[ident] = text

            # markdown export carries the inlined viewer-md.js bundle: the
            # bootstrap script literally calls mod.renderMarkdown(...). Final-
            # review Fix 4 split marked out of the combined marked+mermaid
            # viewer.js, so a markdown export must NOT drag in the mermaid
            # renderer it never calls, and the file must stay well under the
            # old combined bundle's multi-MB size.
            assert "renderMarkdown" in paths["exp-md"]
            assert "renderMermaidInto" not in paths["exp-md"]
            assert len(paths["exp-md"]) < 500_000, len(paths["exp-md"])

            # react: not reproducible server-side -> 400
            cr_api.cr_store.do_create(
                {"identifier": "exp-react", "type": "react", "title": "R",
                 "content": "export default () => null"}, "sess-export")
            rr = c.post("/creator/artifacts/exp-react/export")
            assert rr.status_code == 400, rr.status_code

            # write_export_bundle: pane-assembled HTML persisted verbatim
            b = c.post("/creator/artifacts/exp-html/export/bundle",
                      json={"html": "<!doctype html><html><body>bundled</body></html>"})
            assert b.status_code == 200
            bpath = b.json()["path"]
            assert os.path.isfile(bpath)
            assert open(bpath, encoding="utf-8").read() == \
                "<!doctype html><html><body>bundled</body></html>"

            # dest validation: relative path rejected
            bad = c.post("/creator/artifacts/exp-html/export", json={"dest": "relative.html"})
            assert bad.status_code == 400, bad.status_code
    finally:
        if saved is None: os.environ.pop("HERMES_HOME", None)
        else: os.environ["HERMES_HOME"] = saved
    print("  creator export ok")


def _selfcheck_creator_publish() -> None:
    """Task 31: POST /creator/artifacts/{id}/publish — routes through to
    cr_store.publish_artifact and relays its result verbatim (200, even for
    an {"error": ...} body — publish_artifact returns those for expected/
    recoverable conditions rather than raising). Monkeypatches shutil.which +
    subprocess.run (module-global, so cr_api's explicit-path-loaded cr_store
    sees them too) so this NEVER shells out to the real `gh` CLI."""
    import shutil
    import subprocess
    import tempfile
    saved = os.environ.get("HERMES_HOME")
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            import cr_api
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            app = FastAPI(); app.include_router(cr_api.router)
            c = TestClient(app)

            cr_api.cr_store.do_create(
                {"identifier": "pub-http-doc", "type": "markdown", "title": "Pub Http Doc",
                 "content": "# hi"}, "sess-publish")
            cr_api.cr_store.do_create(
                {"identifier": "pub-http-app", "type": "react", "title": "PubApp",
                 "content": "export default () => null"}, "sess-publish")

            orig_which, orig_run = shutil.which, subprocess.run

            def fake_run(args, **kw):
                if args[:2] == ["gh", "auth"]:
                    return subprocess.CompletedProcess(args, 0, "", "")
                if args[:3] == ["gh", "gist", "create"]:
                    return subprocess.CompletedProcess(
                        args, 0, "https://gist.github.com/testuser/deadbeef01\n", "")
                raise AssertionError(f"unexpected subprocess.run in selftest: {args}")

            shutil.which = lambda name: "/usr/bin/gh" if name == "gh" else None
            subprocess.run = fake_run
            try:
                r = c.post("/creator/artifacts/pub-http-doc/publish")
                assert r.status_code == 200, (r.status_code, r.text)
                assert r.json() == {
                    "url": "https://gist.github.com/testuser/deadbeef01",
                    "raw_url": "https://raw.githack.com/testuser/deadbeef01/raw/pub-http-doc.html"}

                rn = c.post("/creator/artifacts/pub-http-app/publish")
                assert rn.status_code == 200 and rn.json() == {"error": "needs_pane"}

                rh = c.post("/creator/artifacts/pub-http-app/publish",
                           json={"html": "<html><body>x</body></html>"})
                assert rh.status_code == 200 and rh.json()["url"].startswith(
                    "https://gist.github.com/")
            finally:
                shutil.which, subprocess.run = orig_which, orig_run

            shutil.which = lambda name: None
            try:
                rc = c.post("/creator/artifacts/pub-http-doc/publish")
                assert rc.status_code == 200
                assert rc.json() == {
                    "error": "github_not_configured",
                    "how": "install gh and run gh auth login, or set a token in Creator settings"}
            finally:
                shutil.which = orig_which

            # unknown identifier -> 404 via _guard, like every other route
            rm = c.post("/creator/artifacts/nope-pub/publish")
            assert rm.status_code == 404, rm.status_code
    finally:
        if saved is None: os.environ.pop("HERMES_HOME", None)
        else: os.environ["HERMES_HOME"] = saved
    print("  creator publish ok")


def _selfcheck_creator_defensive_mount() -> None:
    """A throw from `import cr_api` must leave every hw_* route mounted and log a warning."""
    import builtins
    import importlib
    import logging
    import plugin_api

    def _paths(mod) -> set:
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(mod.router)
        return set(app.openapi()["paths"])

    real_import = builtins.__import__

    def _no_cr_api(name, *a, **k):
        if name == "cr_api":
            raise ImportError("selftest: cr_api forced unavailable")
        return real_import(name, *a, **k)

    logger = logging.getLogger("plugin_api")
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.setLevel(0)
    handler.emit = records.append
    logger.addHandler(handler)
    try:
        builtins.__import__ = _no_cr_api
        importlib.reload(plugin_api)
        broken = _paths(plugin_api)
        assert "/status" in broken, "hw_* routes lost when cr_api import failed"
        assert not any(p.startswith("/creator") for p in broken), broken
        assert any("creator API not mounted" in rec.getMessage()
                   for rec in records), [rec.getMessage() for rec in records]
    finally:
        builtins.__import__ = real_import
        logger.removeHandler(handler)
        importlib.reload(plugin_api)  # restore a clean, fully-wired plugin_api
    restored = _paths(plugin_api)
    assert "/status" in restored and "/creator/config" in restored, restored
    print("  creator defensive mount ok")


if __name__ == "__main__":
    run_module_checks()
    _selfcheck_http_read()
    _selfcheck_http_write()
    _selfcheck_http_write_edges()
    _selfcheck_cr_store()
    _selfcheck_cr_tools()
    _selfcheck_read_assistant_messages()
    _selfcheck_creator_http()
    _selfcheck_creator_export()
    _selfcheck_creator_publish()
    _selfcheck_creator_defensive_mount()
    print("ok")
