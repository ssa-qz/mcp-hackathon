# Security

This is a hackathon prototype that uses **public SSA data** plus a **sandbox** write workflow. It is not
connected to any SSA production system and must not process real personal information.

## Reporting a vulnerability

Report suspected vulnerabilities privately to the repository maintainers. Do not open a public
issue. Federal vulnerability-disclosure policies of the hosting agency apply.

## Security design

| Area | Control | Where |
|---|---|---|
| Writes | Local sandbox SQLite only; every response is labeled as a sandbox transaction | `core/sandbox_appointments.py` |
| Agent consent | Server `instructions` and write-tool descriptions require explicit user confirmation before booking or cancelling (verified by `eval/agent_eval.py`) | `app.py`, `tools/appointments.py` |
| Record access | `secrets`-generated confirmation codes (31^8 per year); lookup and cancel also need the booking phone's last 4 digits, compared in constant time; a wrong code and a wrong phone return identical responses; no code-only resource | `core/sandbox_appointments.py`, `resources/appointments.py` |
| Input validation | Pydantic-constrained types on every tool parameter; parameterized SQL only | `models.py` |
| Error hygiene | Business-rule failures return curated messages; unexpected exceptions are masked (`mask_error_details=True`); Census/BLS failures never echo URLs, which may contain a street address or API key | `utils.py`, `core/public_services.py` |
| HTTP surface | Host allowlist on `/mcp` (`MCP_ALLOWED_HOSTS`); CORS restricted to loopback origins without credentials; only `/health` and `/version` skip the Host check, and they expose no data | `app.py`, `tests/test_transport_security.py` |
| Secrets | None required. The optional `BLS_API_KEY` is read from the environment; `.env` is gitignored | `.env.example` |

## Known gaps (required before any non-hackathon use)

- `/mcp` has **no authentication**. Put it behind OAuth or an API gateway and complete an ATO review.
- No rate limiting on the MCP endpoint itself.
- No audit-log shipping or retention policy for sandbox records.
- The office directory is an undated CSV snapshot, not an authoritative live source.
