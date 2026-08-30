"""Hermes Workspace — Knowledge module backend. Wiring only; logic lives in hw_*."""
import datetime
import difflib
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import APIRouter, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import hw_context  # noqa: E402
import hw_extract  # noqa: E402
import hw_index  # noqa: E402
import hw_merge  # noqa: E402
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


# --- write side: extraction -> approval -> write ---------------------------


class PrepareBody(BaseModel):
    messages: list[dict]


@router.post("/extract/prepare")
def extract_prepare(body: PrepareBody) -> dict:
    flat = []
    for m in body.messages:
        content = m.get("content")
        if isinstance(content, list):
            joined = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            m = {**m, "content": joined}
        flat.append(m)
    return {"transcript_text": hw_extract.render_transcript(flat),
            "prompt": hw_extract.build_prompt()}


class ParseBody(BaseModel):
    raw: str


@router.post("/extract/parse")
def extract_parse(body: ParseBody) -> dict:
    return hw_extract.parse_model_output(body.raw)


class ResolveBody(BaseModel):
    candidates: list[dict]
    source_session_id: str = ""


@router.post("/extract/resolve")
def extract_resolve(body: ResolveBody) -> dict:
    idx = hw_index.get_index()
    vp = hw_store.vault_path()
    today = datetime.date.today().isoformat()
    out = []
    for i, c in enumerate(body.candidates):
        r = hw_merge.resolve_target(c["target"], idx)
        line = hw_merge.render_line(c["history_line"], c.get("supersedes"), today)
        tpath = vp / r["target_path"]
        text = tpath.read_text("utf-8", errors="replace") if tpath.is_file() else ""
        is_tl = r["target_path"].startswith("Timeline/")
        dd = hw_merge.dedup_entry(line, text, body.source_session_id, i, idx, is_tl)
        out.append({**c, "candidate_index": i, "target_path": r["target_path"],
                    "action": r["action"], "resolved_from": r["resolved_from"],
                    "fuzzy_candidate": r["fuzzy_candidate"], "rendered_line": line,
                    "quote": c.get("quote", ""),
                    "duplicate": dd["duplicate"], "reason": dd["reason"],
                    "colliding_line": dd["colliding_line"], "warning": dd["warning"]})
    return {"candidates": out}


class MemItem(BaseModel):
    target_path: str
    history_line: str
    supersedes: str | None = None
    candidate_index: int = 0
    pre_sha: str | None = None


class PreviewBody(BaseModel):
    items: list[MemItem]
    source_session_id: str = ""


def _plan(item: MemItem):
    vp = hw_store.vault_path()
    today = datetime.date.today().isoformat()
    line = hw_merge.render_line(item.history_line, item.supersedes, today)
    abspath = vp / item.target_path
    stem = os.path.splitext(os.path.basename(item.target_path))[0]
    is_tl = item.target_path.startswith("Timeline/")
    if abspath.is_file():
        before = abspath.read_text("utf-8", errors="replace")
        after = (hw_merge.insert_timeline_line(before, line) if is_tl
                 else hw_merge.insert_history_line(before, line)[0])
        action, created = "append", False
    else:
        before = ""
        after = hw_merge.new_note_body(stem, line)
        action, created = "create", True
    return line, str(abspath), before, after, action, created


@router.post("/memories/preview")
def memories_preview(body: PreviewBody) -> list[dict]:
    res = []
    for item in body.items:
        try:
            hw_store.guard_path(item.target_path)
        except hw_store.PathError:
            raise HTTPException(status_code=400, detail="invalid path")
        line, abspath, before, after, action, created = _plan(item)
        diff = "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            item.target_path, item.target_path, n=3))
        pre_sha = hw_merge.sha256(before.encode("utf-8")) if os.path.isfile(abspath) else None
        res.append({"target_path": item.target_path, "action": action,
                    "section_created": created, "diff": diff, "pre_sha": pre_sha,
                    "warnings": [], "resolved_from": ""})
    return res


@router.post("/memories/commit")
def memories_commit(body: PreviewBody) -> list[dict]:
    batch_id = uuid.uuid4().hex[:12]
    journal_items, results, touched = [], [], []
    backed_up: set[str] = set()
    for item in body.items:
        try:
            hw_store.guard_path(item.target_path)
        except hw_store.PathError:
            results.append({"target_path": item.target_path, "status": "error",
                            "detail": "invalid path"})
            continue
        line, abspath, before, after, action, _ = _plan(item)
        if os.path.isfile(abspath):
            if abspath not in backed_up:
                hw_merge.backup(abspath)
                backed_up.add(abspath)
        else:
            os.makedirs(os.path.dirname(abspath), exist_ok=True)
        sha_before = hw_merge.sha256(before.encode("utf-8")) if os.path.isfile(abspath) else None
        w = hw_merge.atomic_write(abspath, after, item.pre_sha or sha_before)
        results.append({"target_path": item.target_path, "status": w["status"],
                        "detail": w["detail"]})
        if w["status"] == "written":
            touched.append(item.target_path)
            journal_items.append({"path": item.target_path, "sha_before": sha_before,
                                  "sha_after": w["sha_after"], "line": line,
                                  "source_session_id": body.source_session_id,
                                  "candidate_index": item.candidate_index})
    if journal_items:
        hw_merge.journal_append(batch_id, journal_items)
        idx = hw_index.get_index()
        idx._last_scan_ns = 0
        idx.sync()
    return [{**r, "batch_id": batch_id} for r in results]


class UndoBody(BaseModel):
    batch_id: str | None = None


@router.post("/memories/undo")
def memories_undo(body: UndoBody) -> list[dict]:
    out = hw_merge.undo(body.batch_id)
    idx = hw_index.get_index()
    idx._last_scan_ns = 0
    idx.sync()
    return out


@router.get("/memories/history")
def memories_history() -> list[dict]:
    return [{"batch_id": b["batch_id"], "ts": b["ts"],
             "notes": sorted({it["path"] for it in b["items"]}),
             "counts": len(b["items"])} for b in reversed(hw_merge._read_journal())]
