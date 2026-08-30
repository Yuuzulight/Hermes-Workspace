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
    tmp = _config_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), "utf-8")
    os.replace(tmp, _config_path())
    _cache = dict(cfg)


def vault_path() -> pathlib.Path | None:
    v = get_config()["vault"]
    return pathlib.Path(v) if v else None


def vault_hash() -> str:
    vp = vault_path()
    if not vp:
        return "novault"
    return hashlib.sha1(str(vp.resolve()).encode("utf-8")).hexdigest()[:12]


def guard_path(rel: str, must_be_file: bool = True) -> pathlib.Path:
    vp = vault_path()
    if not vp:
        raise PathError("no vault configured")
    root = vp.resolve()
    unresolved = root / rel
    target = unresolved.resolve()
    if not (target == root or root in target.parents):
        raise PathError(f"path escapes vault: {rel}")
    if must_be_file and (not rel.strip() or target == root):
        raise PathError("expected a file path inside the vault")
    # .resolve() collapses symlinks, so test them on the unresolved path,
    # walking every component below the vault root.
    probe = root
    for part in unresolved.relative_to(root).parts:
        probe = probe / part
        if probe.is_symlink():
            raise PathError(f"symlinked path refused: {rel}")
    return target


def read_rules() -> str:
    """The vault's capture conventions, first readable of: the configured
    `rules_file`, `<vault>/agent_rules.md`, the shipped `default_rules.md`."""
    rf = (get_config().get("rules_file") or "").strip()
    cands: list[pathlib.Path] = []
    if rf:
        p = pathlib.Path(rf)
        if p.is_absolute():
            cands.append(p)
        else:
            try:
                cands.append(guard_path(rf))
            except PathError:
                pass
    vp = vault_path()
    if vp:
        cands.append(vp / "agent_rules.md")
    cands.append(pathlib.Path(__file__).with_name("default_rules.md"))
    for p in cands:
        try:
            if p.is_file():
                return p.read_text("utf-8", errors="replace")
        except OSError:
            pass
    return ""


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


def _validated_vault(path: str) -> str:
    p = pathlib.Path(path)
    if not p.is_dir():
        raise PathError(f"not a directory: {path}")
    if not os.access(p, os.W_OK):
        raise PathError(f"not writable: {path}")
    return str(p)


def set_vault(path: str) -> dict:
    cfg = get_config()
    cfg["vault"] = _validated_vault(path)
    _write_config(cfg)
    return status()


def update_config(patch: dict) -> dict:
    cfg = get_config()
    for key in ("k", "budget_tokens", "max_file_kb", "rules_file"):
        if key in patch and patch[key] is not None:
            cfg[key] = patch[key]
    if "vault" in patch and patch["vault"]:
        cfg["vault"] = _validated_vault(patch["vault"])
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
            for bad in ("", "   ", ".", "Areas/.."):  # vault root is not a file target
                try:
                    guard_path(bad)
                    raise AssertionError(f"vault root accepted as a file: {bad!r}")
                except PathError:
                    pass
            assert guard_path("", must_be_file=False) == pathlib.Path(vault).resolve()

            # rules: shipped default when the vault has none, vault file when present
            rules = read_rules()
            assert "Vault capture rules" in rules and "## History" in rules, rules[:80]
            with open(os.path.join(vault, "agent_rules.md"), "w", encoding="utf-8") as f:
                f.write("# House rules\n\nALWAYS USE FORMAT XYZ\n")
            assert "ALWAYS USE FORMAT XYZ" in read_rules()
            os.remove(os.path.join(vault, "agent_rules.md"))

            real = os.path.join(vault, "real.md")
            with open(real, "w", encoding="utf-8") as f:
                f.write("x")
            try:
                os.symlink(real, os.path.join(vault, "link.md"))
            except (OSError, NotImplementedError):
                pass  # no symlink privilege on this runner
            else:
                try:
                    guard_path("link.md")
                    raise AssertionError("symlink not refused")
                except PathError:
                    pass

            _cache = None
            out = update_config({"vault": vault, "k": 9})
            assert out["config"]["k"] == 9, out
            _cache = None
            assert get_config()["k"] == 9
            assert get_config()["vault"] == vault
    finally:
        _cache = None
        if saved_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved_home


if __name__ == "__main__":
    _selfcheck()
    print("ok")
