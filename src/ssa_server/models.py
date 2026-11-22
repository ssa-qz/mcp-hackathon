"""Pydantic-constrained input types shared by every MCP tool.

Tools annotate their parameters with these types so that limits are published in the
JSON Schema the agent sees *and* enforced by Pydantic before any tool code runs.
Human-readable parameter descriptions come from each tool's docstring ``Args:`` section.
"""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from ssa_server.core.baby_name_data import FIRST_YEAR, LAST_YEAR
from ssa_server.core.sandbox_appointments import ServiceType

__all__ = ["ServiceType"]  # re-exported so tools import every parameter type from one module

# ---------------------------------------------------------------------------
# Baby names (dataset access)
# ---------------------------------------------------------------------------
NameText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
SexType = Literal["M", "F"]
DatasetYear = Annotated[int, Field(ge=FIRST_YEAR, le=LAST_YEAR)]
TopNYear = Annotated[int, Field(ge=1, le=100)]
TopNAllTime = Annotated[int, Field(ge=1, le=50)]
NameList = Annotated[list[NameText], Field(min_length=1, max_length=20)]

# ---------------------------------------------------------------------------
# Field offices (service read)
# ---------------------------------------------------------------------------
SearchText = NameText
StateCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z]{2}$")]
OfficeZip = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, pattern=r"^[0-9]{5}$")]
OfficeCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10)]
PageLimit = Annotated[int, Field(ge=1, le=50, strict=True)]
PageOffset = Annotated[int, Field(ge=0, strict=True)]
NearbyLimit = Annotated[int, Field(ge=1, le=20)]

# ---------------------------------------------------------------------------
# Appointments (service write sandbox)
# ---------------------------------------------------------------------------
ConfirmationCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=30)]
DateString = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
TimeSlotString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^(0?[1-9]|1[0-2]):[0-5][0-9]\s*([AaPp][Mm])$"),
]
PhoneLast4 = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{4}$")]
ApplicantName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
PhoneString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=25)]
NotesText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
ReasonText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]

# ---------------------------------------------------------------------------
# Public services (Census / BLS)
# ---------------------------------------------------------------------------
AddressString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)]
InflationSeries = Literal["CPI-W", "CPI-U"]
RecentMonths = Annotated[int, Field(ge=1, le=24)]
