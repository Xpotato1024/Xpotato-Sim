"""同一Device出力を単腕/双腕の速度へ写像し、校正・filterを追加しない。"""
import pytest

from xpotato_sim.plugins.mappings.normalized_endpoint_velocity.implementation import NormalizedEndpointVelocityMapping, normalize_parameters
from xpotato_sim.plugins.input_sources.selfrionette_device.source import DeviceSessionSource
from tests.plugins.input_sources.selfrionette_device.test_source import parameters, wire


def mapping_parameters(roles=("left",)):
    return {"channel_axis_weights": {role: [[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]]+[[0.,0.,0.]]*4 for role in roles},
        "speed_m_s": .02, "max_speed_m_s": .05, "control_frame": "world"}


def test_source_values_become_velocity_with_explicit_weights_only():
    source = DeviceSessionSource(parameters=parameters(("left", "right")), clock=lambda: 1.)
    source.ingest_session_message(source.parse_message(wire(value=.25)))
    source.ingest_session_message(source.parse_message(wire("right", value=-.5)))
    strategy = NormalizedEndpointVelocityMapping()
    value = strategy.map_coordinated_input(source.read_frame(), mapping_parameters(("left", "right")),
        side_to_endpoint={"left": "left_arm", "right": "right_arm"}, received_at_s=source.last_received_at_s)
    assert value.endpoints[0].velocity_m_s == (.005,0.,0.)
    assert value.endpoints[1].velocity_m_s == (-.01,0.,0.)
    assert value.available and not value.neutral
    assert value.received_at_s == .998
    assert strategy.map_coordinated_input(source.read_frame(), mapping_parameters(("left", "right")),
        side_to_endpoint={"left": "left_arm", "right": "right_arm"}, received_at_s=.998) == value


def test_mapping_cannot_infer_channels_or_roles():
    with pytest.raises(ValueError):
        normalize_parameters({})
    bad = mapping_parameters()
    bad["channel_axis_weights"]["left"] = [[0.,0.,0.]]*7
    with pytest.raises(ValueError):
        normalize_parameters(bad)


def test_large_finite_weights_cannot_overflow_to_zero_or_nonfinite_command():
    source = DeviceSessionSource(parameters=parameters(), clock=lambda: 1.)
    source.ingest_session_message(source.parse_message(wire(value=1.)))
    selected = mapping_parameters()
    selected["channel_axis_weights"]["left"][0] = [1e308, 1e308, 1e308]
    selected["speed_m_s"] = 10.
    with pytest.raises(ValueError, match="overflow"):
        NormalizedEndpointVelocityMapping().map_coordinated_input(source.read_frame(), selected,
            side_to_endpoint={"left": "left_arm"}, received_at_s=source.last_received_at_s)
