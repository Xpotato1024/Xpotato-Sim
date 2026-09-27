from __future__ import annotations

from dataclasses import replace

import pytest

from xpotato_sim.runtime.experiment.composition import (
    PluginParameters,
    compose_experiment,
)
from xpotato_sim.runtime.experiment.contracts import (
    ParameterContract,
    ParameterField,
    PluginAxis,
    PluginParameterOwner,
    PluginSelection,
    VersionedIdentity,
)
from xpotato_sim.runtime.experiment.input_source import (
    InputSource,
    InputSourceHealth,
    InputSourceHealthStatus,
    InputSourceHealthProvider,
    InputSourceMappingAdapterContract,
    InputSourceMode,
    InputSourcePlugin,
    ManagedInputSource,
    ValidatedInputSourceReader,
    ValidatedManagedInputSourceReader,
)
from xpotato_sim.plugins.input_sources.registration import InputSourcePluginRegistration
from xpotato_sim.runtime.experiment.registry import VersionedPluginRegistry
from xpotato_sim.schemas import RawInputFrame
from tests.runtime.test_experiment_plugin_composition import (
    build_test_manifest,
    build_test_mapping,
    build_test_registries,
    build_test_task,
)
from tests.support.input_source_plugin_doubles import (
    CONFORMANCE_INPUT_SOURCE,
    CONFORMANCE_SAMPLE_SCHEMA,
    ConformanceInputSourceReader,
    ManagedConformanceInputSourceReader,
    ReaderWithFrameSequence,
    ReaderWithInvalidFrame,
    ReaderWithInvalidHealth,
    ReaderWithoutHealth,
    build_conformance_input_source,
)


def test_input_source_contract_is_runtime_checkable_and_structural() -> None:
    class StructuralReader:
        def read_frame(self) -> RawInputFrame:
            return RawInputFrame(source="structural", timestamp_s=0.0)

    reader = StructuralReader()
    assert isinstance(reader, InputSource)


def test_valid_plugin_reader_and_metadata_are_detached() -> None:
    metadata = {"nested": ["stable"]}
    plugin = replace(build_conformance_input_source(), initial_metadata=metadata)
    metadata["nested"].append("mutated")

    reader = plugin.create_runtime_reader({})

    assert reader.read_frame().values == (0.25, -0.5)
    assert plugin.initial_metadata["nested"] == ("stable",)


@pytest.mark.parametrize(
    "change, message",
    (
        ({"mode": "offline"}, "mode"),
        ({"produced_sample_schema": "sample/v1"}, "schema"),
        ({"factory": object()}, "factory"),
        ({"initial_health": object()}, "health"),
        ({"initial_metadata": {1: "bad"}}, "keys"),
        ({"produced_evidence": frozenset({"evidence/v1"})}, "evidence"),
    ),
)
def test_invalid_plugin_declarations_fail_closed(change: dict[str, object], message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        replace(build_conformance_input_source(), **change)


def test_health_contract_rejects_contradictory_or_invalid_values() -> None:
    with pytest.raises(ValueError, match="failure reason"):
        InputSourceHealth(InputSourceHealthStatus.ACTIVE, reason="failed")
    with pytest.raises(ValueError, match="requires a reason"):
        InputSourceHealth(InputSourceHealthStatus.STALE)
    with pytest.raises(ValueError, match="non-negative"):
        InputSourceHealth(InputSourceHealthStatus.STALE, reason="old", age_ms=-1)
    with pytest.raises(ValueError, match="non-negative"):
        InputSourceHealth(InputSourceHealthStatus.STALE, reason="old", age_ms=True)


def test_factory_output_and_parameters_are_validated_without_fallback() -> None:
    def bad_factory(parameters):
        return object()

    plugin = replace(build_conformance_input_source(), factory=bad_factory)
    with pytest.raises(TypeError, match="must satisfy"):
        plugin.create_runtime_reader({})

    parameter_plugin = replace(
        build_conformance_input_source(),
        parameter_contract=ParameterContract((ParameterField("gain", float),)),
    )
    with pytest.raises(ValueError, match="missing required"):
        parameter_plugin.create_runtime_reader({})
    with pytest.raises(ValueError, match="not bool"):
        parameter_plugin.create_runtime_reader({"gain": True})


def test_mapping_adapter_contract_is_versioned_and_uses_effective_schema() -> None:
    output_schema = VersionedIdentity("normalized_conformance_sample", 1)
    plugin = replace(
        build_conformance_input_source(),
        mapping_input_adapter=InputSourceMappingAdapterContract(
            input_schema=CONFORMANCE_SAMPLE_SCHEMA,
            output_schema=output_schema,
            adapt=lambda frame: frame,
        ),
    )

    assert plugin.mapping_input_adapter is not None
    assert plugin.mapping_input_adapter.input_schema == CONFORMANCE_SAMPLE_SCHEMA
    assert plugin.mapping_input_adapter.output_schema == output_schema
    assert plugin.effective_mapping_input_sample_schema == output_schema


def test_mapping_adapter_input_schema_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="input_schema must match"):
        replace(
            build_conformance_input_source(),
            mapping_input_adapter=InputSourceMappingAdapterContract(
                input_schema=VersionedIdentity("wrong_raw_sample", 1),
                output_schema=VersionedIdentity("normalized_conformance_sample", 1),
                adapt=lambda frame: frame,
            ),
        )


