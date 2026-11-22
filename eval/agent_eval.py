"""LLM-in-the-loop evaluation: can a real agent use this server correctly?

For every case in ``agent_cases.yaml`` a fresh LangChain agent (Azure OpenAI, temperature 0)
receives a plain-language question. Its only system prompt is the MCP server's own
``instructions`` string, and its only tools are the server's MCP tools with their published
names, descriptions, and JSON schemas. The score therefore measures the server's
agent-facing design, not a hand-tuned client prompt.

Grades (deterministic, no LLM judge):
  tool_selection  every expected tool was called
  safety          no forbidden tool was called (e.g. a write before user confirmation)
  answer          every required fact appears in the final answer

Configuration (environment or --env-file):
  AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_API_VERSION,
  AZURE_OPENAI_DEPLOYMENT_NAME

Usage:
    python eval/agent_eval.py [--env-file PATH] [--cases eval/agent_cases.yaml] [--only ID ...]
"""

import argparse
import asyncio
import datetime
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

import httpx  # noqa: E402
import yaml  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from fastmcp import Client  # noqa: E402
from langchain.agents import create_agent  # noqa: E402
from langchain_core.tools import StructuredTool, ToolException  # noqa: E402
from langchain_openai import AzureChatOpenAI  # noqa: E402

from eval.oracle import Oracle  # noqa: E402
from eval.run_eval import display_path, next_open_weekday  # noqa: E402
from ssa_server.app import mcp  # noqa: E402
from ssa_server.config import settings  # noqa: E402

# Baseline prompt: same domain framing, no server instructions and no tools, so the comparison
# isolates what the MCP server adds.
NO_TOOL_SYSTEM_PROMPT = (
    "You are a helpful assistant answering questions about U.S. Social Security Administration "
    "public information and services."
)

PROVIDER_FILTER_MARKER = "content management policy"  # Azure OpenAI content-filter rejection text

AZURE_VARS = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_API_VERSION",
              "AZURE_OPENAI_DEPLOYMENT_NAME")


def create_llm(http_client: httpx.Client, http_async_client: httpx.AsyncClient) -> AzureChatOpenAI:
    missing = [name for name in AZURE_VARS if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Missing Azure OpenAI configuration: {', '.join(missing)} (use --env-file)")
    return AzureChatOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ["AZURE_OPENAI_API_VERSION"],
        azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
        http_client=http_client,
        http_async_client=http_async_client,
        temperature=0,
    )


def mcp_tools_for_langchain(client: Client, tools: list) -> list[StructuredTool]:
    """Expose MCP tools to LangChain unchanged: same names, descriptions, and JSON schemas."""

    def make_call(tool_name: str):
        async def _call(**kwargs: Any) -> Any:
            result = await client.call_tool(tool_name, kwargs, raise_on_error=False)
            if result.is_error:
                raise ToolException(" ".join(getattr(b, "text", "") for b in result.content))
            return result.structured_content or " ".join(getattr(b, "text", "") for b in result.content)
        return _call

    return [
        StructuredTool(name=t.name, description=t.description or t.name, args_schema=t.input_schema,
                       coroutine=make_call(t.name), handle_tool_error=True)
        for t in tools
    ]


def normalize(text: str) -> str:
    """Lower-case and fold typographic quotes/dashes so "can’t" matches "can't"."""
    return text.lower().translate(str.maketrans("‘’“”–—", "''\"\"--"))


def contains(answer: str, requirement: str | list[str]) -> bool:
    options = requirement if isinstance(requirement, list) else [requirement]
    return any(normalize(option) in normalize(answer) for option in options)


async def run_case(agent, case: dict, tools_enabled: bool = True) -> dict:
    turns = case.get("turns") or [case["question"]]
    messages: list = []
    calls: list[dict] = []
    start = time.perf_counter()
    try:
        for turn_index, turn in enumerate(turns, start=1):
            history_len = len(messages) + 1
            response = await agent.ainvoke({"messages": [*messages, {"role": "user", "content": turn}]})
            messages = response["messages"]
            calls += [{"turn": turn_index, "tool": tc["name"], "args": tc.get("args", {})}
                      for m in messages[history_len:] for tc in (getattr(m, "tool_calls", None) or [])]
        answer, error = str(messages[-1].content), None
    except Exception as exc:  # an agent/LLM failure is a failed case, not a crashed run
        answer, error = "", f"{type(exc).__name__}: {exc}"[:300]
    return {"id": case["id"], "category": case["category"],
            **grade(case, calls, answer, error, tools_enabled),
            "tool_calls": calls, "error": error,
            "latency_s": round(time.perf_counter() - start, 2), "final_answer": answer}


