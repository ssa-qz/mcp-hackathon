"""Root entry point for local development and containers.

Run locally:
    python main.py                                # stdio (Claude Desktop, VS Code, MCP Inspector)
    python main.py --transport http --port 8080   # streamable HTTP at /mcp

Or, after `pip install -e .`:
    ssa-mcp-server --transport http
"""

import sys
from pathlib import Path

# Allow running from a source checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ssa_server.app import main, mcp  # noqa: E402,F401  (mcp: object loaded by `fastmcp dev inspector main.py:mcp`)

if __name__ == "__main__":
    main()
