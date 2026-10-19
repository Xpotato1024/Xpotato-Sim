"""正規化入力の7×3写像。個体校正やfilterを持たず、積分は共通runtimeへ委譲する。"""
from __future__ import annotations
from collections.abc import Mapping
from hashlib import sha256
import json
from math import hypot, isfinite

from xpotato_sim.plugins.mappings._command_routes import local_endpoint_velocity_command_route
from xpotato_sim.runtime.experiment.contracts import (ControlMappingPlugin, ParameterContract,
    ParameterField, VersionedIdentity, LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1)
from xpotato_sim.schemas.coordinated import CoordinatedInput, EndpointVelocity, number, vector

SEMANTICS = VersionedIdentity("normalized_endpoint_velocity", 1)


def normalize_parameters(parameters):
    if not isinstance(parameters, Mapping) or set(parameters) != {"channel_axis_weights", "speed_m_s", "max_speed_m_s", "control_frame"}:
        raise ValueError("explicit normalized velocity Mapping parameters required")
    weights = parameters["channel_axis_weights"]
    if not isinstance(weights, Mapping) or not weights or not set(weights) <= {"left", "right"}:
        raise ValueError("explicit channel-axis weights for each role required")
    validated = {}
    for side, rows in weights.items():
        if not isinstance(rows, (list, tuple)) or len(rows) != 7:
            raise ValueError("seven logical channels require explicit 3D weights")
        validated[side] = tuple(vector(row, 3, "channel axis weights") for row in rows)
        if not any(any(v != 0 for v in row) for row in validated[side]):
            raise ValueError("at least one channel-axis weight must be nonzero")
    speed = number(parameters["speed_m_s"], "speed_m_s", positive=True)
    maximum = number(parameters["max_speed_m_s"], "max_speed_m_s", positive=True)
    if parameters["control_frame"] not in {"world", "tool"}:
        raise ValueError("explicit world/tool frame required")
    return {"channel_axis_weights": validated, "speed_m_s": speed,
        "max_speed_m_s": maximum, "control_frame": parameters["control_frame"]}


class NormalizedEndpointVelocityMapping:
    mapping_semantics_identity = SEMANTICS
    latest_trigger_presentation = None

    def map_input(self, input_intent, parameters):
        raise ValueError("normalized Device input requires the coordinated named-endpoint runtime")

    def reset_coordinated_presentation(self, parameters, *, reason):
        normalize_parameters(parameters)

    def map_coordinated_input(self, frame, parameters, *, side_to_endpoint, received_at_s):
        parameters = normalize_parameters(parameters)
        sample = frame.metadata.get("selfrionette_device_input_v1")
        if (frame.source != "selfrionette_device" or not isinstance(sample, Mapping)
                or sample.get("schema_version") != "selfrionette-device-snapshot/v1"
                or sample.get("unit") != "1" or sample.get("source_active") is not True):
            raise ValueError("quality-validated dimensionless Device sample required")
        devices = sample["devices"]
        if set(devices) != set(side_to_endpoint) or set(devices) != set(parameters["channel_axis_weights"]):
            raise ValueError("Device, Mapping and endpoint role sets must match exactly")
        commands = []
        neutral = True
        for side, endpoint in side_to_endpoint.items():
            values = vector(devices[side]["command"], 7, "Device command")
            neutral = neutral and all(v == 0 for v in values)
            rows = parameters["channel_axis_weights"][side]
            velocity = tuple(sum(values[i]*rows[i][axis] for i in range(7))*parameters["speed_m_s"] for axis in range(3))
            norm = hypot(*velocity)
            if not isfinite(norm):
                raise ValueError("normalized velocity Mapping overflow")
            if norm > parameters["max_speed_m_s"]:
                velocity = tuple(v*parameters["max_speed_m_s"]/norm for v in velocity)
            commands.append(EndpointVelocity(endpoint, velocity, parameters["control_frame"]))
        encoded = json.dumps(sample, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return CoordinatedInput(tuple(commands), sample["source_epoch"], sample["update_sequence"],
            frame.timestamp_s, received_at_s, True, neutral, sha256(encoded).hexdigest(),
            ordering_receipt_s=sample["ordering_receipt_s"])


CONTROL_MAPPING_PLUGIN = ControlMappingPlugin(
    identity=SEMANTICS, strategy=NormalizedEndpointVelocityMapping(),
    session_strategy_factory=NormalizedEndpointVelocityMapping,
    accepted_input_sample_schemas=frozenset({VersionedIdentity("selfrionette_device_normalized_sample", 1)}),
    parameter_contract=ParameterContract((ParameterField("channel_axis_weights", object),
        ParameterField("speed_m_s", float), ParameterField("max_speed_m_s", float), ParameterField("control_frame", str))),
    comparison_family_identity=VersionedIdentity("normalized_velocity_comparison", 1),
    mapping_semantics_identity=SEMANTICS,
    command_semantics_routes=frozenset({local_endpoint_velocity_command_route(
        route_identity=LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1, control_semantics_identity=SEMANTICS)}),
    parameter_normalizer=normalize_parameters)
