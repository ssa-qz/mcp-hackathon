"""Shared helpers for the MCP tool layer."""

import functools
from typing import Callable, TypeVar

from fastmcp.exceptions import ToolError

F = TypeVar("F", bound=Callable)


def agent_errors(fn: F) -> F:
    """Turn expected business-rule failures into clean, agent-actionable tool errors.

    ``core`` raises ``ValueError`` with messages written for the agent (what was wrong and how
    to fix it). Re-raising them as ``ToolError`` sends that message to the client without a
    server-side stack trace. Any *other* exception stays unexpected: FastMCP logs it and, with
    ``mask_error_details=True``, returns only a generic error so internals never leak.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            raise ToolError(str(exc)) from None

    return wrapper  # type: ignore[return-value]
