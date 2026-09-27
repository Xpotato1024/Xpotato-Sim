from __future__ import annotations

import builtins
from dataclasses import replace
from types import SimpleNamespace

import pytest

import xpotato_sim.runtime.runners.live_selfrionette as live_selfrionette
from xpotato_sim.runtime.runners.live_selfrionette import (
    LiveSelfrionetteRuntimeRunnerConfig,
    run_live_selfrionette_runtime_runner,
)
from xpotato_sim.schemas import RawInputFrame
from xpotato_sim.plugins.input_sources.selfrionette import (
    NormalizedLoadcellInputIntent,
    normalize_loadcell_frame_for_mapping,
)
from xpotato_sim.runtime.experiment.contracts import PluginSelection, VersionedIdentity
from xpotato_sim.runtime.experiment.input_source import InputSourceMappingAdapterContract


_TEST_MAPPING_ADAPTER = InputSourceMappingAdapterContract(
    input_schema=VersionedIdentity("loadcell_vector_sample", 1),
    output_schema=VersionedIdentity("loadcell_normalized_input_intent", 1),
    adapt=normalize_loadcell_frame_for_mapping,
)


def _guard_serial_import(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args, **kwargs):
        if name == "serial" or name.startswith("serial."):
            raise AssertionError("serial import should not happen for injected line source mode")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)


def test_live_selfrionette_runtime_runner_processes_injected_lines_without_opening_serial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _guard_serial_import(monkeypatch)

    payloads = run_live_selfrionette_runtime_runner(
        LiveSelfrionetteRuntimeRunnerConfig(
            port=None,
            max_frames=1,
            current_tip_position_m=(0.1, 0.0, 0.3),
        ),
        line_source=[
            "status,setup_start",
            "vector,1000,40000,0,0,0,0,0,0",
        ],
    )

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["version"] == 0
    assert payload["target_position_m"] is None
    assert payload["metadata"]["source_kind"] == "selfrionette"
    assert payload["metadata"]["frame_index"] == 1
    assert payload["metadata"]["serial_timestamp_s"] == 1.0
    assert payload["metadata"]["current_tip_position_m"] == (0.1, 0.0, 0.3)
    assert len(payload["metadata"]["desired_endpoint_m"]) == 3
    assert "serial_port" not in payload["metadata"]
    assert "baud_rate" not in payload["metadata"]
    assert "target_position_m" not in payload["metadata"]


def test_live_selfrionette_runtime_runner_resolves_mapping_plugin_at_normalized_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _guard_serial_import(monkeypatch)
    production_mapping = live_selfrionette.resolve_control_mapping_plugin(
        PluginSelection("loadcell_endpoint_mapping", 1)
    )
    seen_inputs: list[object] = []

    class SpyStrategy:
        mapping_semantics_identity = production_mapping.strategy.mapping_semantics_identity

        def map_input(self, input_intent: object, parameters):
            seen_inputs.append(input_intent)
            return production_mapping.strategy.map_input(input_intent, parameters)

    spy_mapping = replace(production_mapping, strategy=SpyStrategy())
    resolved_selections: list[PluginSelection] = []

    def resolve(selection: PluginSelection):
        resolved_selections.append(selection)
        return spy_mapping

    monkeypatch.setattr(live_selfrionette, "resolve_control_mapping_plugin", resolve)
    payloads = run_live_selfrionette_runtime_runner(
        LiveSelfrionetteRuntimeRunnerConfig(port=None, max_frames=1),
        line_source=(
            "status,setup_start",
            "vector,1000,1,0,0,0,0,0,0",
        ),
    )

    assert len(payloads) == 1
    assert resolved_selections == [PluginSelection("loadcell_endpoint_mapping", 1)]
    assert len(seen_inputs) == 1
    assert isinstance(seen_inputs[0], NormalizedLoadcellInputIntent)


