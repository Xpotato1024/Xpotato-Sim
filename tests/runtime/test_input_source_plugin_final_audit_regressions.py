from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.runtime.control.input_source_selection import (
    select_runtime_input_source,
)
from xpotato_sim.runtime.control.viewer_control_ingress import (
    ingest_viewer_control_message,
)
from xpotato_sim.runtime.execution.input_step_loop import (
    build_runtime_input_source_step_loop_plan,
    run_runtime_input_source_step_loop,
)
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealthStatus,
)
from xpotato_sim.schemas import (
    ViewerControlGamepadButtonMessage,
    ViewerControlGamepadMessage,
    ViewerControlKeyboardMessage,
    ViewerControlMessage,
)


class _MutableClock:
    def __init__(self, now_s: float) -> None:
        self.now_s = now_s

    def __call__(self) -> float:
        return self.now_s


def _viewer_message(source_kind: str, *, timestamp_s: float) -> ViewerControlMessage:
    if source_kind == "keyboard":
        return ViewerControlMessage(
            type="viewer_control_message",
            timestamp_s=timestamp_s,
            source_kind="keyboard",
            keyboard=ViewerControlKeyboardMessage(
                active_key_codes=("KeyD",),
                key_state={"KeyD": True},
                focus_state="focused",
                zero_state=False,
            ),
        )
    if source_kind == "gamepad":
        return ViewerControlMessage(
            type="viewer_control_message",
            timestamp_s=timestamp_s,
            source_kind="gamepad",
            gamepad=ViewerControlGamepadMessage(
                connected=True,
                index=0,
                id="audit-pad",
                axes=(0.25, -0.5, 0.0),
                buttons=(
                    ViewerControlGamepadButtonMessage(pressed=True, value=1.0),
                ),
            ),
        )
    raise AssertionError(f"unsupported test viewer source kind: {source_kind}")


@pytest.mark.parametrize("viewer_source_kind", ["keyboard", "gamepad"])
@pytest.mark.parametrize("stale", [False, True])
def test_viewer_source_subtype_survives_frame_command_and_state_projection(
    viewer_source_kind: str,
    stale: bool,
) -> None:
    clock = _MutableClock(10.0)
    selection = select_runtime_input_source("viewer", steps=1)
    plan = build_runtime_input_source_step_loop_plan(selection, viewer_clock=clock)
    capability = plan.viewer_bridge_capability
    assert capability is not None

    ingest_viewer_control_message(
        capability,
        _viewer_message(viewer_source_kind, timestamp_s=1.0),
    )
    if stale:
        clock.now_s = 10.251

    record = asyncio.run(run_runtime_input_source_step_loop(plan, steps=1))[0]
    expected_source_kind = f"viewer_{viewer_source_kind}"

    assert record.frame.metadata["source_kind"] == expected_source_kind
    assert record.motion_command.metadata["source_kind"] == expected_source_kind
    assert record.state.metadata["source_kind"] == expected_source_kind
    assert bool(record.motion_command.metadata.get("runtime_input_safety_applied")) is stale


def test_loadcell_health_tracks_start_read_close_and_restart() -> None:
    plugin = INPUT_SOURCE_CATALOG.resolve("selfrionette").plugin
    reader = plugin.create_runtime_reader(
        {"lines": ("vector,1000,1,2,3,4,5,6,7",)}
    )

    initial = reader.current_health()
    assert initial.status is InputSourceHealthStatus.DISCONNECTED
    assert initial.reason == "not_started"

    reader.start()
    # startは受信の証拠ではない。未受信を明示し、次のread成功後だけactiveとなる。
    assert reader.current_health().status is InputSourceHealthStatus.INACTIVE
    assert reader.current_health().age_ms is None
    reader.read_frame()
    assert reader.current_health().status is InputSourceHealthStatus.ACTIVE

    reader.close()
    closed = reader.current_health()
    assert closed.status is InputSourceHealthStatus.DISCONNECTED
    assert closed.reason == "not_started"

    reader.start()
    # startは受信の証拠ではない。未受信を明示し、次のread成功後だけactiveとなる。
    assert reader.current_health().status is InputSourceHealthStatus.INACTIVE
    assert reader.current_health().age_ms is None
    reader.close()


def test_loadcell_start_failure_updates_health_and_cleanup_restores_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingSerial:
        def __init__(self, **_: object) -> None:
            raise RuntimeError("serial open failure")

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=FailingSerial))
    plugin = INPUT_SOURCE_CATALOG.resolve("selfrionette").plugin
    reader = plugin.create_runtime_reader(
        {"port": "COM-audit", "baud_rate": 115200}
    )

    with pytest.raises(RuntimeError, match="serial open failure"):
        reader.start()

    failed = reader.current_health()
    assert failed.status is InputSourceHealthStatus.DISCONNECTED
    assert failed.reason == "start_failed"

    reader.close()
    cleaned = reader.current_health()
    assert cleaned.status is InputSourceHealthStatus.DISCONNECTED
    assert cleaned.reason == "not_started"


def test_catalog_exposes_its_canonical_registry() -> None:
    assert INPUT_SOURCE_CATALOG.registry.ids == INPUT_SOURCE_CATALOG.ids
