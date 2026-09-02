"""Creator HTTP surface (spec §5.10). stdlib + FastAPI + pydantic only at module scope."""
import base64
import functools
import hashlib
import importlib.util
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# cr_store is loaded by explicit path — it lives one dir up, not on sys.path.
_p = Path(__file__).resolve().parent.parent / "cr_store.py"
_s = importlib.util.spec_from_file_location("hw_cr_store", _p)
cr_store = importlib.util.module_from_spec(_s)
_s.loader.exec_module(cr_store)  # safe: _selfcheck() is under __main__

router = APIRouter(prefix="/creator")

_ASSET_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
_ASSET_SUFFIXES = {".js", ".json", ".wasm"}
_ASSETS_DIR = Path(__file__).resolve().parent / "creator-libs"

_STATUS = {
    cr_store.StoreNotFound: 404,
    cr_store.StoreGone: 410,
    cr_store.StoreBadInput: 400,
    cr_store.StoreBusy: 503,
}


class VersionBody(BaseModel):
    content: str | None = None
    restore_from: int | None = None


class ScanBody(BaseModel):
    session_id: str


class ConfigBody(BaseModel):
    project_root: str | None = None
    github_token: str | None = None


def _guard(fn):
    """Map cr_store store exceptions onto their HTTP status for one route."""
    @functools.wraps(fn)
    def wrap(*a, **kw):
        try:
            return fn(*a, **kw)
        except tuple(_STATUS) as e:
            raise HTTPException(status_code=_STATUS[type(e)], detail=str(e))
    return wrap


@router.get("/artifacts")
@_guard
def artifacts_index(session_id: str | None = None):
    return {"artifacts": cr_store.list_artifacts(session_id)}


@router.get("/artifacts/{id}")
@_guard
def artifact_detail(id: str):
    return cr_store.get_artifact(id)


@router.get("/artifacts/{id}/v/{n}")
@_guard
def artifact_version(id: str, n: int):
    return cr_store.get_version(id, n)


@router.post("/artifacts/{id}/versions")
@_guard
def create_version(id: str, body: VersionBody):
    has_content = body.content is not None
    has_restore = body.restore_from is not None
    if has_content == has_restore:
        raise HTTPException(status_code=400,
                            detail="provide exactly one of content, restore_from")
    if has_content:
        # Fast path (cheaper than round-tripping to do_create/edit_version);
        # cr_store.MAX_BYTES is the single source of truth for the §5.6 cap —
        # edit_version() enforces the same limit itself either way.
        if len(body.content.encode("utf-8")) > cr_store.MAX_BYTES:
            return JSONResponse(status_code=400, content={"error": "too_large"})
        return cr_store.edit_version(id, body.content)
    r = cr_store.restore(id, body.restore_from, "")
    return {"identifier": id, "version": r["version"], "action": r["action"]}


@router.post("/scan")
@_guard
def run_scan(body: ScanBody):
    return cr_store.scan(body.session_id)


@router.delete("/artifacts/{id}")
@_guard
def remove_artifact(id: str):
    cr_store.delete_artifact(id)
    return {"ok": True}


@router.get("/config")
@_guard
def read_config():
    return cr_store.get_config()


@router.post("/config")
@_guard
def write_config(body: ConfigBody):
    return cr_store.set_config(body.model_dump(exclude_unset=True))


@router.get("/asset/{name}")
def get_asset(name: str):
    suffix = Path(name).suffix
    if not _ASSET_NAME_RE.match(name) or suffix not in _ASSET_SUFFIXES:
        raise HTTPException(status_code=400, detail="invalid asset name")
    p = _ASSETS_DIR / name
    base = os.path.realpath(_ASSETS_DIR)
    if not os.path.realpath(p).startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="invalid asset name")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    raw = p.read_bytes()
    encoding = "base64" if suffix == ".wasm" else "utf8"
    data = base64.b64encode(raw).decode("ascii") if encoding == "base64" else raw.decode("utf-8")
    return {"name": name, "encoding": encoding, "data": data,
            "sha256": hashlib.sha256(raw).hexdigest()}


@router.get("/{_unmatched:path}")
def _unmatched_get(_unmatched: str):
    """HTTP clients collapse `../` out of a URL path before sending it (RFC 3986
    dot-segment removal), so a traversal attempt like `/creator/asset/../x` never
    reaches `/asset/{name}` — it arrives here as `/creator/x` instead. Registered
    last (only matches what no real route did): treat it as the malformed/hostile
    request it is rather than leaking a generic 404."""
    raise HTTPException(status_code=400, detail="invalid path")
