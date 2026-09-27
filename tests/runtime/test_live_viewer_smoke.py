from __future__ import annotations

from xpotato_sim.runtime.runners.live_viewer_smoke import (
    build_live_viewer_smoke_parser,
    build_live_viewer_smoke_endpoint,
    build_live_viewer_smoke_report_lines,
    build_live_viewer_smoke_viewer_url,
)


def test_live_viewer_smoke_parser_defaults_match_manual_smoke_docs() -> None:
    args = build_live_viewer_smoke_parser().parse_args([])

    assert args.host == "127.0.0.1"
    assert args.port == 8766
    assert args.steps == 3
    assert args.grace_period_s == 5.0


def test_live_viewer_smoke_endpoint_helper_uses_loopback_endpoint() -> None:
    assert build_live_viewer_smoke_endpoint("127.0.0.1", 8766) == "ws://127.0.0.1:8766"


def test_live_viewer_smoke_viewer_url_helper_uses_browser_viewer_url() -> None:
    assert (
        build_live_viewer_smoke_viewer_url("127.0.0.1", 8766)
        == "apps/mujoco-viewer/?websocketUrl=ws://127.0.0.1:8766"
    )


def test_live_viewer_smoke_report_lines_include_endpoint_and_viewer_url() -> None:
    lines = build_live_viewer_smoke_report_lines("127.0.0.1", 8766)

    assert lines[0] == "WebSocket endpoint: ws://127.0.0.1:8766"
    assert lines[1] == "Viewer URL: apps/mujoco-viewer/?websocketUrl=ws://127.0.0.1:8766"


def test_live_viewer_smoke_report_lines_keep_endpoint_and_viewer_url_separate() -> None:
    lines = build_live_viewer_smoke_report_lines("127.0.0.1", 8766)

    assert lines[0] != lines[1]
    assert lines[0].startswith("WebSocket endpoint:")
    assert lines[1].startswith("Viewer URL:")