def test_live_selfrionette_runtime_runner_consumes_one_shot_generator_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _guard_serial_import(monkeypatch)
    lines = (
        line
        for line in (
            "status,setup_start",
            "vector,1000,40000,0,0,0,0,0,0",
        )
    )

    payloads = run_live_selfrionette_runtime_runner(
        LiveSelfrionetteRuntimeRunnerConfig(port=None, max_frames=1),
        line_source=lines,
    )

    assert len(payloads) == 1
    assert payloads[0]["metadata"]["serial_timestamp_s"] == 1.0


def test_live_selfrionette_runtime_runner_preserves_primary_failure_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Reader:
        def start(self) -> None:
            pass

        def read_frame(self) -> RawInputFrame:
            return RawInputFrame(
                source="selfrionette",
                timestamp_s=1.0,
                values=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            )

        def close(self) -> None:
            raise RuntimeError("cleanup failure")

    reader = Reader()
    registration = SimpleNamespace(
        plugin=SimpleNamespace(
            create_runtime_reader=lambda *args, **kwargs: reader,
            effective_mapping_input_sample_schema=VersionedIdentity(
                "loadcell_normalized_input_intent", 1
            ),
            mapping_input_adapter=_TEST_MAPPING_ADAPTER,
        )
    )
    monkeypatch.setattr(
        live_selfrionette,
        "INPUT_SOURCE_CATALOG",
        SimpleNamespace(resolve=lambda alias: registration),
    )

    def fail_runtime(*args, **kwargs):
        raise RuntimeError("runtime failure")

    monkeypatch.setattr(
        live_selfrionette,
        "run_offline_input_runtime_stepping_smoke",
        fail_runtime,
    )

    with pytest.raises(RuntimeError, match="runtime failure") as error:
        run_live_selfrionette_runtime_runner(
            LiveSelfrionetteRuntimeRunnerConfig(port=None, max_frames=1),
            line_source=("vector,1000,0,0,0,0,0,0,0",),
        )

    assert any("cleanup failed" in note for note in error.value.__notes__)


def test_live_selfrionette_runtime_runner_surfaces_close_failure_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Reader:
        def start(self) -> None:
            pass

        def read_frame(self) -> RawInputFrame:
            return RawInputFrame(
                source="selfrionette",
                timestamp_s=1.0,
                values=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            )

        def close(self) -> None:
            raise RuntimeError("cleanup failure")

    reader = Reader()
    registration = SimpleNamespace(
        plugin=SimpleNamespace(
            create_runtime_reader=lambda *args, **kwargs: reader,
            effective_mapping_input_sample_schema=VersionedIdentity(
                "loadcell_normalized_input_intent", 1
            ),
            mapping_input_adapter=_TEST_MAPPING_ADAPTER,
        )
    )
    monkeypatch.setattr(
        live_selfrionette,
        "INPUT_SOURCE_CATALOG",
        SimpleNamespace(resolve=lambda alias: registration),
    )
    monkeypatch.setattr(
        live_selfrionette,
        "run_offline_input_runtime_stepping_smoke",
        lambda *args, **kwargs: SimpleNamespace(payload={"version": 0}),
    )

    with pytest.raises(RuntimeError, match="cleanup failure"):
        run_live_selfrionette_runtime_runner(
            LiveSelfrionetteRuntimeRunnerConfig(port=None, max_frames=1),
            line_source=("vector,1000,0,0,0,0,0,0,0",),
        )


def test_live_selfrionette_runtime_runner_rejects_live_mode_without_pyserial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args, **kwargs):
        if name == "serial" or name.startswith("serial."):
            raise ModuleNotFoundError("No module named 'serial'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    with pytest.raises(RuntimeError, match="serial module is required for live Selfrionette mode"):
        run_live_selfrionette_runtime_runner(
            LiveSelfrionetteRuntimeRunnerConfig(
                port="COM5",
                max_frames=1,
                current_tip_position_m=(0.1, 0.0, 0.3),
            ),
        )


def test_live_selfrionette_runtime_runner_rejects_non_finite_defaults() -> None:
    with pytest.raises(ValueError, match="max_frames must be a positive integer"):
        LiveSelfrionetteRuntimeRunnerConfig(
            port=None,
            max_frames=0,
        )
