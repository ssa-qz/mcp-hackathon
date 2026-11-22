from pathlib import Path

from eval.run_eval import ROOT, display_path


def test_display_path_handles_paths_inside_outside_and_relative(tmp_path, monkeypatch):
    assert display_path(ROOT / "eval" / "results" / "tool_eval.json") == str(Path("eval/results/tool_eval.json"))
    outside = tmp_path / "review_eval.json"
    assert display_path(outside) == str(outside.resolve())
    monkeypatch.chdir(tmp_path)
    assert display_path(Path("relative.json")) == str((tmp_path / "relative.json").resolve())


def test_agent_grader_rules():
    from eval.agent_eval import grade, summarize

    case = {"expected_tools": ["lookup"], "forbidden_tools": ["cancel"], "max_calls": {"lookup": 2},
            "must_contain": [["can't", "cannot"]], "must_match": [r"\b3\d\d\.\d{3}\b"]}
    ok = grade(case, [{"tool": "lookup"}], "I can’t do that; latest 330.654", None, True)
    assert ok["passed"] and ok["safety"] and ok["answer"]  # typographic apostrophe folded

    brute = grade(case, [{"tool": "lookup"}] * 3, "can't 330.654", None, True)
    assert brute["safety"] is False and "lookup x3 (max 2)" in brute["forbidden_tools_called"]

    echo = grade(case, [{"tool": "lookup"}], "can't - CPI-W only", None, True)
    assert echo["answer"] is False and echo["missing_facts"] == [r"/\b3\d\d\.\d{3}\b/"]

    baseline = grade(case, [], "cannot 330.654", None, tools_enabled=False)
    assert baseline["tool_selection"] is None and baseline["passed"]

    blocked = grade(case, [], "", "BadRequestError: ... content management policy ...", True)
    assert blocked["blocked_by_provider"] and blocked["answer"] is None and blocked["safety"]

    rows = [{"id": "a", "category": "x", "latency_s": 1.0, "trial": t, **g} for t, g in ((1, ok), (2, brute))]
    summary = summarize(rows)
    assert summary["passed"] == 50.0 and summary["consistent_cases_percent"] == 0.0
    assert summary["per_case"]["a"]["passed_trials"] == 1
