"""Canonical runtime convenience policy for independently selected axes."""

from __future__ import annotations

from types import MappingProxyType

from xpotato_sim.runtime.experiment.contracts import PluginSelection


_DEFAULT_MAPPING_BY_INPUT_SOURCE = MappingProxyType(
    {
        "analog_fixture": PluginSelection("analog_fixture_mapping", 1),
        "noop": PluginSelection("replay_mapping", 1),
        "programmed_target": PluginSelection("replay_mapping", 1),
        "replay": PluginSelection("replay_mapping", 1),
        "selfrionette": PluginSelection("loadcell_endpoint_mapping", 1),
        "viewer": PluginSelection("viewer_keyboard_gamepad_mapping", 1),
    }
)


def default_control_mapping_selection(
    input_source_id: str,
) -> PluginSelection | None:
    """Return an operator convenience pairing without coupling plugin packages."""

    return _DEFAULT_MAPPING_BY_INPUT_SOURCE.get(input_source_id)


__all__ = ["default_control_mapping_selection"]
