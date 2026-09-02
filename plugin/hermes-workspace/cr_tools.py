"""Creator agent tools + system-prompt section. Thin: parse args, delegate to cr_store."""
import json
import os

try:
    from . import cr_store
except ImportError:
    import cr_store  # selftest / explicit-path context

try:
    from tools.registry import tool_error, tool_result
except ImportError:  # selftest env — no Hermes on the path
    def tool_result(data=None, **kwargs):
        return json.dumps(data if data is not None else kwargs, ensure_ascii=False)

    def tool_error(message, **extra):
        out = {"error": str(message)}
        out.update(extra)
        return json.dumps(out, ensure_ascii=False)


# --- §5.8 system-prompt section (verbatim; no leading '#', asserted < 3900) ---
PROMPT_SECTION = """\
You have `create_artifact` / `update_artifact` / `read_artifact` (Creator).
Use them for substantial standalone content the user will want to preview,
revise, and keep — a web page, an SVG, a code file of ~40+ lines, a Markdown
document, a Mermaid diagram (and, when available, a React component). Put the
content in the artifact, not in your reply — a one-line pointer is enough.
- Choose a short stable kebab-case identifier per artifact and reuse it with
  `update_artifact` on later turns; each update is a version the user can step
  through and revert.
- Call `read_artifact` before an update if the user may have edited it.
- `type=html` may be a full document or a fragment. `type=code` takes a
  `language`. Use `type=react` for an interactive component (Phase 2+).
- Small snippets, inline examples, and command output stay in your reply as
  normal code blocks."""


# --- §5.7 tool schemas ---
SCHEMAS: dict[str, dict] = {
    "create_artifact": {
        "name": "create_artifact",
        "description": (
            "Create a Creator artifact (new identifier) or append a version "
            "(existing identifier keeps its original type/ext)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "short stable kebab-case id; reuse to version",
                },
                "type": {"type": "string", "enum": list(cr_store.VALID_TYPES)},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "language": {
                    "type": "string",
                    "description": "for type=code, e.g. python",
                },
            },
            "required": ["identifier", "type", "title", "content"],
        },
    },
    "update_artifact": {
        "name": "update_artifact",
        "description": (
            "Append a new version to an existing artifact. Identical normalized "
            "content is a no-op."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["identifier", "content"],
        },
    },
    "read_artifact": {
        "name": "read_artifact",
        "description": "Return an artifact's current full content and metadata.",
        "parameters": {
            "type": "object",
            "properties": {"identifier": {"type": "string"}},
            "required": ["identifier"],
        },
    },
}


def _h_create(args, **kwargs):
    try:
        return tool_result(cr_store.do_create(args, kwargs.get("session_id", "")))
    except (cr_store.StoreNotFound, cr_store.StoreBadInput, cr_store.StoreBusy) as e:
        return tool_error(str(e))


def _h_update(args, **kwargs):
    try:
        return tool_result(cr_store.do_update(args, kwargs.get("session_id", "")))
    except (cr_store.StoreNotFound, cr_store.StoreBadInput, cr_store.StoreBusy) as e:
        return tool_error(str(e))


def _h_read(args, **kwargs):
    try:
        return tool_result(cr_store.do_read(args))
    except (cr_store.StoreNotFound, cr_store.StoreBadInput, cr_store.StoreBusy,
            cr_store.StoreGone) as e:
        return tool_error(str(e))


_HANDLERS = {
    "create_artifact": _h_create,
    "update_artifact": _h_update,
    "read_artifact": _h_read,
}


def register(ctx) -> None:
    for name, handler in _HANDLERS.items():
        ctx.register_tool(
            name=name,
            toolset="creator",
            schema=SCHEMAS[name],
            handler=handler,
            description=SCHEMAS[name]["description"],
        )
    ctx.register_system_prompt_section("creator-artifacts", PROMPT_SECTION)


def _selfcheck() -> None:
    import tempfile
    assert len(PROMPT_SECTION) < 3900 and not PROMPT_SECTION.lstrip().startswith("#")
    saved = os.environ.get("HERMES_HOME")
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["HERMES_HOME"] = os.path.join(d, "home")
            out = _h_create({"identifier": "t", "type": "code", "title": "T",
                             "content": "x=1"}, session_id="s1")
            assert "created" in out
            bad = _h_update({"identifier": "missing", "content": "y"}, session_id="s1")
            assert "no artifact" in bad.lower()
            good = _h_read({"identifier": "t"})
            assert json.loads(good)["content"] == "x=1"
            # Fix 5: a row-exists/file-missing artifact maps to a clean
            # tool_error (StoreGone), not an unhandled FileNotFoundError.
            meta = cr_store._meta("t")
            os.remove(cr_store._creator_dir() / meta[0] / f"v{meta[6]}.{meta[7]}")
            gone = _h_read({"identifier": "t"})
            assert "error" in json.loads(gone)
    finally:
        if saved is None: os.environ.pop("HERMES_HOME", None)
        else: os.environ["HERMES_HOME"] = saved


if __name__ == "__main__":
    _selfcheck()
    print("ok")
