"""Vault path, config persistence, and path-safety helpers."""
import hashlib
import json
import os
import pathlib

CONFIG_DEFAULTS = {
    "vault": "",
    "k": 6,
    "budget_tokens": 1500,
    "max_file_kb": 2048,
    "rules_file": "",  # empty => auto-detect agent_rules.md, else default_rules.md
}


class PathError(Exception):
    pass


def _hermes_home() -> pathlib.Path:
    return pathlib.Path(os.environ.get("HERMES_HOME", pathlib.Path.home() / ".hermes"))


def data_dir() -> pathlib.Path:
    d = _hermes_home() / "plugins" / "hermes-workspace" / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _config_path() -> pathlib.Path:
    return data_dir() / "config.json"


_cache: dict | None = None


def get_config() -> dict:
    global _cache
    if _cache is None:
        cfg = dict(CONFIG_DEFAULTS)
        p = _config_path()
        if p.exists():
            try:
                cfg.update(json.loads(p.read_text("utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        _cache = cfg
    return dict(_cache)


def _write_config(cfg: dict) -> None:
    global _cache
    _cache = dict(cfg)
    tmp = _config_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), "utf-8")
    os.replace(tmp, _config_path())


def vault_path() -> pathlib.Path | None:
    v = get_config()["vault"]
    return pathlib.Path(v) if v else None


def vault_hash() -> str:
    vp = vault_path()
    if not vp:
        return "novault"
    return hashlib.sha1(str(vp.resolve()).encode("utf-8")).hexdigest()[:12]


def guard_path(rel: str) -> pathlib.Path:
    vp = vault_path()
    if not vp:
        raise PathError("no vault configured")
    root = vp.resolve()
    target = (root / rel).resolve()
    if not (target == root or root in target.parents):
        raise PathError(f"path escapes vault: {rel}")
    if target.is_symlink() or (target.exists() and target.parent != root and any(
        p.is_symlink() for p in target.parents if root in p.parents or p == root)):
        raise PathError(f"symlinked path refused: {rel}")
    return target


def status() -> dict:
    vp = vault_path()
    exists = bool(vp and vp.is_dir())
    writable = bool(exists and os.access(vp, os.W_OK))
    return {
        "vault_path": str(vp) if vp else "",
        "vault_exists": exists,
        "writable": writable,
        "schema_version": 1,
    }


def set_vault(path: str) -> dict:
    p = pathlib.Path(path)
    if not p.is_dir():
        raise PathError(f"not a directory: {path}")
    if not os.access(p, os.W_OK):
        raise PathError(f"not writable: {path}")
    cfg = get_config()
    cfg["vault"] = str(p)
    _write_config(cfg)
    return status()


def update_config(patch: dict) -> dict:
    cfg = get_config()
    for key in ("k", "budget_tokens", "max_file_kb", "rules_file"):
        if key in patch and patch[key] is not None:
            cfg[key] = patch[key]
    if "vault" in patch and patch["vault"]:
        return {**set_vault(patch["vault"]), "config": cfg}
    _write_config(cfg)
    return {**status(), "config": cfg}


def _selfcheck() -> None:
    import tempfile

    global _cache
    saved_home = os.environ.get("HERMES_HOME")
    try:
        with tempfile.TemporaryDirectory() as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            _cache = None
            vault = os.path.join(d, "vault")
            os.makedirs(vault)
            st = set_vault(vault)
            assert st["vault_exists"] and st["writable"], st
            _cache = None
            assert get_config()["vault"] == vault
            try:
                guard_path("../escape")
                raise AssertionError("traversal not blocked")
            except PathError:
                pass
            assert guard_path("Areas/x.md").name == "x.md"
    finally:
        _cache = None
        if saved_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved_home


if __name__ == "__main__":
    _selfcheck()
    print("ok")
