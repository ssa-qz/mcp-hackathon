"""Server entry point: builds the FastMCP server, the HTTP app, and picks a transport.

Run with ``python main.py``, ``python -m ssa_server.app``, or the ``ssa-mcp-server`` script.
"""

import argparse
import os
from typing import Literal

import uvicorn
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Route

from ssa_server.config import settings
from ssa_server.core import DATA_DIR
from ssa_server.prompts import register_prompts
from ssa_server.resources import register_resources
from ssa_server.routes import health_endpoint, version_endpoint
from ssa_server.tools import register_tools

LOCAL_ORIGIN_PATTERN = r"https?://(?:localhost|127\.0\.0\.1)(?::[0-9]+)?"
PROBE_PATHS = frozenset({"/health", "/version"})


class ProbeExemptTrustedHostMiddleware(TrustedHostMiddleware):
    """Enforce Host allowlisting on MCP traffic while letting platform health probes through.

    Container platforms (cloud.gov, Code Engine, Docker HEALTHCHECK) probe by pod/container IP,
    which can never be listed in advance. Probe routes expose no data or tools.
    """

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"] in PROBE_PATHS:
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

SERVER_INSTRUCTIONS = """\
U.S. Social Security Administration (SSA) open data and public services.

Capabilities:
1. Dataset access - SSA national baby-name counts, ranks, decade trends, comparisons, and all-time
   leaders for 1880-2025 (published records only; names with fewer than 5 births in a year are omitted,
   so a missing name does not mean zero births).
2. Service read - the SSA field-office directory (address, phone, published weekday hours); Census
   Bureau geocoding of an address to county, congressional district, and census tract; live BLS CPI-W
   data that drives the annual Social Security cost-of-living adjustment (COLA).
3. Service write (sandbox) - book, look up, and cancel in-person field-office appointments in a
   demonstration database. Nothing is sent to real SSA scheduling systems.

Rules for agents:
- Confirm before writing. Before book_field_office_appointment or cancel_field_office_appointment,
  summarize the exact details and wait for the user to explicitly confirm in a later message. A request
  to book is not a confirmation. In that summary, give the date as a calendar date (YYYY-MM-DD with the
  weekday), not a phrase like "next Monday", and format the phone as (XXX) XXX-XXXX. After booking,
  show the confirmation code and the document checklist.
- Do not choose for the user. When a search returns more than one office (e.g. Henderson exists in NV,
  NC, and KY), list every match with its state and ask which one they mean.
- Lookups and cancellations need the confirmation code and the last 4 digits of the booking phone,
  both supplied by the user. Never guess them.
- No personal records. This server cannot see anyone's earnings, benefit amounts, payments, or claim
  status; say so plainly and point the user to ssa.gov/myaccount instead of estimating.
- Office hours come from an undated directory snapshot, not live status; advise confirming before a visit.
- Cite the source (SSA dataset, field-office directory, Census Geocoder, BLS) and the years or series used.
- Answer the user directly. Do not add commentary about your own process or plans, such as "One note: I'll ...".
"""


def create_server() -> FastMCP:
    """Build and configure the unified FastMCP server instance."""
    server = FastMCP(
        name="SSA MCP Server",
        instructions=SERVER_INSTRUCTIONS,
        # Unexpected exceptions reach clients as a generic message; expected business-rule
        # failures are raised as ToolError by utils.agent_errors and keep their guidance.
        mask_error_details=True,
    )
    register_tools(server)
    register_prompts(server)
    register_resources(server)
    return server


# Default server singleton instance
mcp = create_server()


def create_app(
    stateless: bool = True,
    transport: Literal["http", "sse"] = "http",
    allowed_hosts: list[str] | None = None,
):
    """Build an ASGI application with Host allowlisting, loopback-only CORS, and health probes.

    Args:
        allowed_hosts: Accepted Host headers for MCP traffic. Defaults to MCP_ALLOWED_HOSTS.
    """
    middleware = [
        Middleware(
            ProbeExemptTrustedHostMiddleware,
            allowed_hosts=allowed_hosts or settings.ALLOWED_HOSTS,
            www_redirect=False,
        ),
        Middleware(
            CORSMiddleware,
            allow_origin_regex=LOCAL_ORIGIN_PATTERN,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Content-Type",
                "MCP-Protocol-Version",
                "MCP-Session-Id",
                "Last-Event-ID",
            ],
            expose_headers=["mcp-session-id", "mcp-protocol-version"],
            allow_credentials=False,
        ),
    ]

    base_app = mcp.http_app(
        middleware=middleware,
        transport=transport,
        stateless_http=stateless,
    )

    # Attach /health and /version probe routes to Starlette app router
    base_app.router.routes.insert(0, Route("/health", health_endpoint, methods=["GET"]))
    base_app.router.routes.insert(1, Route("/version", version_endpoint, methods=["GET"]))

    return base_app


app = create_app()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    # A platform-injected port (Databricks Apps, Cloud Foundry/cloud.gov, Code Engine) means the
    # server must speak HTTP even if MCP_TRANSPORT was left at its stdio default.
    platform_port = os.getenv("DATABRICKS_APP_PORT") or os.getenv("PORT")
    default_transport = os.getenv("MCP_TRANSPORT") or ("http" if platform_port else "stdio")
    parser = argparse.ArgumentParser(description="SSA MCP Server - GSA MCP Hackathon")
    parser.add_argument("--transport", choices=["stdio", "http", "sse"], default=default_transport,
                        help="stdio (default), http (streamable HTTP at /mcp), or legacy sse (/sse)")
    parser.add_argument("--port", type=int, default=int(platform_port or settings.PORT),
                        help="HTTP/SSE port (default: platform port, PORT, or 8080)")
    parser.add_argument("--host", default=settings.HOST,
                        help="HTTP/SSE bind address (default: HOST or 127.0.0.1; use 0.0.0.0 in containers)")
    return parser.parse_args(argv)


def ensure_baby_name_database() -> None:
    """Build names.db from the bundled SSA files if it is missing. An existing database is untouched."""
    db_path = settings.BABY_NAMES_DB_PATH
    if db_path.exists():
        return
    from scripts.baby_name_ingest import ingest_baby_names  # available in a repository checkout

    print(f"{db_path} not found; building it from the bundled SSA files (one-time).")
    ingest_baby_names(DATA_DIR / "names", db_path)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    ensure_baby_name_database()
    if args.transport == "stdio":
        mcp.run()
        return
    endpoint = "mcp" if args.transport == "http" else "sse"
    print(f"Starting SSA MCP Server ({args.transport}) on http://{args.host}:{args.port}/{endpoint}")
    print(f"Health check available at http://{args.host}:{args.port}/health")
    uvicorn.run(create_app(transport=args.transport), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
