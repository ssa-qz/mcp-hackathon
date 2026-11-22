# Project verification

- Run commands from `C:\projects\mcp_hackathon` with `C:\projects\python\venv_3.13\Scripts\python.exe` (has FastMCP, pytest, ruff, LangChain, PyYAML). System Python lacks the dependencies.
- Lint: `& "C:\projects\python\venv_3.13\Scripts\python.exe" -m ruff check .`
- Tests: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"; & "C:\projects\python\venv_3.13\Scripts\python.exe" -B -m pytest -q -p no:cacheprovider -p asyncio` (plugin autoload is disabled because an unrelated installed plugin is broken; `-p asyncio` re-enables the one needed).
- Ground-truth tool eval: `& "C:\projects\python\venv_3.13\Scripts\python.exe" -B eval/run_eval.py` (add `--skip-live` offline; CI runs `--skip-live --iterations 3`). Writes `eval/results/tool_eval.json`.
- LLM agent eval (billable Azure OpenAI calls - ask before running): `& "C:\projects\python\venv_3.13\Scripts\python.exe" -B eval/agent_eval.py --env-file C:\projects\python\llmsql\.env --repeats 5`. Writes `eval/results/agent_eval.json`. No-tool baseline: add `--no-tools --output eval/results/agent_eval_no_tools.json`. `agent_eval_baseline.json` is the pre-guardrail server run and must not be overwritten.
- To fix a few cases without re-running everything: `--only <ids> --update "reason"` (the reason is logged in the result file's `reruns`). After changing grading rules in `agent_cases.yaml`, re-score saved answers for free with `--regrade eval/results/agent_eval.json eval/results/agent_eval_no_tools.json`.
- Agent-eval runs must keep a fresh agent and a fresh sandbox DB per trial (shared state made repeat bookings collide). Required facts must not be satisfiable by echoing the question; prefer exact values or `must_match` regexes. Azure's content filter can reject prompts outright (`blocked_by_provider`); such runs did not test the server.
- Evaluation write-ups: `eval/evaluation_document.md` (GSA sample format) and `eval/evaluation_report.md`; keep their numbers in sync with `eval/results/`.
- Run the server: `python main.py` (stdio) or `python main.py --transport http --port 8080` (`/mcp`, `/health`, `/version`). Also `python -m ssa_server.app` with `PYTHONPATH=src`.

# Layout and conventions

- Single unified server in `src/ssa_server/` following the GSA template (`tools/`, `resources/`, `prompts/` with `register(mcp)` aggregators). The old per-domain `servers/` were removed; do not reintroduce duplicate tool definitions.
- `src/ssa_server/core/` holds domain logic and must not import FastMCP. Core raises `ValueError` with agent-readable messages; tool functions are wrapped with `@agent_errors` (`utils.py`) to turn them into `ToolError`. The server uses `mask_error_details=True`.
- Tool parameters use the constrained types in `models.py`; descriptions come from tool docstrings (`Args:`).
- Write tools must keep the "only call after the user has explicitly confirmed" wording; tests and the agent eval check it.
- Appointment lookup/cancel require `phone_last4`; never add a resource or tool that returns an appointment by code alone.
- Upstream (Census/BLS) errors go through `describe_upstream_failure` and must never include URLs or response bodies.
- Dataset year bounds live in `core/baby_name_data.py` (`FIRST_YEAR`, `LAST_YEAR`); bump `LAST_YEAR`, the docstrings, and `scripts/baby_name_ingest.py`'s range when SSA publishes a new year.

# Data

- `data/names/yobYYYY.txt` and `data/FO-Address-Open-Close-Times.csv` are committed public data. `data/names.db` and `data/sandbox_appointments.db` are generated and gitignored.
- `python scripts/baby_name_ingest.py` is safe to rerun: it keeps an existing DB and only adds the `name_totals` summary table if missing. `--force` rebuilds atomically (stop the server first).
- The office CSV has no publication date; its hours are not live status.
- Tests use temp sandbox DBs and pin "today" for booking-window rules; evals use a temp DB too.
- Use synthetic applicant details only.

# Deployment

- HTTP deployments must set `MCP_ALLOWED_HOSTS` to the public hostname (cloud.gov manifest does). `/health` and `/version` are exempt from the Host check for platform probes.
- A platform `PORT` or `DATABRICKS_APP_PORT` makes the server default to HTTP.
- Test both streamable HTTP and SSE when changing transport middleware (`tests/test_transport_security.py`).
