"""測定scriptが既存ChromiumのCDPへ接続しないことを、実関数で検証する。"""
import ast
import io
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/viewer/measure_workbench_latency.py"


@pytest.fixture
def discovery(tmp_path):
    # CLI本体は起動せず、script中の実関数だけをcompileする。
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_owned_debugger_tab")
    clock = itertools.count(step=.05)
    calls = []
    values = {"/json/version": {"webSocketDebuggerUrl": "ws://127.0.0.1:47123/devtools/browser/owned"},
              "/json/list": [{"type": "page", "webSocketDebuggerUrl": "ws://127.0.0.1:47123/devtools/page/owned-tab"}]}
    def get(url, timeout):
        calls.append(url)
        assert url.startswith("http://127.0.0.1:47123/")
        return io.StringIO(json.dumps(values[urlsplit(url).path]))
    namespace = {"Path": Path, "json": json, "urlsplit": urlsplit,
                 "time": SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda n: None),
                 "urllib": SimpleNamespace(request=SimpleNamespace(urlopen=get))}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SCRIPT), "exec"), namespace)
    (tmp_path / "DevToolsActivePort").write_text("47123\n/devtools/browser/owned\n", encoding="utf-8")
    return namespace["_owned_debugger_tab"], tmp_path, values, calls


def test_discovers_only_the_new_browser_endpoint(discovery):
    discover, profile, values, calls = discovery
    result = discover(SimpleNamespace(poll=lambda: None), profile)
    assert result == values["/json/list"][0]
    assert calls == ["http://127.0.0.1:47123/json/version", "http://127.0.0.1:47123/json/list"]


def test_exited_child_never_connects_to_any_cdp(discovery):
    discover, profile, values, calls = discovery
    with pytest.raises(RuntimeError, match="exited before"):
        discover(SimpleNamespace(poll=lambda: 1), profile)
    assert not calls


def test_missing_owned_endpoint_does_not_fallback_to_fixed_port(discovery):
    discover, profile, values, calls = discovery
    (profile / "DevToolsActivePort").unlink()
    with pytest.raises(RuntimeError, match="timeout"):
        discover(SimpleNamespace(poll=lambda: None), profile, timeout_s=.2)
    assert not calls


def test_browser_identity_mismatch_fails_before_page_discovery(discovery):
    discover, profile, values, calls = discovery
    values["/json/version"]["webSocketDebuggerUrl"] = "ws://127.0.0.1:47123/devtools/browser/foreign"
    with pytest.raises(RuntimeError, match="identity mismatch"):
        discover(SimpleNamespace(poll=lambda: None), profile)
    assert calls == ["http://127.0.0.1:47123/json/version"]


@pytest.mark.parametrize("endpoint", [
    "ws://127.0.0.1:9386/devtools/page/foreign",
    "ws://example.invalid:47123/devtools/page/foreign",
    "ws://127.0.0.1:47123/devtools/browser/owned",
])
def test_foreign_or_non_page_endpoint_is_rejected(discovery, endpoint):
    discover, profile, values, calls = discovery
    values["/json/list"][0]["webSocketDebuggerUrl"] = endpoint
    with pytest.raises(RuntimeError, match="page endpoint mismatch"):
        discover(SimpleNamespace(poll=lambda: None), profile)


def test_launcher_uses_ephemeral_port_and_new_profile():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "'--remote-debugging-port=0'" in source
    assert "tempfile.mkdtemp(prefix='owned-chromium-',dir=TEMP)" in source
    assert "tab=_owned_debugger_tab(browser,browser_profile)" in source
    assert "9386" not in source
