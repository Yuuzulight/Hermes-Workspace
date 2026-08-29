"""Hermes Workspace — Knowledge module backend. Wiring only; logic lives in hw_*."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import APIRouter, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

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
    return hw_store.status()


@router.get("/config")
def read_config() -> dict:
    return hw_store.get_config()


@router.post("/config")
def write_config(patch: ConfigPatch) -> dict:
    try:
        return hw_store.update_config(patch.model_dump())
    except hw_store.PathError as e:
        raise HTTPException(status_code=400, detail=str(e))
