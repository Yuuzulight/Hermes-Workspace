"""Pure Markdown note parsing for the FTS index. No I/O."""
import re

SECRET_KEY_RE = re.compile(r"pass|secret|token|api[_-]?key$", re.I)
_TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z0-9/_-]+)")
_LINK_RE = re.compile(r"\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|[^\]]+)?\]\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^(```|~~~)")


def _split_frontmatter(text: str) -> tuple[str, str]:
    text = text.lstrip("\ufeff")
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---", 4)
    if end == -1:
        return "", text
    nl = text.find("\n", end + 1)
    body = text[nl + 1:] if nl != -1 else ""
    return text[4:end], body


def _flatten_frontmatter(fm: str) -> tuple[str, str, list[str]]:
    """Return (searchable_pairs, tags_from_fm, title_bits)."""
    pairs: list[str] = []
    tags: list[str] = []
    title_bits: list[str] = []
    for line in fm.splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            m2 = re.match(r"^\s*-\s+(.*)$", line)
            if m2 and pairs:
                pairs.append(m2.group(1).strip())
            continue
        key, val = m.group(1), m.group(2).strip()
        if SECRET_KEY_RE.search(key):
            continue
        low = key.lower()
        values = [v.strip() for v in val.strip("[]").split(",") if v.strip()]
        if low == "tags":
            tags += values
        if low in ("title", "aliases"):
            title_bits += values
        pairs.append(f"{key} {val}".strip())
    return (
        " ".join(p for p in pairs if p),
        " ".join(t for t in tags if t),
        title_bits,
    )


def parse_note(text: str, stem: str) -> dict:
    fm, body = _split_frontmatter(text)
    fm_pairs, fm_tags, title_bits = _flatten_frontmatter(fm)

    headings, tags, in_fence = [], [], False
    for line in body.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        h = _HEADING_RE.match(line)
        if h:
            headings.append(h.group(2).strip())
        else:
            tags += _TAG_RE.findall(line)

    nested = []
    for t in tags:
        nested.append(t)
        if "/" in t:
            nested += t.split("/")
    links = [m.strip().rsplit("/", 1)[-1].lower() for m in _LINK_RE.findall(body)]

    return {
        "title": " ".join(dict.fromkeys([stem, *title_bits])),
        "headings": "\n".join(headings),
        "tags": " ".join(dict.fromkeys([*nested, *fm_tags.split()])),
        "links": " ".join(dict.fromkeys(links)),
        "frontmatter": fm_pairs,
        "body": body,
    }


def _selfcheck() -> None:
    r = parse_note("\ufeff---\ntitle: My Note\naliases: [MN, note two]\napi_key: sekret\n"
                   "tags: [alpha, beta]\n---\n# Heading One\n\n"
                   "Some prose with #inline and #area/health tags.\n"
                   "A [[People/Ada Lovelace|Ada]] link.\n"
                   "```\n#not-a-tag\n```\n", "my-note")
    assert "my-note" in r["title"] and "My Note" in r["title"], r["title"]
    assert "Heading One" in r["headings"]
    assert "inline" in r["tags"].split() and "area/health" in r["tags"].split()
    assert "health" in r["tags"].split()  # nested expansion
    assert "alpha" in r["tags"].split() and "beta" in r["tags"].split()
    assert "ada lovelace" in r["links"]
    assert "sekret" not in r["frontmatter"]  # secret key skipped
    assert "not-a-tag" not in r["tags"].split()  # fenced code ignored


if __name__ == "__main__":
    _selfcheck()
    print("ok")
