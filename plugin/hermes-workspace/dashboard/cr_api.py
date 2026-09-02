"""Creator HTTP surface (spec §5.10). stdlib + FastAPI + pydantic only at module scope."""
import functools
import importlib.util
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

MAX_BYTES = 1_000_000

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
        if len(body.content.encode("utf-8")) > MAX_BYTES:
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
