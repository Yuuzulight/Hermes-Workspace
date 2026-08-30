"""Turn a chat transcript into reviewable memory candidates."""
import json
import re
import unicodedata

import hw_context

PROVIDER_DENY_RE = re.compile(
    r"\b(claude|anthropic|gpt|openai|gemini|grok|xai|llama|mistral|ollama|copilot)\b", re.I)
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{16,}|api[_-]?key\s*[:=]|password\s*[:=]|-----BEGIN)", re.I)
_LINE_OK_RE = re.compile(r"^- \*\*\d{4}-\d{2}-\d{2}\*\* — .+[.!?](\s\*\(supersedes:.*\)\*)?$")

PROMPT = '''You extract durable memories from a conversation to store in the user's personal
knowledge vault. Return ONLY a JSON object: {"memories": [ ... ]}. No prose, no code fence.

Each memory:
{
  "target": "the vault note this belongs in, as a path. One note per person
             (People/<Name>.md), per project or ongoing area (Areas/<Name>.md),
             cross-cutting topic (Topics/<Name>.md), or stable facts about the
             vault owner (Profile.md). Use an existing note when one fits.",
  "history_line": "a single markdown bullet, exactly:
                   - **YYYY-MM-DD** — <one or two declarative sentences>.
                   Present tense for a standing fact or preference; past tense
                   with the date for an event. Self-contained: no pronoun
                   referring outside the sentence; resolve 'I'/'you' to the
                   actual name, else 'the user'.",
  "quote": "a short verbatim snippet from the user for the reviewer's eyes",
  "supersedes": "the verbatim earlier claim this corrects, or null"
}

If a memory is dated and matters beyond a single note (a launch, decision,
interview, retrain, deadline), emit a SECOND memory object for it with
target "Timeline/<this year>.md" and a one-sentence history_line.

Recall scaffold - look for: standing facts, preferences, decisions and events,
facts about people, open questions the user wants tracked.

Rules:
- Only stable information worth remembering weeks from now.
- Only what the user stated or explicitly confirmed. Never record the
  assistant's suggestions or opinions.
- Exclude questions to the assistant, hypotheticals, transient task chatter, code.
- Do not invent. If unsure, omit the item.
- Never include a password, API key, token, or full street address.
- Name no AI model, assistant, or provider in any field.
- Prefer 0-12 items. Return {"memories": []} if nothing qualifies.'''


def build_prompt(existing_history: str = "") -> str:
    if existing_history.strip():
        return PROMPT + ("\n\nAlready recorded in the vault - do NOT re-emit these:\n"
                         + existing_history.strip()[:4000])
    return PROMPT


def render_transcript(messages: list[dict]) -> str:
    out = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        text = hw_context.strip_vault_context(m.get("text") or m.get("content") or "")
        if text.strip():
            out.append(f"{role.upper()}: {text.strip()}")
    joined = "\n\n".join(out)
    if len(joined) > 80_000:  # ~20k tokens
        joined = "[earlier messages omitted]\n\n" + joined[-80_000:]
    return joined


def parse_model_output(raw: str) -> dict:
    txt = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip())
    data = None
    try:
        data = json.loads(txt)
    except json.JSONDecodeError:
        for op, cl in (("{", "}"), ("[", "]")):
            i, j = txt.find(op), txt.rfind(cl)
            if i != -1 and j > i:
                try:
                    data = json.loads(txt[i:j + 1])
                    break
                except json.JSONDecodeError:
                    continue
    if data is None:
        return {"candidates": [], "rejected": [], "error": "model_output_unparseable",
                "raw_excerpt": raw[:500]}
    items = data.get("memories", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return {"candidates": [], "rejected": [], "error": "model_output_unparseable",
                "raw_excerpt": raw[:500]}
    candidates, rejected = [], []
    for it in items:
        v = validate_candidate(it if isinstance(it, dict) else {})
        (candidates if v else rejected).append(v or {"candidate": it, "reason": "invalid"})
    return {"candidates": candidates, "rejected": rejected, "error": None, "raw_excerpt": None}


def _clean(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = "".join(ch for ch in s if ch == "\n" or ord(ch) >= 32)
    return re.sub(r"[ \t]+", " ", s).strip()


def validate_candidate(c: dict) -> dict | None:
    target = _clean(str(c.get("target", "")))
    hline = _clean(str(c.get("history_line", "")))
    supersedes = c.get("supersedes")
    supersedes = _clean(str(supersedes)) if supersedes else None
    if not target or len(target) > 200 or ".." in target.split("/") or re.match(r"^[A-Za-z]:", target):
        return None
    if not (8 <= len(hline) <= 400):
        return None
    for field in (target, hline, supersedes or ""):
        if PROVIDER_DENY_RE.search(field) or SECRET_RE.search(field):
            return None
    if not _LINE_OK_RE.match(hline):
        m = re.match(r"^\s*-?\s*(?:\*\*(\d{4}-\d{2}-\d{2})\*\*\s*[—-]\s*)?(.*)$", hline)
        prose = (m.group(2) if m else hline).strip().rstrip(".") + "."
        date = m.group(1) if m and m.group(1) else None
        hline = f"- **{date}** — {prose}" if date else f"- {prose}"  # date fixed in render_line
    return {"target": target, "history_line": hline, "supersedes": supersedes}


def _selfcheck() -> None:
    msgs = [
        {"role": "user", "text": hw_context.VAULT_CONTEXT_OPEN + "\nnote stuff\n"
         + hw_context.VAULT_CONTEXT_CLOSE + "\nI switched my editor to Helix."},
        {"role": "assistant", "text": "Noted."},
        {"role": "tool", "text": "should be dropped"},
    ]
    tr = render_transcript(msgs)
    assert "note stuff" not in tr and "USER: I switched my editor to Helix." in tr
    assert "should be dropped" not in tr

    ok = parse_model_output('```json\n{"memories":[{"target":"Profile.md",'
                            '"history_line":"- **2026-08-30** — The user uses Helix.",'
                            '"supersedes":null}]}\n```')
    assert ok["error"] is None and ok["candidates"][0]["target"] == "Profile.md"

    assert parse_model_output("total garbage no json")["error"] == "model_output_unparseable"

    prose_then = parse_model_output('here you go:\n{"memories":[{"target":"Profile.md",'
                                    '"history_line":"- **2026-08-30** — Uses Helix."}]} thanks')
    assert prose_then["error"] is None and len(prose_then["candidates"]) == 1

    mixed = parse_model_output('[{"target":"Profile.md","history_line":'
                               '"- **2026-08-30** — Uses Helix."},'
                               '{"target":"Profile.md","history_line":"- x — Claude is great."}]')
    assert len(mixed["candidates"]) == 1 and len(mixed["rejected"]) == 1  # denylist drop

    assert validate_candidate({"target": "../etc", "history_line": "- **2026-08-30** — x."}) is None
    v = validate_candidate({"target": "Topics/Foo.md", "history_line": "just prose no format"})
    assert v and v["history_line"].endswith(".")
    assert "type" not in v and "quote" not in v
    assert build_prompt("- **2020-01-01** — old.").endswith("old.")
    assert "do NOT re-emit" in build_prompt("- **2020-01-01** — old.")


if __name__ == "__main__":
    _selfcheck()
    print("ok")