def test_offline_does_not_require_lifecycle_and_live_does() -> None:
    offline = build_conformance_input_source(mode=InputSourceMode.OFFLINE)
    offline_reader = offline.create_runtime_reader({})
    assert isinstance(offline_reader, ValidatedInputSourceReader)
    assert not isinstance(offline_reader, ManagedInputSource)

    def plain_factory(parameters):
        return ConformanceInputSourceReader(parameters)

    live = replace(
        build_conformance_input_source(mode=InputSourceMode.LIVE), factory=plain_factory
    )
    with pytest.raises(TypeError, match="ManagedInputSource"):
        live.create_runtime_reader({})


def test_live_factory_creation_is_side_effect_free_until_explicit_start() -> None:
    created = []

    def factory(parameters):
        reader = ManagedConformanceInputSourceReader(parameters)
        created.append(reader)
        return reader

    plugin = build_conformance_input_source(
        mode=InputSourceMode.VIEWER_BRIDGE, factory_override=factory
    )
    reader = plugin.create_runtime_reader({})

    assert isinstance(reader, ManagedInputSource)
    assert isinstance(reader, ValidatedManagedInputSourceReader)
    assert isinstance(created[0], ManagedConformanceInputSourceReader)
    assert created[0].started is False
    assert created[0].closed is False
    reader.start()
    assert created[0].started is True
    reader.close()
    assert created[0].closed is True


def test_factory_output_requires_side_effect_free_typed_health_provider() -> None:
    with pytest.raises(TypeError, match="HealthyInputSource"):
        replace(
            build_conformance_input_source(),
            factory=ReaderWithoutHealth,
        ).create_runtime_reader({})

    with pytest.raises(TypeError, match="invalid initial health"):
        replace(
            build_conformance_input_source(),
            factory=ReaderWithInvalidHealth,
        ).create_runtime_reader({})


def test_initial_health_is_checked_once_without_frame_or_lifecycle_side_effects() -> None:
    created = []

    def factory(parameters):
        reader = ManagedConformanceInputSourceReader(parameters)
        created.append(reader)
        return reader

    plugin = build_conformance_input_source(
        mode=InputSourceMode.LIVE, factory_override=factory
    )
    adapter = plugin.create_runtime_reader({})

    assert isinstance(adapter, ValidatedManagedInputSourceReader)
    assert created[0].health_calls == 1
    assert created[0].read_calls == 0
    assert created[0].started is False
    assert created[0].closed is False


def test_initial_health_mismatch_is_rejected_without_fallback() -> None:
    created = []
    stale = InputSourceHealth(
        InputSourceHealthStatus.STALE, reason="no recent sample", age_ms=12
    )

    def factory(parameters):
        reader = ConformanceInputSourceReader(parameters)
        reader.set_health(stale)
        created.append(reader)
        return reader

    with pytest.raises(ValueError, match="initial health does not match"):
        build_conformance_input_source(factory_override=factory).create_runtime_reader({})
    assert created[0].health_calls == 1
    assert created[0].read_calls == 0


@pytest.mark.parametrize(
    "status, reason, age_ms",
    (
        (InputSourceHealthStatus.STALE, "timeout", 5),
        (InputSourceHealthStatus.INVALID, "malformed sample", 6),
        (InputSourceHealthStatus.DISCONNECTED, "transport closed", 7),
    ),
)
def test_health_transitions_remain_typed_and_immutable(
    status: InputSourceHealthStatus, reason: str, age_ms: int
) -> None:
    created = []

    def factory(parameters):
        reader = ConformanceInputSourceReader(parameters)
        created.append(reader)
        return reader

    adapter = build_conformance_input_source(factory_override=factory).create_runtime_reader({})
    assert isinstance(adapter, InputSourceHealthProvider)
    created[0].set_health(
        InputSourceHealth(status, reason=reason, age_ms=age_ms, metadata={"code": status.value})
    )

    health = adapter.current_health()
    assert health.status is status
    assert health.reason == reason
    assert health.age_ms == age_ms
    assert health.metadata["code"] == status.value
    with pytest.raises(TypeError):
        health.metadata["new"] = "mutation"  # type: ignore[index]


