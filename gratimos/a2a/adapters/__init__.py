"""Adapters that put other vendors' agents behind the A2A interface."""

from .claude import (
    DEFAULT_MODEL,
    ClaudeConfig,
    ClaudeExecutor,
    ClaudeTool,
    claude_agent,
    hub_tools,
)
from .uipath import (
    JOB_STATES,
    UiPathConfig,
    UiPathConnection,
    UiPathOrchestrator,
    UiPathTransport,
    job_to_task,
)

__all__ = [
    "DEFAULT_MODEL",
    "JOB_STATES",
    "ClaudeConfig",
    "ClaudeExecutor",
    "ClaudeTool",
    "UiPathConfig",
    "UiPathConnection",
    "UiPathOrchestrator",
    "UiPathTransport",
    "claude_agent",
    "hub_tools",
    "job_to_task",
]