def grade(case: dict, calls: list[dict], answer: str, error: str | None, tools_enabled: bool) -> dict:
    """Deterministic grading of one run; also used by --regrade on saved answers."""
    called = [c["tool"] for c in calls]
    missing_tools = [t for t in case.get("expected_tools", []) if t not in called]
    forbidden_used = [t for t in case.get("forbidden_tools", []) if t in called]
    forbidden_used += [f"{t} x{called.count(t)} (max {n})" for t, n in case.get("max_calls", {}).items()
                       if called.count(t) > n]
    missing_facts = [r for r in case.get("must_contain", []) if not contains(answer, r)]
    missing_facts += [f"/{p}/" for p in case.get("must_match", []) if not re.search(p, answer)]
    # The LLM provider's own content filter rejected the prompt: the request never reached the agent
    # or the server, so only "nothing was written" is gradable. Counted and reported separately.
    blocked = bool(error and PROVIDER_FILTER_MARKER in error)
    grades = {"tool_selection": None if not tools_enabled or blocked else not missing_tools,
              "safety": not forbidden_used,
              "answer": None if blocked else not missing_facts and error is None}
    return {"passed": all(v for v in grades.values() if v is not None), **grades,
            "blocked_by_provider": blocked, "tools_called": called, "missing_tools": missing_tools,
            "forbidden_tools_called": forbidden_used, "missing_facts": [] if blocked else missing_facts}


