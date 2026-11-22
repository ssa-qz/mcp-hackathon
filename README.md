# SSA Open Data & Public Services MCP Server

[![CI](https://github.com/ssa-qz/mcp-hackathon/actions/workflows/ci.yml/badge.svg)](https://github.com/ssa-qz/mcp-hackathon/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![MCP 2025-11-25](https://img.shields.io/badge/MCP-2025--11--25-green.svg)](https://modelcontextprotocol.io/)

A Model Context Protocol (MCP) server that lets AI agents answer questions with U.S. Social Security
Administration (SSA) open data and complete a sandboxed service workflow (booking a field-office visit).
It covers all three tracks of the GSA MCP Server and AI Agent Government Hackathon (2026) in one server.

Built on the official [`GSA-TTS/mcp-hackathon-template`](https://github.com/GSA-TTS/mcp-hackathon-template)
layout with [FastMCP](https://github.com/jlowin/fastmcp).

---

## 1. Problem

- SSA's public datasets (146 years of national baby-name counts, the directory of 1,182 field offices)
  are published only as downloadable files. There is no API an AI agent can query.
- Common questions mix SSA data with other agencies' data: "Which office serves my address, and what
  congressional district is it in?" (Census Bureau) or "How much will benefits rise next year?" (BLS CPI-W).
- Without grounded tools, chat assistants guess office hours, make up COLA figures, and may say they
  booked an appointment when they did not.

## 2. Solution

The server gives agents a governed set of tools for these tasks:

| Track | What the agent can do | Source |
|---|---|---|
| **Dataset access** | Popularity, yearly top-N, decade trends, comparisons, and all-time leaders for 2,181,032 name records (1880–2025) | SSA `yobYYYY.txt` files → indexed SQLite |
| **Service read** | Find offices and published hours; find offices for a resident's address; resolve county and congressional district; **compute the COLA with the statutory formula** | SSA office CSV, live Census Geocoder, live BLS CPI-W |
| **Service write (sandbox)** | Book, look up, and cancel field-office appointments, with business rules, privacy checks, and a required-documents checklist | Sandbox SQLite (never a production system) |

### Results (full details in [`eval/evaluation_report.md`](eval/evaluation_report.md))

| Evaluation | Result |
|---|---|
| Agent eval: an LLM agent answers 20 plain-language questions (4 adversarial) × 5 trials using only the server's own instructions | **100/100 runs**, 20/20 cases consistent; no-tool baseline 50%; server before guardrails 78.6% |
| Ground-truth tool eval: 35 cases checked against an independent re-read of the raw files, plus live APIs | **35/35** |
| Unit, integration, and transport tests | **122 passing** (CI on Python 3.11–3.13) |
| Local dataset queries (p50) | 7–32 ms (all-time leaderboard: 7 s → 10 ms after pre-aggregation) |

---

## 3. MCP primitives

### Tools (14)

| Tool | Purpose | Annotations |
|---|---|---|
| `get_baby_name_popularity` | Counts, peak year, best/latest rank for a name over a year range | read-only |
| `get_top_names_by_year` | Top N names for a year and sex, with share of published births | read-only |
| `get_name_trend` | Decade-by-decade totals and ranks; peak decade | read-only |
| `compare_names` | Side-by-side metrics for up to 20 names | read-only |
| `get_all_time_popular_names` | All-time leaders, 1880–2025 | read-only |
| `find_field_offices` | Search offices by state, city, ZIP, or name (paginated) | read-only |
| `get_field_office_details` | Address, phone, fax, published weekday hours | read-only |
| `find_offices_near_address` | **Workflow:** geocode a resident's address, then list offices by ZIP → city → state, with the match basis stated | read-only, open-world |
| `geocode_address` | Census coordinates, county FIPS, congressional district, census tract | read-only, open-world |
| `get_cpi_cola_indicators` | Recent CPI-W / CPI-U readings and 12-month change | read-only, open-world |
| `estimate_social_security_cola` | **Workflow:** statutory COLA (Q3 over Q3 CPI-W, rounded to 0.1%), with every input value shown | read-only, open-world |
| `book_field_office_appointment` | Sandbox booking; returns confirmation code and documents checklist | write |
| `lookup_field_office_appointment` | Status by confirmation code **plus last 4 digits of phone** | read-only |
| `cancel_field_office_appointment` | Cancel by code plus phone digits; repeating it is a harmless no-op | write, destructive |

Every parameter is a Pydantic-constrained type (`src/ssa_server/models.py`), so limits such as
`top_n ≤ 50`, at most 20 names, the year range, the ZIP pattern, and the time-slot format appear in the
JSON Schema the agent sees and are enforced before any tool code runs.

### Resources (5 static, 1 template)
`ssa://datasets/baby-names/metadata`, `ssa://datasets/baby-names/all-time-top-10`,
`ssa://field-offices/metadata`, `ssa://field-offices/offices/{office_code}`,
`ssa://field-offices/appointments/metadata`, `public-services://metadata`.


### Prompts (4)
`demographic_name_analysis`, `plan_field_office_visit`, `schedule_appointment_intake`,
`assess_office_jurisdiction_and_cola`.

---

## 4. Technical approach and key decisions

```
src/ssa_server/
├── app.py            # FastMCP server, agent instructions, HTTP app (Host/CORS), CLI entry point
├── config.py         # Settings from environment / .env
├── models.py         # Pydantic-constrained parameter types shared by all tools
├── utils.py          # agent_errors: business-rule failures -> clean ToolError messages
├── routes.py         # /health and /version probes
├── tools/            # MCP tools, one module per domain, each with register(mcp)
├── resources/        # MCP resources, same pattern
├── prompts/          # MCP prompts, same pattern
└── core/             # Domain logic: no FastMCP imports, unit-testable on its own
    ├── baby_name_data.py        field_offices.py        sandbox_appointments.py
    ├── public_services.py       (Census + BLS clients, statutory COLA)
    └── visit_planning.py        (address -> offices workflow)
data/                 # Public SSA source files (names/, office CSV); generated *.db are gitignored
scripts/              # baby_name_ingest.py: builds and validates names.db atomically
eval/                 # Ground-truth tool eval, LLM agent eval, results, evaluation report
tests/                # 122 pytest tests
```

- **Tools are built around user goals, not API endpoints.** `estimate_social_security_cola` applies the
  formula in 42 U.S.C. 415(i) instead of returning raw CPI values. Tests confirm it reproduces SSA's
  published 2025 (2.5%) and 2026 (2.8%) COLAs. `find_offices_near_address` chains Census geocoding with
  the office directory in one call.
- **The server carries its own agent policy.** The MCP `instructions` and the write-tool descriptions
  require explicit user confirmation before booking or cancelling, listing every match when a place name
  is ambiguous, and declining questions about personal records. The agent eval uses *only* these
  instructions as the system prompt. The baseline run exposed an unconfirmed booking; these rules fixed it.
- **Errors tell the agent what to do next.** A broken business rule (weekend date, slot outside hours,
  double booking) returns a specific message the agent can relay. Unexpected exceptions are masked
  (`mask_error_details=True`), and upstream errors never echo URLs, which could contain an address or key.
- **The data stays faithful to the source.** ZIP codes and office codes stay strings (leading zeros
  survive), NUL padding is stripped, hours are reproduced as published, and invalid published hours
  are refused rather than "fixed".
- **Performance.** Indexed SQLite plus a `name_totals` summary table built at ingest. CPI responses
  are cached for 6 hours to stay within BLS's daily limits.

---

## 5. Quick start

See [QUICKSTART.md](QUICKSTART.md) for client setup (Claude Desktop, VS Code, MCP Inspector).
The video demo uses [`demo/chat.py`](demo/chat.py), an optional terminal client that discovers the
server's MCP tools and instructions and displays tool calls. See the
[terminal demo setup](QUICKSTART.md#terminal-demo-azure-openai) to run it.

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/baby_name_ingest.py                 # one-time: builds data/names.db (safe to rerun)

python main.py                                     # stdio
python main.py --transport http --port 8080        # streamable HTTP at /mcp, probe at /health
```

### Verify

```bash
pip install pytest pytest-asyncio ruff
ruff check . && pytest -q
python eval/run_eval.py                 # ground-truth tool eval (add --skip-live offline)
pip install pyyaml langchain langchain-openai
python eval/agent_eval.py --env-file path/to/.env   # LLM agent eval (Azure OpenAI)
```

### Configuration (`.env.example`)

| Variable | Default | Purpose |
|---|---|---|
| `MCP_TRANSPORT` / `HOST` / `PORT` | `stdio` / `127.0.0.1` / `8080` | Transport and bind address; a platform `PORT` or `DATABRICKS_APP_PORT` switches to HTTP |
| `MCP_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Host headers accepted on `/mcp`; **set this to your public hostname when deployed** |
| `BLS_API_KEY` | unset | Optional free BLS key (v2 API, 500 instead of ~25 requests/day) |
| `BABY_NAMES_DB_PATH`, `FIELD_OFFICES_CSV_PATH`, `APPOINTMENTS_DB_PATH` | `data/...` | Data locations |

### Deploy
`Dockerfile` (builds `names.db` into the image) and `manifest.yaml` (cloud.gov). Both serve
streamable HTTP. Set `MCP_ALLOWED_HOSTS` to the public route.

---

## 6. Security

See [SECURITY.md](SECURITY.md). In brief: public data only; writes go only to a local sandbox database;
confirmation codes cannot be guessed and lookups require phone verification; Host allowlisting and
loopback-only CORS; parameterized SQL; sanitized errors; no authentication on `/mcp` (hackathon
posture). Add authentication and complete an ATO review before any non-sandbox use.

## 7. Limitations

- The field-office CSV is an undated snapshot. Hours are not live, and matches use the office's
  address, not SSA's service-area assignment.
- Baby-name counts cover published records only (at least 5 births per name per year), not all births.
- The COLA tool assumes the base is the preceding year's Q3. SSA's announcement is the official figure.
- The agent eval uses one model at temperature 0. Results may vary across models and runs.
