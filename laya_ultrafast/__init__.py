"""Laya (local) or Jev (hosted) chooses an observed action. Code owns execution."""

from .agent import Agent
from .browser import Browser

__all__ = ["Agent", "Browser", "LayaMCPServer"]


def __getattr__(name):
    if name == "LayaMCPServer":
        from .mcp import LayaMCPServer

        return LayaMCPServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
