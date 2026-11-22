"""Domain logic (data access, business rules, upstream API clients), independent of MCP.

Nothing in this package imports FastMCP, so every function can be unit-tested and reused
outside the server. The MCP layer (tools/, resources/, prompts/) wraps these functions.
"""

from pathlib import Path

# Repository-level data/ directory (src/ssa_server/core/ -> repo root). Runtime paths are
# configurable through ssa_server.config; these defaults serve tests and direct use.
DATA_DIR = Path(__file__).resolve().parents[3] / "data"
