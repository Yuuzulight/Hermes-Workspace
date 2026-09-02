"""Creator store — all Creator behaviour. stdlib only; no relative imports."""
import base64, hashlib, json, os, re, sqlite3, time
from pathlib import Path


def normalize(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def _selfcheck() -> None:
    # skeleton assertions grow every task
    assert normalize("a\r\nb\n") == "a\nb"


if __name__ == "__main__":
    _selfcheck()
    print("ok")
