"""Hermes Workspace plugin — agent-side entry. Knowledge has no agent half yet; Creator registers here."""


def register(ctx) -> None:
    from . import cr_tools
    cr_tools.register(ctx)