def test_adapter_validates_current_health_on_every_call() -> None:
    created = []

    def factory(parameters):
        reader = ConformanceInputSourceReader(parameters)
        created.append(reader)
        return reader

    adapter = build_conformance_input_source(factory_override=factory).create_runtime_reader({})
    assert created[0].health_calls == 1
    created[0].current_health = lambda: object()

    with pytest.raises(TypeError, match="expected InputSourceHealth"):
        adapter.current_health()

    assert created[0].health_calls == 1


@pytest.mark.parametrize("invalid_frame", ({"not": "RawInputFrame"}, (1, 2), [1, 2], None, object()))
def test_read_frame_output_is_validated_at_read_time(invalid_frame: object) -> None:
    created = []

    def factory(parameters):
        reader = ReaderWithInvalidFrame(parameters, invalid_frame)
        created.append(reader)
        return reader

    adapter = build_conformance_input_source(factory_override=factory).create_runtime_reader({})
    assert created[0].read_calls == 0
    with pytest.raises(TypeError, match="expected RawInputFrame"):
        adapter.read_frame()
    assert created[0].read_calls == 1


def test_valid_frame_is_returned_unchanged_and_each_read_is_checked() -> None:
    frame = RawInputFrame(source="fixture", timestamp_s=1.0, values=(1.0,))
    created = []

    def factory(parameters):
        reader = ReaderWithFrameSequence(parameters, [frame, {"bad": True}])
        created.append(reader)
        return reader

    adapter = build_conformance_input_source(factory_override=factory).create_runtime_reader({})
    assert adapter.read_frame() is frame
    with pytest.raises(TypeError, match="expected RawInputFrame"):
        adapter.read_frame()
    assert created[0].read_calls == 2


@pytest.mark.parametrize("exception", (StopIteration("end"), RuntimeError("domain failure")))
def test_delegate_read_exceptions_are_preserved(exception: Exception) -> None:
    class RaisingReader(ConformanceInputSourceReader):
        def read_frame(self):
            self.read_calls += 1
            raise exception

    adapter = build_conformance_input_source(
        factory_override=RaisingReader
    ).create_runtime_reader({})
    with pytest.raises(type(exception), match=str(exception)):
        adapter.read_frame()


def test_replay_has_no_managed_lifecycle_and_viewer_bridge_does() -> None:
    replay = build_conformance_input_source(mode=InputSourceMode.REPLAY)
    assert not isinstance(replay.create_runtime_reader({}), ManagedInputSource)

    viewer = build_conformance_input_source(mode=InputSourceMode.VIEWER_BRIDGE)
    assert isinstance(viewer.create_runtime_reader({}), ManagedInputSource)


def test_registry_resolve_and_ids_are_deterministic() -> None:
    alpha = replace(
        build_conformance_input_source(), identity=VersionedIdentity("alpha", 1)
    )
    beta = replace(
        build_conformance_input_source(), identity=VersionedIdentity("beta", 1)
    )
    registry = VersionedPluginRegistry((beta, alpha), kind="input source plugin")
    assert registry.ids == ("alpha", "beta")
    assert registry.resolve(PluginSelection("alpha", 1)) is alpha
    with pytest.raises(ValueError, match="unknown input source plugin ID"):
        registry.resolve(PluginSelection("unknown", 1))
    with pytest.raises(ValueError, match="contract version mismatch"):
        registry.resolve(PluginSelection("alpha", 2))
    with pytest.raises(ValueError, match="duplicate input source plugin registration"):
        VersionedPluginRegistry((alpha, alpha), kind="input source plugin")


def test_registration_requires_typed_execution_adapter() -> None:
    from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG

    registration = INPUT_SOURCE_CATALOG.resolve("replay")
    with pytest.raises(TypeError, match="typed execution adapter"):
        InputSourcePluginRegistration(
            plugin=registration.plugin,
            cli_aliases=registration.cli_aliases,
            request_builder=registration.request_builder,
            execution_adapter=object(),  # type: ignore[arg-type]
        )


def test_composition_resolves_source_schema_and_evidence_without_factory_call() -> None:
    evidence = VersionedIdentity("source.diagnostic", 1)
    source = build_conformance_input_source(produced_evidence=frozenset({evidence}))
    resolved = compose_experiment(
        build_test_manifest(
            parameters=(
                *build_test_manifest().parameters,
                PluginParameters(
                    PluginParameterOwner(
                        PluginAxis.INPUT_SOURCE,
                        PluginSelection(CONFORMANCE_INPUT_SOURCE.name, 1),
                    ),
                    {},
                ),
            )
        ),
        build_test_registries(input_source=source),
    )
    assert resolved.input_source is source
    assert resolved.resolved_input_sample_schema == CONFORMANCE_SAMPLE_SCHEMA
    assert resolved.evidence_producer(evidence).producer_axis is PluginAxis.INPUT_SOURCE


