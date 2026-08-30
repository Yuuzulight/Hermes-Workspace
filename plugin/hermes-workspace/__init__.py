"""Hermes Workspace plugin — agent-side entry."""


def register(ctx):
    from . import cr_tools
    cr_tools.register(ctx)
