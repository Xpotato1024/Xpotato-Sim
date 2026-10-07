"""旧offline plot/browser smokeの入力・出力・app委譲とPS5.1引数境界を拘束する。"""
from __future__ import annotations

import base64
import csv
from datetime import datetime
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


plot = load("offline_loadcell_plot", "scripts/hardware/selfrionette/plot_loadcell_vectors.py")
browser = load("browser_smoke_launcher", "scripts/viewer/run_browser_viewer_smoke.py")
bridge = load("powershell_bridge", "scripts/repository/powershell_launcher.py")


@pytest.mark.parametrize("line", ["status,ok", "Vector,1,1,2,3,4,5,6,7", "vector,1,1,2"])
def test_non_vector_and_short_lines_are_ignored(line):
    assert plot.parse_vector_line(line) is None


@pytest.mark.parametrize("timestamp,expected", [
    ("-1", -1), ("+12", 12), (str(2**63 - 1), 2**63 - 1),
    (str(-(2**63)), -(2**63)), (str(2**63), None), ("bad", None), ("1_0", None),
])
def test_legacy_signed_timestamp_malformed_values_and_extra_columns(timestamp, expected):
    record = plot.parse_vector_line(f" vector,{timestamp},1.5,-2,3e2,bad,NaN,Infinity,1_0,extra ")
    assert record.timestamp_ms == expected
    assert record.values[:3] == (1.5, -2.0, 300.0)
    assert math.isnan(record.values[3]) and math.isnan(record.values[4])
    assert math.isnan(record.values[5]) and math.isnan(record.values[6])


@pytest.mark.parametrize("text,expected", [
    ("∞", math.inf), ("-∞", -math.inf), ("Infinity", math.nan),
    ("1e309", math.nan), ("nan", math.nan), ("NaN", math.nan), ("1e-400", 0.0),
])
def test_legacy_japanese_dotnet_nonfinite_and_overflow(text, expected):
    record = plot.parse_vector_line(f"vector,1,{text},0,0,0,0,0,0")
    actual = record.values[0]
    assert math.isnan(actual) if math.isnan(expected) else actual == expected


def test_default_paths_use_source_or_current_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert plot.default_output_path("relative/log.txt") == Path("relative/log.png")
    assert plot.default_output_path("relative/.hidden") == Path("relative/.png")
    assert plot.default_output_path(None, datetime(2026, 10, 7, 12, 34, 56)) == (
        tmp_path / "loadcell-vectors-20261007-123456.png"
    )


def test_clipboard_file_stdin_precedence_without_reading_real_clipboard(monkeypatch, tmp_path):
    path = tmp_path / "input.txt"
    path.write_text("file", encoding="utf-8")
    monkeypatch.setattr(plot, "read_clipboard", lambda: "clipboard")
    monkeypatch.setattr(plot.sys, "stdin", io.StringIO("stdin"))
    assert plot.read_source(str(path), True) == "clipboard"
    assert plot.read_source(str(path), False) == "file"
    assert plot.read_source(None, False) == "stdin"


def test_plot_exports_all_channels_sample_order_and_1600_900_png(monkeypatch, tmp_path):
    source = tmp_path / "日本語 log.txt"
    source.write_text("status,ok\nvector,100,1,2,3,4,5,6,7\nvector,bad,8,9,10,bad,12,13,14,extra\n",
                      encoding="utf-8")
    assert plot.main(["-InputPath", str(source), "-Channels", "0,2", "-Title", ""]) == 0
    output = source.with_suffix(".png")
    assert Image.open(output).size == (1600, 900)
    csv_path = output.with_suffix(".csv")
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0] == {"sample_index": "0", "timestamp_ms": "100",
                       **{f"ch{ch}": str(ch + 1) for ch in range(7)}}
    assert rows[1]["sample_index"] == "1" and rows[1]["timestamp_ms"] == ""
    assert rows[1]["ch3"] == "NaN" and rows[1]["ch6"] == "14"


def test_chart_keeps_channel_colors_and_sample_index(monkeypatch, tmp_path):
    from matplotlib.figure import Figure
    seen = []
    def capture(figure, *args, **kwargs):
        axis = figure.axes[0]
        seen.append([(line.get_label(), line.get_color(), list(line.get_xdata()), list(line.get_ydata()))
                     for line in axis.lines])
        assert axis.get_xlabel() == "Sample index" and axis.get_ylabel() == "Value"
    monkeypatch.setattr(Figure, "savefig", capture)
    records = [plot.Vector(999, tuple(range(7))), plot.Vector(4, tuple(range(7, 14)))]
    plot.write_chart(records, tmp_path / "plot.png", "test", [2, 0, -1, 7])
    assert seen == [[("ch2", plot.PALETTE[2], [0, 1], [2, 9]),
                     ("ch0", plot.PALETTE[0], [0, 1], [0, 7])]]


