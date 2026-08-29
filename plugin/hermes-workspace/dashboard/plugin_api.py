"""Hermes Workspace — Knowledge module backend. Wiring only; logic lives in hw_*."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import APIRouter, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import hw_context  # noqa: E402
import hw_index  # noqa: E402
import hw_store  # noqa: E402

router = APIRouter()


class ConfigPatch(BaseModel):
    vault: str | None = None
    k: int | None = None
    budget_tokens: int | None = None
    max_file_kb: int | None = None
    rules_file: str | None = None


@router.get("/status")
def get_status() -> dict:
    st = hw_store.status()
    vp = hw_store.vault_path()
    if vp and vp.is_dir():
        st = {**st, **hw_index.get_index().status()}
    return st


@router.get("/config")
def read_config() -> dict:
    return hw_store.get_config()


@router.post("/config")
def write_config(patch: ConfigPatch) -> dict:
    try:
        return hw_store.update_config(patch.model_dump())
    except hw_store.PathError as e:
        raise HTTPException(status_code=400, detail=str(e))


class SearchBody(BaseModel):
    query: str
    limit: int = 8


@router.post("/search")
def search(body: SearchBody) -> dict:
    vp = hw_store.vault_path()
    if not vp or not vp.is_dir():
        return {"results": [], "error": "vault_not_found"}
    return {"results": hw_index.get_index().search(body.query, body.limit)}


class ContextBody(BaseModel):
    query: str
    budget_tokens: int = 1500
    k_max: int = 6


@router.post("/context")
def context(body: ContextBody) -> dict:
    vp = hw_store.vault_path()
    if not vp or not vp.is_dir():
        return {"notes": [], "total_tokens": 0, "block": ""}
    return hw_context.build_context(hw_index.get_index(), body.query,
                                    body.budget_tokens, body.k_max)


@router.get("/tree")
def tree(path: str = "") -> dict:
    try:
        base = hw_store.guard_path(path)
    except hw_store.PathError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not base.is_dir():
        raise HTTPException(status_code=404, detail="not a directory")
    vp = hw_store.vault_path()
    dirs, files = [], []
    with os.scandir(base) as it:
        entries = sorted(it, key=lambda e: e.name.lower())
    for entry in entries:
        if entry.name.startswith("."):
            continue
        rel = os.path.relpath(entry.path, vp).replace(os.sep, "/")
        if entry.is_dir(follow_symlinks=False):
            dirs.append(rel)
        elif entry.name.lower().endswith(".md"):
            files.append({"path": rel, "title": os.path.splitext(entry.name)[0],
                          "mtime": entry.stat().st_mtime})
    return {"dirs": dirs, "files": files}


@router.get("/note")
def note(path: str) -> dict:
    try:
        p = hw_store.guard_path(path)
    except hw_store.PathError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not p.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return {"path": path, "abspath": str(p),
            "markdown": p.read_text("utf-8", errors="replace")}


@router.get("/resolve")
def resolve(link: str) -> dict:
    vp = hw_store.vault_path()
    if not vp or not vp.is_dir():
        return {"path": None}
    target = link.strip().strip("[]").split("#")[0].split("|")[0].strip()
    idx = hw_index.get_index()
    hits = idx.search(f'"{target}"', 5)
    for h in hits:
        stem = os.path.splitext(os.path.basename(h["path"]))[0]
        if stem.lower() == target.lower() or h["path"] == target or h["path"] == target + ".md":
            return {"path": h["path"]}
    return {"path": None}


class ReindexBody(BaseModel):
    full: bool = False


@router.post("/reindex")
def reindex(body: ReindexBody) -> dict:
    vp = hw_store.vault_path()
    if not vp or not vp.is_dir():
        return {"indexed": 0, "removed": 0, "took_ms": 0, "error": "vault_not_found"}
    return hw_index.get_index().sync(full=body.full)
