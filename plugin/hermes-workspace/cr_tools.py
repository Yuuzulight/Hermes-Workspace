"""Creator agent tools + system-prompt section."""


def _register_tools(ctx):
    """Register Creator tools with Hermes runtime."""
    # Import here to avoid circular imports at module load time
    from . import cr_store as cs

    def create_artifact(identifier, type_name, language=None, title=None, origin="agent"):
        return cs.create_artifact(
            identifier=identifier,
            type_name=type_name,
            language=language,
            title=title,
            origin=origin,
        )

    def update_artifact(dir_path, type_name, language=None, title=None, origin="agent"):
        return cs.update_artifact(
            dir_path=dir_path,
            type_name=type_name,
            language=language,
            title=title,
            origin=origin,
        )

    def read_artifact(dir_path):
        return cs.read_artifact(dir_path)

    def list_versions(identifier):
        return cs.list_versions(identifier)

    def delete_artifact(identifier):
        cs.delete_artifact(_ensure_identifier(identifier))

    def _ensure_identifier(raw: str) -> str:
        """Sanitize artifact identifier per spec §5.1."""
        if not raw or not isinstance(raw, str):
            return "artifact"
        s = re.sub(r"[^a-z0-9._-]+", "-", raw.lower())
        s = re.sub(r"-{2,}", "-", s)
        s = s.strip("-") or "artifact"
        if ".." in s or "\\" in s:
            return "artifact"
        if not s[0].isalnum():
            s = "a" + s
        return (s[:64] or "artifact").rstrip("-")

    def _compute_sha256(content: str) -> str:
        """Compute SHA-256 of normalized content."""
        import hashlib
        from . import cr_store as cs
        return hashlib.sha256(cs.normalize(content).encode("utf-8")).hexdigest()

    # Register with Hermes runtime (expects tools to be callable functions)
    ctx.register_tool("create_artifact", create_artifact)
    ctx.register_tool("update_artifact", update_artifact)
    ctx.register_tool("read_artifact", read_artifact)
    ctx.register_tool("list_versions", list_versions)
    ctx.register_tool("delete_artifact", delete_artifact)
    ctx.register_tool("_compute_sha256", _compute_sha256)


def register(ctx):
    """Entry point called by Hermes plugin loader."""
    _register_tools(ctx)