def test_empty_vectors_fail_without_output(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(plot.sys, "stdin", io.StringIO("status,ok\n"))
    assert plot.main(["--output-path", str(tmp_path / "empty.png")]) == 1
    assert "No vector lines were found." in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("flags,open_browser,startup", [
    ([], False, False), (["-OpenBrowser"], True, False),
    (["--no-browser"], False, True), (["-OpenBrowser", "-NoBrowser"], False, True),
])
@pytest.mark.parametrize("status", [0, 17, 130])
def test_browser_profile_equivalence_exit_and_cleanup(monkeypatch, flags, open_browser, startup, status):
    paths = []
    def app(arguments):
        assert arguments[:2] == ["app", "--profile"]
        path = Path(arguments[2])
        paths.append(path)
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
        profile = json.loads(path.read_text(encoding="utf-8"))
        assert profile == {
            "schema_version": "xpotato-sim-launch-profile/v1", "name": "browser-smoke",
            "workspace": str(ROOT), "mode": "replay", "robot": {"name": "fast_arm", "version": 1},
            "input": {"plugin": {"name": "programmed_target", "version": 1},
                      "provider": None, "preset": "sweep_x"},
            "mapping": {"plugin": {"name": "replay_mapping", "version": 1}, "parameters": {}},
            "execution": {"steps": 6, "dt_s": 1 / 60, "interval_s": .033, "grace_period_s": 90},
            "web": {"host": "127.0.0.1", "port": 5176, "websocket_port": 8768,
                    "open_browser": open_browser},
        }
        assert ("--startup-check" in arguments) == startup
        return status
    monkeypatch.setattr(browser, "run_app", app)
    assert browser.main(flags) == status
    assert not paths[0].exists() and not paths[0].parent.exists()


@pytest.mark.parametrize("exception", [RuntimeError("startup failed"), KeyboardInterrupt()])
def test_browser_temp_profile_cleaned_on_original_exception(monkeypatch, exception):
    paths = []
    def app(arguments):
        paths.append(Path(arguments[2]))
        raise exception
    monkeypatch.setattr(browser, "run_app", app)
    with pytest.raises(type(exception)) as caught:
        browser.main(["--no-browser"])
    assert caught.value is exception and not paths[0].exists()


def test_browser_custom_legacy_options_keep_replay_contract():
    args = browser.build_parser().parse_args([
        "-HostName", "127.0.0.2", "-PublisherPort", "18001", "-ViewerPort", "18002",
        "-Preset", "hold", "-Steps", "4", "-IntervalS", ".05", "-GracePeriodS", "30",
    ])
    profile = browser.build_profile(args)
    assert profile["web"]["host"] == "127.0.0.2"
    assert profile["web"]["port"] == 18002 and profile["web"]["websocket_port"] == 18001
    assert profile["execution"] == {"steps": 4, "dt_s": 1 / 60, "interval_s": .05, "grace_period_s": 30}
    assert profile["input"]["preset"] == "hold"


@pytest.mark.parametrize("status", [0, 17, 130])
def test_powershell_bridge_preserves_argv_cwd_and_status(monkeypatch, tmp_path, capsys, status):
    target = tmp_path / "spy.py"
    target.write_text(
        "import argparse, json, os\n"
        "def build_parser(): return argparse.ArgumentParser()\n"
        "def main(arguments):\n"
        "    print(json.dumps([arguments, os.getcwd()]))\n"
        f"    return {status}\n", encoding="utf-8",
    )
    arguments = ["日本語 space", 'a"b', "", "a&ver", "%COMSPEC%", "0,2"]
    encoded = base64.b64encode(json.dumps(arguments).encode()).decode()
    monkeypatch.setattr(bridge, "ROOT", tmp_path)
    previous = sys.argv
    assert bridge.main(["spy.py", encoded]) == status
    assert sys.argv is previous
    assert json.loads(capsys.readouterr().out) == [arguments, str(Path.cwd())]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 compatibility boundary")
@pytest.mark.parametrize("relative", [
    "scripts/hardware/selfrionette/plot_loadcell_vectors.ps1",
    "scripts/viewer/run-browser-viewer-smoke.ps1",
])
def test_actual_powershell_wrapper_forwards_unicode_quotes_empty_and_arrays(tmp_path, relative):
    node = shutil.which("node")
    assert node
    wrapper = tmp_path / relative
    wrapper.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / relative, wrapper)
    spy = tmp_path / "spy.js"
    spy.write_text(
        "const args=process.argv.slice(2);"
        "console.log(JSON.stringify({args,argv:JSON.parse(Buffer.from(args.at(-1),'base64').toString('utf8'))}));"
        "process.exit(17);", encoding="ascii",
    )
    (tmp_path / "uv.cmd").write_text(f'@"{node}" "{spy}" %*\n', encoding="utf-8")
    # 実機/clipboard/正式appは起動しない。固定uv transportをspyで置換する。
    invoke = tmp_path / "invoke.ps1"
    invoke.write_text(
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
        f"& '{wrapper}' -Title '日本語 space' '' 'a\"b' 'a&ver' '%COMSPEC%' -Channels @(0,2)\n"
        "exit $LASTEXITCODE\n", encoding="utf-8-sig",
    )
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    result = subprocess.run(["powershell.exe", "-NoProfile", "-File", str(invoke)],
                            cwd=tmp_path, env=env, input="", capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 17, result.stderr
    payload = json.loads(result.stdout)
    assert payload["argv"] == ["-Title", "日本語 space", "", 'a"b', "a&ver",
                               "%COMSPEC%", "-Channels", "0,2"]
    assert payload["args"][:3] == ["run", "--project", str(tmp_path)]


@pytest.mark.parametrize("encoding,marker", [
    ("utf-8", b"\xef\xbb\xbf"), ("utf-16-le", b"\xff\xfe"),
    ("utf-16-be", b"\xfe\xff"), ("utf-32-le", b"\xff\xfe\x00\x00"),
    ("utf-32-be", b"\x00\x00\xfe\xff"),
])
def test_recorded_file_bom_detection_keeps_powershell_out_file_logs(tmp_path, encoding, marker):
    source = tmp_path / "recorded.txt"
    text = "日本語 comment\nvector,100,1,2,3,4,5,6,7\n"
    source.write_bytes(marker + text.encode(encoding))
    assert plot.read_source(str(source), False) == text
    assert plot.main(["--input-path", str(source)]) == 0


@pytest.mark.parametrize("arguments,expected", [
    (["-nobrowser", "-steps", "4"], ["-NoBrowser", "-Steps=4"]),
    (["-NoBrowser:", "False"], []),
    (["-NoBrowser:$false"], []),
    (["-nob:$true"], ["-NoBrowser"]),
    (["-Step:", "4"], ["-Steps=4"]),
    (["--steps", "4"], ["--steps", "4"]),
])
def test_ps_binding_uses_browser_owner_options(arguments, expected):
    assert bridge.normalize_legacy_arguments(browser.build_parser(), arguments) == expected


def test_ps_binding_uses_plot_owner_options():
    arguments = ["-inputpath:", "file.txt", "-channels", "0,2", "-clipboard:", "False"]
    normalized = bridge.normalize_legacy_arguments(plot.build_parser(), arguments)
    assert normalized == ["-InputPath=file.txt", "-Channels", "0,2"]
    parsed = plot.build_parser().parse_args(normalized)
    assert parsed.input_path == "file.txt" and not parsed.clipboard


@pytest.mark.parametrize("arguments", [["-Help:$false"], ["-Help:", "False"]])
def test_false_help_switch_does_not_skip_plot(arguments):
    normalized = bridge.normalize_legacy_arguments(plot.build_parser(), arguments)
    assert normalized == []
    assert plot.build_parser().parse_args(normalized).title == "Loadcell vectors"


def test_ps_quoted_value_remains_data_even_if_it_looks_like_an_option():
    normalized = bridge.normalize_legacy_arguments(plot.build_parser(),
                                                   ["-inputpath", "-recorded.txt", "-title", "-Help"])
    parsed = plot.build_parser().parse_args(normalized)
    assert parsed.input_path == "-recorded.txt" and parsed.title == "-Help"


def test_ps_missing_value_fails_before_falling_back_to_stdin(capsys):
    with pytest.raises(SystemExit) as caught:
        bridge.normalize_legacy_arguments(plot.build_parser(), ["-InputPath"])
    assert caught.value.code == 2
    assert "requires a value" in capsys.readouterr().err
