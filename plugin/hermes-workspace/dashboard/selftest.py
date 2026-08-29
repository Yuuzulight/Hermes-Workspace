"""Run every module self-check plus a full HTTP round-trip. `python selftest.py [--big]`."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

MODULES = ["hw_store", "hw_notes", "hw_index"]  # grows each task


def run_module_checks() -> None:
    for name in MODULES:
        mod = __import__(name)
        if hasattr(mod, "_selfcheck"):
            mod._selfcheck()
            print(f"  {name}._selfcheck ok")


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import plugin_api
    app = FastAPI()
    app.include_router(plugin_api.router)
    return TestClient(app)


def _selfcheck_http_read() -> None:
    import tempfile
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        os.environ["HERMES_HOME"] = os.path.join(d, "home")
        import hw_store, hw_index
        hw_store._cache = None
        vault = os.path.join(d, "vault")
        os.makedirs(os.path.join(vault, "Areas"))
        open(os.path.join(vault, "Areas", "Argos.md"), "w", encoding="utf-8").write(
            "# Argos\n\nwidget engine\n\n## History\n\n- **2026-08-20** — merged.\n")
        hw_index.reset_for_tests()
        c = _client()
        assert c.post("/config", json={"vault": vault}).json()["vault_exists"]
        c.post("/reindex", json={"full": True})
        assert c.post("/search", json={"query": "widget engine"}).json()["results"][0]["path"] \
            == "Areas/Argos.md"
        assert "Areas" in c.get("/tree").json()["dirs"]
        assert c.get("/note", params={"path": "Areas/Argos.md"}).json()["markdown"].startswith("# Argos")
        assert c.get("/note", params={"path": "../x"}).status_code == 400
        assert c.get("/note", params={"path": "Areas/Nope.md"}).status_code == 404
        hw_index.reset_for_tests()
    print("  http read round-trip ok")


if __name__ == "__main__":
    run_module_checks()
    _selfcheck_http_read()
    print("ok")
