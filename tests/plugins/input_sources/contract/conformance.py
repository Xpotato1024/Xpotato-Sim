"""Reusable, source-agnostic Input Source Plugin conformance assertions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from xpotato_sim.runtime.experiment.contracts import ControlMappingPlugin
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
    InputSourceMode,
    InputSourcePlugin,
    InputSourceRuntimeDependencies,
    ManagedInputSource,
)
from xpotato_sim.schemas import RawInputFrame


class TimestampSequencePolicy(str, Enum):
    """Explicit source-declared timestamp/order policy."""

    CONSTANT_TIMESTAMP = "constant_timestamp"
    MONOTONIC_INDEXED = "monotonic_indexed"
    PRESERVED_REPLAY_ORDER = "preserved_replay_order"
    TERMINAL_HOLD = "terminal_hold"
    NOT_APPLICABLE = "not_applicable"


FrameValidator = Callable[[RawInputFrame], None]
FrameSequenceValidator = Callable[[tuple[RawInputFrame, ...]], None]


@dataclass(frozen=True, slots=True)
class InputSourceHealthTransitionCase:
    """Optional source-local parameter case checked by the generic suite."""

    parameters: Mapping[str, object]
    expected_after_read_status: InputSourceHealthStatus
    expected_after_read_reason: str | None = None
    expected_initial_status: InputSourceHealthStatus | None = None
    runtime_dependencies: InputSourceRuntimeDependencies | None = None


@dataclass(frozen=True, slots=True)
class InputSourceConformanceCase:
    """Source-local valid parameters around the generic contract assertions."""

    plugin: InputSourcePlugin
    parameters: Mapping[str, object]
    expected_frame_source: str
    runtime_dependencies: InputSourceRuntimeDependencies | None = None
    expected_started_status: str | None = None
    reads_per_instance: int = 1
    timestamp_sequence_policy: TimestampSequencePolicy = TimestampSequencePolicy.NOT_APPLICABLE
    frame_validator: FrameValidator | None = None
    metadata_validator: Callable[[Mapping[str, object]], None] | None = None
    timestamp_sequence_validator: FrameSequenceValidator | None = None
    health_transition_cases: tuple[InputSourceHealthTransitionCase, ...] = ()


def assert_input_source_plugin_conforms(case: InputSourceConformanceCase) -> None:
    """Exercise the reusable contract against one production or test plugin.

    The case owns only source-specific valid parameters and injected dependencies;
    all identity, sample, health, lifecycle, and deterministic-reader assertions
    remain generic.
    """

    plugin = case.plugin
    if case.reads_per_instance < 1:
        raise ValueError("reads_per_instance must be positive")
    if (
        case.timestamp_sequence_policy is TimestampSequencePolicy.PRESERVED_REPLAY_ORDER
        and case.timestamp_sequence_validator is None
    ):
        raise ValueError(
            "preserved replay order requires timestamp_sequence_validator"
        )
    assert plugin.identity.name
    assert plugin.identity.version >= 1
    assert plugin.produced_sample_schema.canonical_id
    if plugin.mapping_input_adapter is not None:
        assert plugin.mapping_input_adapter.input_schema == plugin.produced_sample_schema
        assert plugin.mapping_input_adapter.output_schema.canonical_id
    assert isinstance(plugin.initial_health, InputSourceHealth)
    assert isinstance(plugin.initial_metadata, MappingProxyType)
    plugin.parameter_contract.validate(case.parameters)

    def read_once() -> RawInputFrame:
        reader = plugin.create_runtime_reader(
            case.parameters,
            runtime_dependencies=case.runtime_dependencies,
        )
        assert reader.current_health() == plugin.initial_health
        managed = plugin.mode in (InputSourceMode.LIVE, InputSourceMode.VIEWER_BRIDGE)
        if managed:
            assert isinstance(reader, ManagedInputSource)
            reader.start()
        else:
            assert not isinstance(reader, ManagedInputSource)

        frames: list[RawInputFrame] = []
        for _ in range(case.reads_per_instance):
            frame = reader.read_frame()
            assert isinstance(frame, RawInputFrame)
            assert frame.source == case.expected_frame_source
            if case.frame_validator is not None:
                case.frame_validator(frame)
            if case.metadata_validator is not None:
                case.metadata_validator(frame.metadata)
            assert reader.current_health().status.value in {
                "active",
                "inactive",
                "stale",
                "invalid",
                "disconnected",
            }
            if case.expected_started_status is not None:
                assert reader.current_health().status.value == case.expected_started_status
            frames.append(frame)
        if managed:
            reader.close()
        return tuple(frames)

    first = read_once()
    second = read_once()
    assert second == first
    if case.timestamp_sequence_policy is TimestampSequencePolicy.CONSTANT_TIMESTAMP:
        assert len({frame.timestamp_s for frame in first}) == 1
    elif case.timestamp_sequence_policy is TimestampSequencePolicy.MONOTONIC_INDEXED:
        assert all(
            left.timestamp_s <= right.timestamp_s
            for left, right in zip(first, first[1:])
        )
        indices = [frame.metadata.get("frame_index") for frame in first]
        assert all(isinstance(index, int) for index in indices)
        assert indices == sorted(indices)
    elif case.timestamp_sequence_policy is TimestampSequencePolicy.TERMINAL_HOLD:
        assert len(first) >= 2
        assert first[-1] == first[-2]
    if case.timestamp_sequence_validator is not None:
        case.timestamp_sequence_validator(first)

    for transition in case.health_transition_cases:
        reader = plugin.create_runtime_reader(
            transition.parameters,
            runtime_dependencies=transition.runtime_dependencies,
        )
        initial_health = reader.current_health()
        if transition.expected_initial_status is not None:
            assert initial_health.status is transition.expected_initial_status
        managed = plugin.mode in (InputSourceMode.LIVE, InputSourceMode.VIEWER_BRIDGE)
        if managed:
            assert isinstance(reader, ManagedInputSource)
            reader.start()
        reader.read_frame()
        health = reader.current_health()
        assert health.status is transition.expected_after_read_status
        assert health.reason == transition.expected_after_read_reason
        if managed:
            reader.close()


def assert_sample_schema_compatible(
    plugin: InputSourcePlugin,
    mapping: ControlMappingPlugin,
) -> None:
    """Keep the effective source-to-mapping schema gate explicit."""

    assert mapping.accepted_input_sample_schemas
    effective_schema = plugin.effective_mapping_input_sample_schema
    assert effective_schema in mapping.accepted_input_sample_schemas
    if plugin.mapping_input_adapter is None:
        assert effective_schema == plugin.produced_sample_schema
    else:
        assert plugin.mapping_input_adapter.input_schema == plugin.produced_sample_schema
        assert plugin.mapping_input_adapter.output_schema == effective_schema


__all__ = [
    "InputSourceConformanceCase",
    "InputSourceHealthTransitionCase",
    "TimestampSequencePolicy",
    "assert_input_source_plugin_conforms",
    "assert_sample_schema_compatible",
]
