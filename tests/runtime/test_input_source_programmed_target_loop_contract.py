from __future__ import annotations

import pytest

from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source


def _programmed_target_parameters(**overrides: object) -> dict[str, object]:
    parameters: dict[str, object] = {
        "steps": 1,
        "initial_position_m": (0.0, 0.0, 0.0),
        "preset": "sweep_x",
        "loop": False,
    }
    parameters.update(overrides)
    return parameters


def test_programmed_target_plugin_factory_honors_loop_parameter() -> None:
    plugin = INPUT_SOURCE_CATALOG.resolve("programmed_target").plugin
    reader = plugin.create_runtime_reader(_programmed_target_parameters(loop=True))

    frames = [reader.read_frame() for _ in range(22)]

    assert frames[0].metadata["frame_index"] == 0
    assert frames[20].metadata["frame_index"] == 20
    assert frames[21] == frames[0]


@pytest.mark.parametrize("steps", [0, -1])
def test_programmed_target_plugin_factory_rejects_non_positive_steps(steps: int) -> None:
    plugin = INPUT_SOURCE_CATALOG.resolve("programmed_target").plugin

    with pytest.raises(ValueError, match="steps must be a positive integer"):
        plugin.create_runtime_reader(_programmed_target_parameters(steps=steps))


def test_programmed_target_plugin_factory_rejects_unsupported_preset() -> None:
    plugin = INPUT_SOURCE_CATALOG.resolve("programmed_target").plugin

    with pytest.raises(ValueError, match="unsupported programmed_target preset"):
        plugin.create_runtime_reader(_programmed_target_parameters(preset="unsupported"))


def test_programmed_target_cli_selection_keeps_loop_disabled() -> None:
    selection = select_runtime_input_source("programmed_target", steps=1)

    assert selection.loop is False
    assert selection.validated_parameters is not None
    assert selection.validated_parameters["loop"] is False
