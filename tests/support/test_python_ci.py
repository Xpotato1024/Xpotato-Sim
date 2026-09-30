"""CIのjob状態、集合、鮮度、実行完了を故障注入で検証する。"""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("python_ci_under_test", ROOT / ".github/python_ci.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)

NODES = ["tests/runtime/test_future.py::test_new", "tests/new_axis/test_future.py::test_new"]
IDENTITY = {"sha": "revision", "run": "123", "attempt": "2", "lock": "lock"}
NEEDS = {"python-tests": {"result": "success"}}


def reports():
    return [{"shard": shard, "identity": dict(IDENTITY), "exitstatus": 0,
             "collected": list(NODES), "selected": [n for n in NODES if ci.owner(n) == shard],
             "executed": {n: [{"when": when, "outcome": "passed", "duration_s": 0.1}
                              for when in ("setup", "call", "teardown")]
                          for n in NODES if ci.owner(n) == shard}}
            for shard in ci.SHARDS]


def test_complete_disjoint_reports_pass_and_new_paths_have_default_owner():
    assert ci.validate(reports(), IDENTITY, NEEDS) == {"passed": 2}
    assert ci.owner("tests/runtime/nested/test_future.py::test_new[param]") == "runtime"
    assert ci.owner("tests/future/test_new.py::test_new") == "general"


@pytest.mark.parametrize("status", ["failure", "cancelled", "skipped", "", None])
def test_unsuccessful_job_is_rejected_even_with_good_reports(status):
    with pytest.raises(ValueError, match="dependency"):
        ci.validate(reports(), IDENTITY, {"python-tests": {"result": status}})


@pytest.mark.parametrize("needs", [{}, {"python-tests": {}}, {"other": {"result": "success"}}])
def test_missing_job_is_rejected(needs):
    with pytest.raises(ValueError, match="dependency"):
        ci.validate(reports(), IDENTITY, needs)


@pytest.mark.parametrize("field", ["sha", "run", "attempt", "lock"])
def test_stale_report_is_rejected(field):
    values = reports()
    values[1]["identity"][field] = "stale"
    with pytest.raises(ValueError, match="stale"):
        ci.validate(values, IDENTITY, NEEDS)


@pytest.mark.parametrize("mutation", [
    "missing-shard", "duplicate-shard", "missing-node", "duplicate-node", "different-collection",
    "empty-shard", "overlap", "wrong-owner", "missing-execution", "extra-execution",
    "failed-test", "missing-call", "missing-teardown", "duplicate-phase", "failed-exit",
])
def test_report_corruption_cannot_pass(mutation):
    values = reports()
    a, b = values
    phases = a["executed"][NODES[0]]
    if mutation == "missing-shard": values.pop()
    elif mutation == "duplicate-shard": b["shard"] = a["shard"]
    elif mutation == "missing-node":
        for r in values: r["collected"].pop()
    elif mutation == "duplicate-node":
        for r in values: r["collected"].append(NODES[0])
    elif mutation == "different-collection": b["collected"].reverse()
    elif mutation == "empty-shard": b["selected"] = []
    elif mutation == "overlap": b["selected"].append(NODES[0])
    elif mutation == "wrong-owner": a["selected"], b["selected"] = b["selected"], a["selected"]
    elif mutation == "missing-execution": a["executed"].clear()
    elif mutation == "extra-execution": a["executed"][NODES[1]] = copy.deepcopy(phases)
    elif mutation == "failed-test": phases[1]["outcome"] = "failed"
    elif mutation == "missing-call": phases.pop(1)
    elif mutation == "missing-teardown": phases.pop()
    elif mutation == "duplicate-phase": phases.append(copy.deepcopy(phases[-1]))
    elif mutation == "failed-exit": a["exitstatus"] = 1
    with pytest.raises(ValueError):
        ci.validate(values, IDENTITY, NEEDS)


def test_existing_test_skip_is_recorded_but_job_skip_is_not_accepted():
    values = reports()
    phases = values[0]["executed"][NODES[0]]
    phases.pop(1)
    phases[0]["outcome"] = "skipped"
    assert ci.validate(values, IDENTITY, NEEDS) == {"passed": 1, "skipped": 1}


def test_plugin_preserves_file_order_and_writes_phase_manifest(tmp_path, monkeypatch):
    options = {"ci_shard": "runtime", "ci_report": str(tmp_path / "report.json")}
    deselected = []
    config = SimpleNamespace(getoption=options.__getitem__, hook=SimpleNamespace(
        pytest_deselected=lambda items: deselected.extend(items)))
    session = SimpleNamespace(config=config)
    monkeypatch.setattr(ci, "identity", lambda: dict(IDENTITY))
    ci.pytest_configure(config)
    ci.pytest_sessionstart(session)
    items = [SimpleNamespace(nodeid=n) for n in NODES]
    ci.pytest_collection_modifyitems(session, config, items)
    assert [i.nodeid for i in items] == [NODES[0]]
    assert [i.nodeid for i in deselected] == [NODES[1]]
    for when in ("setup", "call", "teardown"):
        ci.pytest_runtest_logreport(SimpleNamespace(nodeid=NODES[0], when=when, outcome="passed", duration=0.1))
    ci.pytest_sessionfinish(session, 0)
    value = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert value["collected"] == NODES and value["selected"] == [NODES[0]]
    assert value["exitstatus"] == 0 and len(value["executed"][NODES[0]]) == 3
    assert ci._session_report is None


def test_workflow_keeps_required_gates_and_all_validation():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    gate = workflow.split("  python-validation:\n", 1)[1].split("  viewer-validation:", 1)[0]
    assert "if: always()" in gate and "needs: [python-tests]" in gate
    assert "CI_NEEDS: ${{ toJSON(needs) }}" in gate
    assert "python .github/python_ci.py --reports" in gate
    assert "fail-fast: false" in workflow and "shard: [runtime, general]" in workflow
    assert "uv run pytest tests -q -p python_ci" in workflow
    for command in ("--durations=30", "--junitxml=", "--strict-map --strict-links",
                    "compileall src tests", "git diff --check", "npm test", "npm run typecheck", "npm run build"):
        assert command in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow
    for forbidden in ("pull_request_target", "continue-on-error", "paths-ignore:", "paths:"):
        assert forbidden not in workflow
