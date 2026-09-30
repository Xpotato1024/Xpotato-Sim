"""CIのjob状態、集合、鮮度、実行完了を故障注入で検証する。"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("python_ci_under_test", ROOT / ".github/python_ci.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)

NODES = ["tests/runtime/test_future.py::test_new", "tests/new_axis/test_future.py::test_new",
         "tests/new_axis/test_future.py::test_another"]
IDENTITY = {"sha": "revision", "run": "123", "attempt": "2", "lock": "lock"}
NEEDS = {"python-tests": {"result": "success"}}


def reports():
    return [{"shard": shard, "identity": dict(IDENTITY), "exitstatus": 0,
             "collected": list(NODES), "selected": [n for n in NODES if ci.owner(n) == shard],
             "collect_only": False, "executed": {n: [{"when": when, "outcome": "passed", "duration_s": 0.1}
                              for when in ("setup", "call", "teardown")]
                          for n in NODES if ci.owner(n) == shard}}
            for shard in ci.SHARDS]


def reference():
    return {"kind": "reference", "identity": dict(IDENTITY), "exitstatus": 0,
            "collect_only": True, "collected": list(NODES)}


def check(values, expected=IDENTITY, needs=NEEDS, references=None):
    return ci.validate(values, expected, needs, [reference()] if references is None else references)


def test_complete_disjoint_reports_pass_and_new_paths_have_default_owner():
    assert check(reports(), IDENTITY, NEEDS) == {"passed": 3}
    assert ci.owner("tests/runtime/nested/test_future.py::test_new[param]") == "runtime"
    assert ci.owner("tests/future/test_new.py::test_new") == "general"


@pytest.mark.parametrize("status", ["failure", "cancelled", "skipped", "", None])
def test_unsuccessful_job_is_rejected_even_with_good_reports(status):
    with pytest.raises(ValueError, match="dependency"):
        check(reports(), IDENTITY, {"python-tests": {"result": status}})


@pytest.mark.parametrize("needs", [{}, {"python-tests": {}}, {"other": {"result": "success"}}])
def test_missing_job_is_rejected(needs):
    with pytest.raises(ValueError, match="dependency"):
        check(reports(), IDENTITY, needs)


@pytest.mark.parametrize("field", ["sha", "run", "lock"])
def test_stale_report_is_rejected(field):
    values = reports()
    values[1]["identity"][field] = "stale"
    with pytest.raises(ValueError, match="stale"):
        check(values, IDENTITY, NEEDS)


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
        check(values, IDENTITY, NEEDS)


def test_existing_test_skip_is_recorded_but_job_skip_is_not_accepted():
    values = reports()
    phases = values[0]["executed"][NODES[0]]
    phases.pop(1)
    phases[0]["outcome"] = "skipped"
    assert check(values, IDENTITY, NEEDS) == {"passed": 2, "skipped": 1}


def test_partial_rerun_reuses_successful_general_and_latest_runtime():
    old_general, old_runtime = reports()[1], reports()[0]
    old_general["identity"]["attempt"] = old_runtime["identity"]["attempt"] = "1"
    old_runtime["exitstatus"] = 1
    ref = reference()
    ref["identity"]["attempt"] = "1"
    assert check([old_general, old_runtime, reports()[0]], references=[ref]) == {"passed": 3}


def test_gate_only_rerun_can_reuse_both_successful_previous_shards():
    values, ref = reports(), reference()
    for value in [*values, ref]: value["identity"]["attempt"] = "1"
    assert check(values, references=[ref]) == {"passed": 3}


def test_latest_failed_reference_cannot_fall_back_to_older_success():
    old, new = reference(), reference()
    old["identity"]["attempt"] = "1"
    new["exitstatus"] = 1
    with pytest.raises(ValueError, match="unsuccessful"):
        check(reports(), references=[old, new])


@pytest.mark.parametrize("attempt", ["0", "-1", "3", "invalid"])
def test_invalid_or_future_attempt_is_rejected(attempt):
    values = reports()
    values[0]["identity"]["attempt"] = attempt
    with pytest.raises(ValueError):
        check(values)


def test_latest_failed_attempt_cannot_fall_back_to_older_success():
    old = reports()[0]
    old["identity"]["attempt"] = "1"
    values = reports()
    values[0]["exitstatus"] = 1
    with pytest.raises(ValueError, match="unsuccessful"):
        check([old, *values])


def test_skipped_setup_with_call_is_rejected():
    values = reports()
    values[0]["executed"][NODES[0]][0]["outcome"] = "skipped"
    with pytest.raises(ValueError, match="call after"):
        check(values)


def test_common_omission_in_both_reports_is_rejected_by_reference():
    values = reports()
    omitted = NODES[1]
    for r in values:
        r["collected"].remove(omitted)
        if omitted in r["selected"]: r["selected"].remove(omitted)
        r["executed"].pop(omitted, None)
    with pytest.raises(ValueError, match="collection mismatch"):
        check(values)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "future", "failed", "wrong-run", "wrong-sha", "wrong-lock", "wrong-attempt"])
def test_reference_identity_and_latest_success_are_required(mutation):
    refs = [reference()]
    if mutation == "missing": refs = []
    elif mutation == "duplicate": refs.append(reference())
    elif mutation == "failed": refs[0]["exitstatus"] = 1
    elif mutation == "future": refs[0]["identity"]["attempt"] = "3"
    else: refs[0]["identity"][mutation.removeprefix("wrong-")] = "1"
    with pytest.raises(ValueError):
        check(reports(), references=refs)


def test_collect_only_shard_cannot_pass_even_with_complete_phase_fields():
    values = reports()
    values[0]["collect_only"] = True
    with pytest.raises(ValueError, match="collect-only"):
        check(values)


def run_probe(tmp_path, *options):
    env = dict(os.environ, PYTHONPATH=str(ROOT / ".github"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
                             "-p", "python_ci", *options], cwd=tmp_path, env=env,
                            capture_output=True, text=True, encoding="utf-8")
    return result


@pytest.fixture
def probe(tmp_path):
    (tmp_path / "tests/runtime").mkdir(parents=True)
    (tmp_path / "tests/runtime/test_runtime_probe.py").write_text("def test_runtime(): assert True\n", encoding="utf-8")
    (tmp_path / "tests/test_probe.py").write_text("def test_general(): assert True\n", encoding="utf-8")
    return tmp_path


def test_real_pytest_reference_and_two_shards_match(probe):
    ref_path = probe / "reference.json"
    assert run_probe(probe, "--collect-only", "--ci-reference", str(ref_path)).returncode == 0
    values = []
    for shard in ci.SHARDS:
        path = probe / f"{shard}.json"
        result = run_probe(probe, "--ci-shard", shard, "--ci-report", str(path))
        assert result.returncode == 0, result.stderr
        values.append(json.loads(path.read_text(encoding="utf-8")))
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    assert check(values, ref["identity"], references=[ref]) == {"passed": 2}


@pytest.mark.parametrize("narrow", [["-k", "runtime"], ["--ignore=tests/test_probe.py"]])
def test_narrowed_reference_is_rejected(probe, narrow):
    result = run_probe(probe, "--collect-only", "--ci-reference", str(probe / "reference.json"), *narrow)
    assert result.returncode != 0
    assert "unfiltered" in result.stderr


@pytest.mark.parametrize("narrow", [["-k", "runtime"], ["--ignore=tests/test_probe.py"], ["--collect-only"]])
def test_narrowed_or_collect_only_execution_cannot_pass(probe, narrow):
    ref_path = probe / "reference.json"
    assert run_probe(probe, "--collect-only", "--ci-reference", str(ref_path)).returncode == 0
    values = []
    for shard in ci.SHARDS:
        path = probe / f"{shard}.json"
        run_probe(probe, "--ci-shard", shard, "--ci-report", str(path), *narrow)
        values.append(json.loads(path.read_text(encoding="utf-8")))
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    with pytest.raises(ValueError):
        check(values, ref["identity"], references=[ref])


def test_explicit_bash_preserves_failing_pytest_pipeline(probe):
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    step = workflow.split("      - name: Run tests\n", 1)[1].split("      - name:", 1)[0]
    assert "shell: bash" in step
    bash = (str(Path(shutil.which("git")).parents[1] / "bin/bash.exe")
            if os.name == "nt" else shutil.which("bash"))
    assert bash and Path(bash).is_file(), "bash is required for pipeline regression"
    (probe / "tests/test_probe.py").write_text("def test_failure(): assert False\n", encoding="utf-8")
    command = (f"{shlex.quote(sys.executable.replace(chr(92), '/'))} -m pytest tests -q -p no:cacheprovider "
               f"2>&1 | tee {shlex.quote((probe / 'pipeline.log').as_posix())}")
    result = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", command],
                            cwd=probe, capture_output=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed" in (probe / "pipeline.log").read_text(encoding="utf-8")


def test_plugin_preserves_file_order_and_writes_phase_manifest(tmp_path, monkeypatch):
    options = {"ci_shard": "runtime", "ci_report": str(tmp_path / "report.json"), "ci_reference": None, "collectonly": False}
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
    assert [i.nodeid for i in deselected] == NODES[1:]
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
    assert "uv run pytest tests --collect-only -q -p python_ci" in workflow
    assert "--ci-reference" in workflow and "pattern: python-*\n" in gate
    assert "pattern: python-*-${{ github.run_attempt }}" not in gate
    for command in ("--durations=30", "--junitxml=", "--strict-map --strict-links",
                    "compileall src tests", "git diff --check", "npm test", "npm run typecheck", "npm run build"):
        assert command in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow
    for forbidden in ("pull_request_target", "continue-on-error", "paths-ignore:", "paths:"):
        assert forbidden not in workflow
