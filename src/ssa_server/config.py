import os
from pathlib import Path
from typing import Literal
from dotenv import load_dotenv

from ssa_server.core import DATA_DIR

# Load local .env if present
load_dotenv()


class Settings:
    """Runtime configuration for SSA MCP Server."""

    # Server network settings
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8080"))
    TRANSPORT: Literal["stdio", "http", "sse"] = os.getenv(
        "MCP_TRANSPORT", "stdio"
    ).lower()  # type: ignore
    # Host headers accepted on the MCP endpoint (comma-separated; '*.example.gov' wildcards allowed).
    # Deployments must add their public hostname, e.g. 'ssa-mcp-server.app.cloud.gov'.
    ALLOWED_HOSTS: list[str] = [
        host.strip()
        for host in os.getenv("MCP_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
        if host.strip()
    ]

    # Data paths
    BABY_NAMES_DB_PATH: Path = Path(
        os.getenv("BABY_NAMES_DB_PATH", str(DATA_DIR / "names.db"))
    )
    FIELD_OFFICES_CSV_PATH: Path = Path(
        os.getenv(
            "FIELD_OFFICES_CSV_PATH",
            str(DATA_DIR / "FO-Address-Open-Close-Times.csv"),
        )
    )
    APPOINTMENTS_DB_PATH: Path = Path(
        os.getenv(
            "APPOINTMENTS_DB_PATH",
            str(DATA_DIR / "sandbox_appointments.db"),
        )
    )

    # External APIs
    CENSUS_BENCHMARK: str = os.getenv("CENSUS_BENCHMARK", "Public_AR_Current")
    CENSUS_VINTAGE: str = os.getenv("CENSUS_VINTAGE", "Current_Current")


settings = Settings()
