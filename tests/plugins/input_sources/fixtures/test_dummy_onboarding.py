from dataclasses import replace

import pytest

from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.runtime.experiment.composition import PluginParameters, compose_experiment
from xpotato_sim.runtime.experiment.contracts import (
    ControlMappingPlugin,
    PluginAxis,
    PluginParameterOwner,
    PluginSelection,
    VersionedIdentity,
)
from tests.plugins.input_sources.contract.conformance import (
    InputSourceConformanceCase,
    assert_input_source_plugin_conforms,
    assert_sample_schema_compatible,
)
from tests.plugins.input_sources.fixtures.dummy_input_source import (
    DUMMY_CATALOG,
    DUMMY_INPUT_SOURCE_ID,
    DUMMY_PLUGIN,
    DUMMY_SAMPLE_SCHEMA,
    DummyCompatibleMapping,
)
from tests.runtime.test_experiment_plugin_composition import (
    build_test_manifest,
    build_test_mapping,
    build_test_registries,
)


def _compatible_mapping() -> ControlMappingPlugin:
    return replace(
        build_test_mapping(identity=VersionedIdentity("test_dummy_mapping", 1)),
        strategy=DummyCompatibleMapping(),
        accepted_input_sample_schemas=frozenset({DUMMY_SAMPLE_SCHEMA}),
    )


def test_dummy_registration_resolves_and_is_not_production_catalog() -> None:
    assert DUMMY_INPUT_SOURCE_ID.name not in INPUT_SOURCE_CATALOG.ids
    assert DUMMY_INPUT_SOURCE_ID.name not in INPUT_SOURCE_CATALOG.aliases

    registration = DUMMY_CATALOG.resolve("test_dummy_input")
    selection = DUMMY_CATALOG.resolve_selection("test_dummy_input")
    assert DUMMY_CATALOG.resolve_plugin(selection) is registration.plugin
    assert registration.plugin is DUMMY_PLUGIN


def test_dummy_plugin_conformance_and_compatible_mapping() -> None:
    assert_input_source_plugin_conforms(
        InputSourceConformanceCase(
            plugin=DUMMY_PLUGIN,
            parameters={"amplitude": 0.25},
            expected_frame_source=DUMMY_INPUT_SOURCE_ID.name,
        )
    )
    mapping = _compatible_mapping()
    assert_sample_schema_compatible(DUMMY_PLUGIN, mapping)


def test_dummy_onboarding_composes_and_incompatible_mapping_fails_closed() -> None:
    mapping = _compatible_mapping()
    manifest = build_test_manifest(
        input_source=PluginSelection(DUMMY_PLUGIN.identity.name, DUMMY_PLUGIN.identity.version),
        control_mapping=PluginSelection(mapping.identity.name, mapping.identity.version),
        parameters=(
            *build_test_manifest().parameters,
            PluginParameters(
                PluginParameterOwner(
                    PluginAxis.INPUT_SOURCE,
                    PluginSelection(DUMMY_PLUGIN.identity.name, DUMMY_PLUGIN.identity.version),
                ),
                {"amplitude": 0.25},
            ),
        ),
    )
    resolved = compose_experiment(
        manifest,
        build_test_registries(input_source=DUMMY_PLUGIN, mapping=mapping),
    )
    assert resolved.input_source is DUMMY_PLUGIN
    assert resolved.resolved_input_sample_schema == DUMMY_SAMPLE_SCHEMA
    reader = DUMMY_PLUGIN.create_runtime_reader({"amplitude": 0.25})
    assert reader.read_frame().values == (0.25,)

    incompatible = replace(
        mapping,
        accepted_input_sample_schemas=frozenset(
            {VersionedIdentity("incompatible_test_sample", 1)}
        ),
    )
    with pytest.raises(ValueError, match="input sample schema compatibility mismatch"):
        compose_experiment(
            manifest,
            build_test_registries(input_source=DUMMY_PLUGIN, mapping=incompatible),
        )
