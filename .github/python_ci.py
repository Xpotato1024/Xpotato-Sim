"""Python CIのfile単位分割と、実行証拠の完全性を検証する。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

SHARDS = ("runtime", "general")
ROOT = Path(__file__).resolve().parents[1]


def owner(node: str) -> str:
    return "runtime" if node.split("::", 1)[0].startswith("tests/runtime/") else "general"


def identity() -> dict:
    return {"sha": os.environ.get("GITHUB_SHA", "local"),
            "run": os.environ.get("GITHUB_RUN_ID", "local"),
            "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
            "lock": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()}


def unique(nodes: list[str]) -> set[str]:
    if not nodes or any(not isinstance(n, str) or not n.startswith("tests/") or "::" not in n for n in nodes):
        raise ValueError("empty or invalid collection")
    if len(nodes) != len(set(nodes)):
        raise ValueError("duplicate node")
    return set(nodes)


def latest(values: list[dict], expected: dict, key: str) -> dict:
    current = int(expected["attempt"])
    found = {}
    for value in values:
        stamp = value["identity"]
        if any(stamp[k] != expected[k] for k in ("sha", "run", "lock")):
            raise ValueError("stale report identity")
        attempt = int(stamp["attempt"])
        if not 1 <= attempt <= current:
            raise ValueError("invalid or future attempt")
        pair = (value[key], attempt)
        if pair in found:
            raise ValueError("duplicate shard/attempt")
        found[pair] = value
    selected = {}
    for (name, attempt), value in sorted(found.items(), key=lambda item: item[0][1]):
        selected[name] = value
    return selected


def validate(reports: list[dict], expected: dict, needs: dict, references: list[dict]) -> dict:
    """job状態とcollection/実行を独立に照合し、不完全な証拠を拒否する。"""
    if set(needs) != {"python-tests"} or needs["python-tests"].get("result") != "success":
        raise ValueError("dependency job did not succeed")
    chosen = latest(reports, expected, "shard")
    if set(chosen) != set(SHARDS):
        raise ValueError("missing or duplicate shard")
    refs = latest(references, expected, "kind")
    if set(refs) != {"reference"}:
        raise ValueError("missing independent collection")
    reference = refs["reference"]
    if (reference["exitstatus"] != 0 or not reference["collect_only"]
            or reference["identity"]["attempt"] != chosen["general"]["identity"]["attempt"]):
        raise ValueError("unsuccessful or mismatched independent collection")
    full = unique(reference["collected"])
    covered: set[str] = set()
    outcomes = Counter()
    for report in chosen.values():
        if report["exitstatus"] != 0 or report["collect_only"]:
            raise ValueError("unsuccessful or collect-only report")
        if report["collected"] != reference["collected"] or unique(report["collected"]) != full:
            raise ValueError("collection mismatch")
        selected = unique(report["selected"])
        if selected != {n for n in full if owner(n) == report["shard"]} or covered & selected:
            raise ValueError("partition mismatch or overlap")
        covered |= selected
        if set(report["executed"]) != selected:
            raise ValueError("missing or extra execution")
        for node, phases in report["executed"].items():
            names = [p["when"] for p in phases]
            if names not in (["setup", "call", "teardown"], ["setup", "teardown"]):
                raise ValueError("incomplete or duplicate phases")
            if any(p["outcome"] not in ("passed", "skipped") for p in phases):
                raise ValueError("failed phase")
            if phases[-1]["outcome"] != "passed" or phases[0]["outcome"] not in ("passed", "skipped"):
                raise ValueError("incomplete teardown")
            if len(phases) == 2 and phases[0]["outcome"] != "skipped":
                raise ValueError("missing call")
            if len(phases) == 3 and phases[0]["outcome"] != "passed":
                raise ValueError("call after skipped setup")
            outcomes["skipped" if any(p["outcome"] == "skipped" for p in phases) else "passed"] += 1
    if covered != full:
        raise ValueError("coverage mismatch")
    return dict(outcomes)


def peak_rss_bytes() -> int:
    if os.name != "nt":
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (n, ctypes.c_size_t) for n in ("peak", "rss", "qpp", "qp", "qnp", "qn", "page", "peakpage")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.peak


def pytest_addoption(parser):
    parser.addoption("--ci-shard", choices=SHARDS)
    parser.addoption("--ci-report")
    parser.addoption("--ci-reference")


def pytest_configure(config):
    reference = config.getoption("ci_reference")
    if reference:
        # 参照側はpytestが現在収集する全集合。実行側の選択optionを持ち込まない。
        narrowed = any(config.getoption(k, default=None) for k in ("keyword", "markexpr", "ignore", "ignore_glob", "deselect", "lf"))
        if config.args != ["tests"] or narrowed or not config.getoption("collectonly") or config.getoption("ci_shard"):
            raise ValueError("reference requires unfiltered pytest tests --collect-only")
        config._python_ci_reference = {"kind": "reference", "identity": identity(), "collect_only": True}
    if config.getoption("ci_shard"):
        if not config.getoption("ci_report"):
            raise ValueError("--ci-report is required")
        config._python_ci = {"shard": config.getoption("ci_shard"), "identity": identity(),
                             "python": sys.version, "os": platform.platform(),
                             "executed": {}, "collect_only": bool(config.getoption("collectonly")),
                             "started": time.perf_counter()}


def pytest_collection_finish(session):
    if hasattr(session.config, "_python_ci_reference"):
        session.config._python_ci_reference["collected"] = [i.nodeid for i in session.items]


def pytest_collection_modifyitems(session, config, items):
    if not hasattr(config, "_python_ci"):
        return
    report = config._python_ci
    report["collected"] = [i.nodeid for i in items]
    unique(report["collected"])
    selected = [i for i in items if owner(i.nodeid) == report["shard"]]
    deselected = [i for i in items if owner(i.nodeid) != report["shard"]]
    report["selected"] = [i.nodeid for i in selected]
    unique(report["selected"])
    report["collection_s"] = time.perf_counter() - report["started"]
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected


def pytest_runtest_logreport(report):
    # pytestはlogreportへconfigを渡さないためsessionstartで当該sessionだけを保持する。
    if _session_report is not None:
        _session_report["executed"].setdefault(report.nodeid, []).append(
            {"when": report.when, "outcome": report.outcome, "duration_s": report.duration})


_session_report = None


def pytest_sessionstart(session):
    global _session_report
    _session_report = getattr(session.config, "_python_ci", None)


def pytest_sessionfinish(session, exitstatus):
    global _session_report
    if hasattr(session.config, "_python_ci_reference"):
        reference = session.config._python_ci_reference
        reference["exitstatus"] = int(exitstatus)
        path = Path(session.config.getoption("ci_reference"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reference, ensure_ascii=False, indent=2), encoding="utf-8")
    report = _session_report
    _session_report = None
    if report is None:
        return
    report["exitstatus"] = int(exitstatus)
    report["elapsed_s"] = time.perf_counter() - report.pop("started")
    report["peak_rss_bytes"] = peak_rss_bytes()
    path = Path(session.config.getoption("ci_report"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = (f"### Python検証: {report['shard']}\n\n"
               f"- 収集: {len(report.get('collected', []))}件; 選択: {len(report.get('selected', []))}件\n"
               f"- 収集: {report.get('collection_s', 0):.2f}秒; pytest: {report['elapsed_s']:.2f}秒\n"
               f"- exit status: {exitstatus}; peak RSS: {report['peak_rss_bytes']} bytes\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
            stream.write(summary)
    print(summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, required=True)
    args = parser.parse_args()
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in args.reports.rglob("report.json")]
    references = [json.loads(p.read_text(encoding="utf-8")) for p in args.reports.rglob("reference.json")]
    try:
        outcomes = validate(reports, identity(), json.loads(os.environ.get("CI_NEEDS", "{}")), references)
    except (ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"Python validation rejected: {exc}") from exc
    print(f"Python validation: {outcomes}; disjoint union and execution complete")


if __name__ == "__main__":
    main()
