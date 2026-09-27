from __future__ import annotations

import pytest

from xpotato_sim.plugins.input_sources.selfrionette import NormalizedLoadcellInputIntent
from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.plugins.mappings.catalog import CONTROL_MAPPING_REGISTRY
from xpotato_sim.plugins.mappings.loadcell_endpoint_mapping import (
    LoadcellEndpointMappingConfig,
    LOADCELL_ENDPOINT_MAPPING_PLUGIN,
    LOADCELL_NORMALIZED_SAMPLE_SCHEMA,
    LOADCELL_VECTOR_SAMPLE_SCHEMA,
)
from xpotato_sim.plugins.mappings.replay_mapping import (
    REPLAY_CONTROL_MAPPING_PLUGIN,
)
from xpotato_sim.runtime.experiment.composition import PluginParameters, compose_experiment
from xpotato_sim.runtime.experiment.contracts import (
    ENDPOINT_DELTA_TO_JOINT_POSITION_V1,
    JOINT_POSITION_COMMAND_V1,
    LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
    PluginAxis,
    PluginParameterOwner,
    PluginSelection,
    REPLAY_COMMAND_TO_JOINT_POSITION_V1,
)
from xpotato_sim.schemas import RawInputFrame
from tests.runtime.test_experiment_plugin_composition import (
    build_test_manifest,
    build_test_registries,
)


def test_production_mapping_catalog_is_deterministic_and_source_compatible_where_applicable() -> None:
    assert CONTROL_MAPPING_REGISTRY.ids == (
        "analog_fixture_mapping",
        "loadcell_endpoint_mapping",
        "replay_mapping",
        "viewer_keyboard_gamepad_mapping",
    )


def test_production_mapping_command_semantics_classification_is_explicit() -> None:
    expected_routes = {
        "analog_fixture_mapping": LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
        "loadcell_endpoint_mapping": ENDPOINT_DELTA_TO_JOINT_POSITION_V1,
        "replay_mapping": REPLAY_COMMAND_TO_JOINT_POSITION_V1,
        "viewer_keyboard_gamepad_mapping": (
            LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1
        ),
    }
    for plugin in CONTROL_MAPPING_REGISTRY.entries:
        route = plugin.resolve_command_semantics_route()
        assert route.identity == expected_routes[plugin.identity.name]
        assert route.control_semantics_identity == plugin.mapping_semantics_identity
        assert route.robot_command_semantics_identity == JOINT_POSITION_COMMAND_V1
    analog = CONTROL_MAPPING_REGISTRY.resolve(PluginSelection("analog_fixture_mapping", 1))
    replay = CONTROL_MAPPING_REGISTRY.resolve(PluginSelection("replay_mapping", 1))
    assert INPUT_SOURCE_CATALOG.resolve("analog_fixture").plugin.produced_sample_schema in analog.accepted_input_sample_schemas
    assert INPUT_SOURCE_CATALOG.resolve("replay").plugin.produced_sample_schema in replay.accepted_input_sample_schemas
    assert LOADCELL_NORMALIZED_SAMPLE_SCHEMA in LOADCELL_ENDPOINT_MAPPING_PLUGIN.accepted_input_sample_schemas
    assert LOADCELL_VECTOR_SAMPLE_SCHEMA not in LOADCELL_ENDPOINT_MAPPING_PLUGIN.accepted_input_sample_schemas


def test_loadcell_mapping_consumes_source_normalized_intent_without_reimplementing_normalization() -> None:
    intent = NormalizedLoadcellInputIntent(
        source="selfrionette",
        timestamp_s=1.0,
        values=(0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        active_channels=(0,),
        metadata={"source_kind": "selfrionette"},
    )
    mapped = LOADCELL_ENDPOINT_MAPPING_PLUGIN.strategy.map_input(
        intent,
        {
            "mapping_config": {
                "channel_axis_weights": (
                    (1.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                ),
                "gain_m": 0.01,
                "max_delta_m": 0.03,
            },
            "current_tip_position_m": (0.1, 0.2, 0.3),
        },
    )
    assert mapped.metadata["desired_endpoint_m"] == pytest.approx((0.102, 0.2, 0.3))


def test_replay_mapping_is_a_typed_raw_frame_boundary() -> None:
    mapped = REPLAY_CONTROL_MAPPING_PLUGIN.strategy.map_input(
        RawInputFrame(source="replay", timestamp_s=2.0, values=(1.0,), metadata={"x": 1}),
        {},
    )
    assert mapped.source == "replay"
    assert mapped.timestamp_s == 2.0
    assert mapped.values == (1.0,)


def test_production_loadcell_source_and_mapping_compose_with_raw_schema_boundary() -> None:
    source = INPUT_SOURCE_CATALOG.resolve("selfrionette").plugin
    manifest = build_test_manifest(
        input_source=PluginSelection("selfrionette", 1),
        control_mapping=PluginSelection("loadcell_endpoint_mapping", 1),
        command_semantics_route=ENDPOINT_DELTA_TO_JOINT_POSITION_V1,
        parameters=(
            *build_test_manifest().parameters,
            PluginParameters(
                PluginParameterOwner(
                    PluginAxis.INPUT_SOURCE,
                    PluginSelection("selfrionette", 1),
                ),
                {"lines": ("vector,1000,1,0,0,0,0,0,0",)},
            ),
            PluginParameters(
                PluginParameterOwner(
                    PluginAxis.CONTROL_MAPPING,
                    PluginSelection("loadcell_endpoint_mapping", 1),
                ),
                {
                    "mapping_config": {},
                    "current_tip_position_m": (0.1, 0.2, 0.3),
                },
            ),
        ),
    )

    resolved = compose_experiment(
        manifest,
        build_test_registries(
            input_source=source,
            mapping=LOADCELL_ENDPOINT_MAPPING_PLUGIN,
        ),
    )

    assert resolved.input_source is source
    assert resolved.control_mapping is LOADCELL_ENDPOINT_MAPPING_PLUGIN
    assert resolved.resolved_input_sample_schema == LOADCELL_VECTOR_SAMPLE_SCHEMA
    assert resolved.resolved_mapping_input_sample_schema == LOADCELL_NORMALIZED_SAMPLE_SCHEMA


def test_production_loadcell_composition_rejects_semantically_invalid_mapping_parameters() -> None:
    manifest = build_test_manifest(
        input_source=PluginSelection("selfrionette", 1),
        control_mapping=PluginSelection("loadcell_endpoint_mapping", 1),
        parameters=(
            *build_test_manifest().parameters,
            PluginParameters(
                PluginParameterOwner(
                    PluginAxis.INPUT_SOURCE,
                    PluginSelection("selfrionette", 1),
                ),
                {"lines": ("vector,1000,1,0,0,0,0,0,0",)},
            ),
            PluginParameters(
                PluginParameterOwner(
                    PluginAxis.CONTROL_MAPPING,
                    PluginSelection("loadcell_endpoint_mapping", 1),
                ),
                {
                    "mapping_config": {"gain_m": -0.01},
                    "current_tip_position_m": (0.1, 0.2, 0.3),
                },
            ),
        ),
    )

    with pytest.raises(ValueError, match="gain_m must be non-negative"):
        compose_experiment(
            manifest,
            build_test_registries(
                input_source=INPUT_SOURCE_CATALOG.resolve("selfrionette").plugin,
                mapping=LOADCELL_ENDPOINT_MAPPING_PLUGIN,
            ),
        )