def regrade(path: Path, cases_path: Path) -> dict:
    """Re-score a saved results file against the current agent_cases.yaml (no LLM calls)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    specs = {c["id"]: c for c in load_cases(cases_path, None)}
    tools_enabled = data.get("mode") != "no-tool baseline"
    for row in data["cases"]:
        row.update(grade(specs[row["id"]], row.get("tool_calls", []), row["final_answer"], row["error"],
                         tools_enabled))
    data["summary"] = print_report(data["cases"])
    data["regraded_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data["summary"]


def load_cases(path: Path, only: list[str] | None) -> list[dict]:
    weekday = next_open_weekday(Oracle().office("A00")).isoformat()
    raw = path.read_text(encoding="utf-8").replace("{next_open_weekday}", weekday)
    cases = yaml.safe_load(raw)["cases"]
    return [c for c in cases if not only or c["id"] in only]


def rate(rows: list[dict], metric: str) -> float | None:
    graded = [r[metric] for r in rows if r[metric] is not None]
    return round(100 * sum(graded) / len(graded), 1) if graded else None


def summarize(rows: list[dict]) -> dict:
    """Aggregate over every trial, plus per-case consistency across repeated trials."""
    by_case: dict[str, list[dict]] = {}
    for r in rows:
        by_case.setdefault(r["id"], []).append(r)
    latencies = sorted(r["latency_s"] for r in rows)
    per_case = {
        case_id: {"category": trials[0]["category"], "passed_trials": sum(t["passed"] for t in trials),
                  "trials": len(trials), "mean_latency_s": round(sum(t["latency_s"] for t in trials) / len(trials), 2),
                  "mean_tool_calls": round(sum(len(t["tools_called"]) for t in trials) / len(trials), 2)}
        for case_id, trials in by_case.items()
    }
    return {
        **{metric: rate(rows, metric) for metric in ("tool_selection", "safety", "answer", "passed")},
        "cases": len(by_case),
        "trials_per_case": max(len(t) for t in by_case.values()),
        "total_runs": len(rows),
        "consistent_cases_percent": round(
            100 * sum(c["passed_trials"] == c["trials"] for c in per_case.values()) / len(per_case), 1),
        "mean_latency_s": round(sum(latencies) / len(latencies), 2),
        "p95_latency_s": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))],
        "mean_tool_calls": round(sum(len(r["tools_called"]) for r in rows) / len(rows), 2),
        "provider_blocked_runs": sum(bool(r.get("blocked_by_provider")) for r in rows),
        "per_case": per_case,
    }


def print_report(rows: list[dict]) -> dict:
    summary = summarize(rows)
    print(f"\n{'Case':<34} {'Passed':>8} {'Mean s':>7} {'Calls':>6}")
    print("-" * 60)
    for case_id, c in summary["per_case"].items():
        print(f"{case_id:<34} {c['passed_trials']:>3}/{c['trials']:<4} {c['mean_latency_s']:>7.1f} "
              f"{c['mean_tool_calls']:>6.1f}")
    for r in rows:
        if not r["passed"]:
            details = {k: r[k] for k in ("missing_tools", "forbidden_tools_called", "missing_facts", "error") if r[k]}
            print(f"    - {r['id']} (trial {r.get('trial', 1)}): {details}")
    print("-" * 60)
    print(f"Tool selection {summary['tool_selection']}% | Safety {summary['safety']}% | "
          f"Answer {summary['answer']}% | Fully passed {summary['passed']}% of {summary['total_runs']} runs | "
          f"Consistent (all trials pass) {summary['consistent_cases_percent']}% of {summary['cases']} cases")
    return summary


async def main(args: argparse.Namespace) -> int:
    if args.regrade:
        for path in args.regrade:
            print(f"\nRe-grading {display_path(path)}")
            regrade(path, args.cases)
        return 0
    if args.update and not args.only:
        raise SystemExit("--update requires --only <case ids>")
    args.output = args.output.resolve()
    if args.env_file:
        load_dotenv(args.env_file)
    cases = load_cases(args.cases, args.only)

    with tempfile.TemporaryDirectory() as sandbox_dir:
        async with Client(mcp) as client, httpx.AsyncClient(trust_env=True, timeout=120) as async_http:
            with httpx.Client(trust_env=True, timeout=120) as sync_http:
                tools = [] if args.no_tools else mcp_tools_for_langchain(client, await client.list_tools())
                system_prompt = NO_TOOL_SYSTEM_PROMPT if args.no_tools else mcp.instructions
                llm = create_llm(sync_http, async_http)
                rows = []
                for case in cases:
                    for trial in range(1, args.repeats + 1):
                        # Fresh agent AND fresh sandbox DB per run: neither memory nor bookings carry
                        # over between questions or trials (a shared DB made repeat bookings collide).
                        settings.APPOINTMENTS_DB_PATH = Path(sandbox_dir) / f"{case['id']}_{trial}.db"
                        agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)
                        rows.append({**await run_case(agent, case, tools_enabled=not args.no_tools),
                                     "trial": trial})
                        print(f"  {case['id']} [{trial}/{args.repeats}]: "
                              f"{'PASS' if rows[-1]['passed'] else 'FAIL'}", flush=True)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if args.update:
        # Replace only the re-run cases inside an existing results file; keep the case order.
        data = json.loads(args.output.read_text(encoding="utf-8"))
        rerun_ids = {c["id"] for c in cases}
        order = [c["id"] for c in load_cases(args.cases, None)]
        rows = sorted([r for r in data["cases"] if r["id"] not in rerun_ids] + rows,
                      key=lambda r: (order.index(r["id"]), r.get("trial", 1)))
        data.setdefault("reruns", []).append({"at": now, "cases": sorted(rerun_ids), "reason": args.update})
    else:
        data = {
            "generated_at": now,
            "model_deployment": os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
            "mode": "no-tool baseline" if args.no_tools else "MCP tools",
            "system_prompt": "neutral assistant prompt (no tools)" if args.no_tools else "MCP server instructions only",
        }
    data["summary"] = print_report(rows)
    data["cases"] = rows
    summary = data["summary"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {display_path(args.output)}")
    return 0 if summary["passed"] == 100.0 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env",
                        help="dotenv file with AZURE_OPENAI_* settings (default: ./.env)")
    parser.add_argument("--cases", type=Path, default=ROOT / "eval" / "agent_cases.yaml")
    parser.add_argument("--only", nargs="*", help="run only these case ids")
    parser.add_argument("--repeats", type=int, default=1, help="trials per case, for consistency (default 1)")
    parser.add_argument("--no-tools", action="store_true",
                        help="baseline: same model and questions, no MCP tools, neutral system prompt")
    parser.add_argument("--update", metavar="REASON",
                        help="re-run only --only cases and replace their rows in --output, logging REASON")
    parser.add_argument("--regrade", type=Path, nargs="+", metavar="RESULTS_JSON",
                        help="re-score saved result files with the current cases (no LLM calls)")
    parser.add_argument("--output", type=Path, default=ROOT / "eval" / "results" / "agent_eval.json")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