def test_composition_does_not_invoke_input_source_factory() -> None:
    calls = 0

    def factory(parameters):
        nonlocal calls
        calls += 1
        return ConformanceInputSourceReader(parameters)

    source = build_conformance_input_source(factory_override=factory)
    compose_experiment(build_test_manifest(), build_test_registries(input_source=source))

    assert calls == 0


def test_composition_rejects_schema_mismatch_and_empty_mapping_acceptance() -> None:
    source = build_conformance_input_source()
    incompatible = replace(
        build_test_mapping(),
        accepted_input_sample_schemas=frozenset(
            {VersionedIdentity("other_sample", 1)}
        ),
    )
    with pytest.raises(ValueError, match="input sample schema compatibility mismatch"):
        compose_experiment(build_test_manifest(), build_test_registries(input_source=source, mapping=incompatible))

    empty = replace(build_test_mapping(), accepted_input_sample_schemas=frozenset())
    with pytest.raises(ValueError, match="at least one accepted"):
        compose_experiment(build_test_manifest(), build_test_registries(input_source=source, mapping=empty))


def test_composition_uses_effective_mapping_schema_and_rejects_missing_or_wrong_adapter() -> None:
    normalized_schema = VersionedIdentity("normalized_conformance_sample", 1)
    mapping = replace(
        build_test_mapping(),
        accepted_input_sample_schemas=frozenset({normalized_schema}),
    )
    adapter = InputSourceMappingAdapterContract(
        input_schema=CONFORMANCE_SAMPLE_SCHEMA,
        output_schema=normalized_schema,
        adapt=lambda frame: frame,
    )

    resolved = compose_experiment(
        build_test_manifest(),
        build_test_registries(
            input_source=replace(build_conformance_input_source(), mapping_input_adapter=adapter),
            mapping=mapping,
        ),
    )
    assert resolved.resolved_input_sample_schema == CONFORMANCE_SAMPLE_SCHEMA
    assert resolved.resolved_mapping_input_sample_schema == normalized_schema

    with pytest.raises(ValueError, match="schema compatibility mismatch"):
        compose_experiment(
            build_test_manifest(),
            build_test_registries(
                input_source=build_conformance_input_source(),
                mapping=mapping,
            ),
        )

    wrong_output = replace(
        build_conformance_input_source(),
        mapping_input_adapter=InputSourceMappingAdapterContract(
            input_schema=CONFORMANCE_SAMPLE_SCHEMA,
            output_schema=VersionedIdentity("other_normalized_sample", 1),
            adapt=lambda frame: frame,
        ),
    )
    with pytest.raises(ValueError, match="schema compatibility mismatch"):
        compose_experiment(
            build_test_manifest(),
            build_test_registries(input_source=wrong_output, mapping=mapping),
        )


def test_no_mapping_adapter_uses_produced_schema_directly() -> None:
    source = build_conformance_input_source()
    assert source.mapping_input_adapter is None
    assert source.effective_mapping_input_sample_schema == CONFORMANCE_SAMPLE_SCHEMA


def test_composition_rejects_unselected_source_parameters_and_wrong_registry_type() -> None:
    source_owner = PluginParameterOwner(
        PluginAxis.INPUT_SOURCE, PluginSelection(CONFORMANCE_INPUT_SOURCE.name, 1)
    )
    unselected_owner = PluginParameterOwner(
        PluginAxis.INPUT_SOURCE, PluginSelection("other_source", 1)
    )
    with pytest.raises(ValueError, match="unselected plugins"):
        compose_experiment(
            build_test_manifest(parameters=(PluginParameters(unselected_owner, {}),)),
            build_test_registries(),
        )
    resolved = compose_experiment(
        build_test_manifest(
            parameters=(*build_test_manifest().parameters, PluginParameters(source_owner, {}))
        ),
        build_test_registries(),
    )
    assert resolved.input_source.identity == CONFORMANCE_INPUT_SOURCE

    wrong = replace(
        build_test_registries(),
        input_sources=VersionedPluginRegistry((build_test_task(),), kind="input source plugin"),
    )
    with pytest.raises(ValueError, match="registry-set type mismatch for input source"):
        compose_experiment(
        build_test_manifest(input_source=PluginSelection("dummy_reach_task", 1)), wrong
        )
