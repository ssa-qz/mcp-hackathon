# Evaluation harness

Two complementary evaluations, plus two write-ups:
[`evaluation_document.md`](evaluation_document.md) (follows the GSA sample evaluation document) and
[`evaluation_report.md`](evaluation_report.md) (detailed report, hackathon Deliverable 3 / Criterion 2).

| File | What it does |
|---|---|
| `run_eval.py` | **Ground-truth tool eval.** Calls every tool through a real MCP client and checks answers against `oracle.py`, error handling, the full sandbox write lifecycle, and live Census/BLS calls. Reports p50/p95 latency with sample counts. Runs in CI with `--skip-live`. |
| `oracle.py` | Independent ground truth computed straight from the raw `yobYYYY.txt` files and office CSV. It shares no code with the server, so an ingest or query bug fails the eval instead of being repeated in the expected value. |
| `agent_eval.py` | **LLM-in-the-loop eval.** A LangChain agent (Azure OpenAI, temperature 0) answers each question in `agent_cases.yaml` with the MCP tools and the server's `instructions` as its *only* system prompt. Grades tool selection, safety (no forbidden tool, `max_calls` brute-force cap), and answer facts (`must_contain` / `must_match`) deterministically. Supports `--repeats N` (consistency), `--no-tools` (baseline), `--update` (re-run selected cases and log why), and `--regrade` (re-score saved answers without LLM calls). Each run gets a fresh agent and sandbox DB. |
| `agent_cases.yaml` | 20 plain-language cases: dataset, service read, ambiguity, unsupported requests, multi-turn write confirmation, and 4 adversarial cases (prompt injection, brute force, PII extraction, SSN request). |
| `results/` | Committed outputs: `tool_eval.json`, `agent_eval.json` (20 × 5 trials), `agent_eval_no_tools.json` (no-tool baseline), and `agent_eval_baseline.json` (server before the write guardrails). |

## Run

```bash
# From the repository root
python eval/run_eval.py                      # 10 local iterations, 2 live (Census/BLS)
python eval/run_eval.py --skip-live          # offline / CI

pip install pyyaml langchain langchain-openai
python eval/agent_eval.py --env-file .env --repeats 5    # needs AZURE_OPENAI_ENDPOINT, _API_KEY, _API_VERSION, _DEPLOYMENT_NAME
python eval/agent_eval.py --env-file .env --no-tools --output eval/results/agent_eval_no_tools.json
python eval/agent_eval.py --env-file .env --repeats 5 --only ambiguous_henderson --update "why it was re-run"
python eval/agent_eval.py --regrade eval/results/agent_eval.json eval/results/agent_eval_no_tools.json
```

Writes always go to a temporary SQLite file. The evals never touch `data/sandbox_appointments.db`.
BLS v1 allows about 25 requests per day per IP. Set `BLS_API_KEY` for heavier use.
