from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.plugins.input_sources.viewer import DEFAULT_VIEWER_SAFE_ENDPOINT_M
from xpotato_sim.plugins.mappings.catalog import resolve_control_mapping_plugin
from xpotato_sim.runtime.experiment.contracts import (
    CommandSemanticsRoute,
    ControlMappingPlugin,
    PluginSelection,
    VersionedIdentity,
)
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceMappingAdapterContract,
    InputSourceMode,
    InputSourcePlugin,
    ValidatedInputSourceReader,
    ValidatedManagedInputSourceReader,
    ViewerBridgeRuntimeCapability,
)
from xpotato_sim.runtime.execution.input_source_adapters import (
    RuntimeInputSourceExecutionAdapter,
)
from xpotato_sim.runtime.control.input_source_state import (
    annotate_raw_input_frame,
    build_runtime_input_source_state_from_health,
    build_runtime_input_source_state_from_metadata,
    runtime_input_source_state_to_metadata,
)
from xpotato_sim.runtime.control.input_source_mapping_policy import (
    default_control_mapping_selection,
)
from xpotato_sim.schemas import RawInputFrame

DEFAULT_RUNTIME_SELECTION_TARGET_POSITION_M: tuple[float, float, float] = (0.6, 0.0, 0.1)

_DEFAULT_REPLAY_INITIAL_METADATA: dict[str, object] = {
    "preset": "r6-h-p5-default",
    "target_position_m": DEFAULT_RUNTIME_SELECTION_TARGET_POSITION_M,
    "desired_endpoint_m": DEFAULT_RUNTIME_SELECTION_TARGET_POSITION_M,
}

_DEFAULT_NOOP_INITIAL_METADATA: dict[str, object] = {
    "preset": "noop",
    "source_kind": "noop",
    "target_position_m": DEFAULT_RUNTIME_SELECTION_TARGET_POSITION_M,
    "desired_endpoint_m": DEFAULT_RUNTIME_SELECTION_TARGET_POSITION_M,
}

_DEFAULT_VIEWER_INITIAL_METADATA: dict[str, object] = {
    "preset": "viewer",
    "source_kind": "viewer",
    "target_position_m": DEFAULT_VIEWER_SAFE_ENDPOINT_M,
    "desired_endpoint_m": DEFAULT_VIEWER_SAFE_ENDPOINT_M,
    "source_active": False,
    "command_age_ms": 0,
    "stale_reason": "no_control_message_received",
}

_INPUT_STATE_METADATA_KEYS = (
    "source_active",
    "command_age_ms",
    "stale_reason",
)


@dataclass(frozen=True, slots=True)
class RuntimeInputSourceSelection:
    source_name: str
    frames: tuple[RawInputFrame, ...]
    loop: bool
    initial_metadata: Mapping[str, object]
    plugin_selection: PluginSelection | None = None
    resolved_plugin: InputSourcePlugin | None = None
    produced_sample_schema: VersionedIdentity | None = None
    source_mode: InputSourceMode | None = None
    runtime_reader: ValidatedInputSourceReader | ValidatedManagedInputSourceReader | None = None
    initial_health: InputSourceHealth | None = None
    execution_adapter: RuntimeInputSourceExecutionAdapter | None = None
    validated_parameters: Mapping[str, object] | None = None
    viewer_bridge_capability: ViewerBridgeRuntimeCapability | None = None
    control_mapping_selection: PluginSelection | None = None
    control_mapping: ControlMappingPlugin | None = None
    control_mapping_parameters: Mapping[str, object] = field(default_factory=dict)
    command_semantics_route_selection: VersionedIdentity | None = None
    resolved_command_semantics_route: CommandSemanticsRoute | None = None
    mapping_input_sample_schema: VersionedIdentity | None = None
    mapping_input_adapter: InputSourceMappingAdapterContract | None = None

    @property
    def effective_mapping_input_sample_schema(self) -> VersionedIdentity | None:
        return self.mapping_input_sample_schema


def _canonicalize_selected_frame(
    frame: RawInputFrame,
    *,
    default_source_kind: str,
    default_state,
) -> RawInputFrame:
    state = default_state
    if any(key in frame.metadata for key in _INPUT_STATE_METADATA_KEYS):
        state = build_runtime_input_source_state_from_metadata(
            frame.metadata,
            default_source_kind=default_source_kind,
        )
    return annotate_raw_input_frame(frame, state)


def _normalize_control_mapping_parameters(
    control_mapping: ControlMappingPlugin | None,
    selected_parameters: Mapping[str, object],
) -> Mapping[str, object]:
    if control_mapping is None:
        return MappingProxyType({})

    return control_mapping.normalize_runtime_parameters(selected_parameters)


def _resolve_control_mapping_parameters(
    control_mapping: ControlMappingPlugin | None,
    control_mapping_parameters: Mapping[str, object] | None,
) -> Mapping[str, object]:
    selected_parameters = (
        {} if control_mapping_parameters is None else control_mapping_parameters
    )
    return _normalize_control_mapping_parameters(control_mapping, selected_parameters)


def select_runtime_input_source(
    source_name: str,
    *,
    steps: int,
    frames: Sequence[RawInputFrame] | None = None,
    line_source: Sequence[str] | None = None,
    samples: Sequence[Mapping[str, object]] | None = None,
    preset: str | None = None,
    replay_initial_metadata: Mapping[str, object] | None = None,
    control_mapping_selection: PluginSelection | None = None,
    control_mapping_parameters: Mapping[str, object] | None = None,
    command_semantics_route_selection: VersionedIdentity | None = None,
) -> RuntimeInputSourceSelection:
    registration = INPUT_SOURCE_CATALOG.resolve(source_name)
    plugin_selection = PluginSelection(
        registration.plugin.identity.name,
        registration.plugin.identity.version,
    )
    plugin = INPUT_SOURCE_CATALOG.resolve_plugin(plugin_selection)
    request = registration.request_builder(
        steps=steps,
        frames=frames,
        line_source=line_source,
        samples=samples,
        preset=preset,
        replay_initial_metadata=replay_initial_metadata,
    )
    source_state = build_runtime_input_source_state_from_health(
        plugin.initial_health,
        source_kind=plugin.identity.name,
    )
    selected_frames = tuple(
        _canonicalize_selected_frame(
            frame,
            default_source_kind=plugin.identity.name,
            default_state=source_state,
        )
        for frame in request.frames
    )
    runtime_dependencies = request.runtime_dependencies
    if runtime_dependencies is not None and runtime_dependencies.replay_frames is not None:
        runtime_dependencies = replace(
            runtime_dependencies,
            replay_frames=selected_frames,
        )
    resolved_mapping_selection = (
        control_mapping_selection
        if control_mapping_selection is not None
        else default_control_mapping_selection(plugin.identity.name)
    )
    control_mapping = (
        resolve_control_mapping_plugin(resolved_mapping_selection)
        if resolved_mapping_selection is not None
        else None
    )
    effective_mapping_schema = plugin.effective_mapping_input_sample_schema
    if (
        control_mapping is not None
        and effective_mapping_schema not in control_mapping.accepted_input_sample_schemas
    ):
        raise ValueError(
            "input sample schema compatibility mismatch: mapping input is "
            f"{effective_mapping_schema.canonical_id!r}, mapping accepts "
            f"{tuple(sorted(item.canonical_id for item in control_mapping.accepted_input_sample_schemas))!r}"
        )
    resolved_mapping_parameters = _resolve_control_mapping_parameters(
        control_mapping,
        control_mapping_parameters,
    )
    resolved_command_semantics_route = (
        control_mapping.resolve_command_semantics_route(
            command_semantics_route_selection
        )
        if control_mapping is not None
        else None
    )

    # No managed reader is created until source/mapping schema and all mapping
    # parameters have passed readiness. In particular, invalid mapping input
    # cannot reach source start or the first frame read.
    reader = plugin.create_runtime_reader(
        request.parameters,
        runtime_dependencies=runtime_dependencies,
    )
    viewer_bridge_capability = (
        reader.viewer_bridge_capability
        if isinstance(reader, ValidatedManagedInputSourceReader)
        else None
    )
    if plugin.mode is InputSourceMode.VIEWER_BRIDGE and viewer_bridge_capability is None:
        raise ValueError("viewer input source plugin is missing its runtime bridge capability")
    initial_source_state = (
        build_runtime_input_source_state_from_metadata(
            selected_frames[0].metadata,
            default_source_kind=plugin.identity.name,
        )
        if selected_frames
        else source_state
    )
    initial_metadata = {
        **plugin.initial_metadata,
        **request.initial_metadata,
        **runtime_input_source_state_to_metadata(initial_source_state),
    }

    return RuntimeInputSourceSelection(
        source_name=registration.cli_aliases[0],
        frames=selected_frames,
        loop=request.loop,
        initial_metadata=initial_metadata,
        plugin_selection=plugin_selection,
        resolved_plugin=plugin,
        produced_sample_schema=plugin.produced_sample_schema,
        source_mode=plugin.mode,
        runtime_reader=reader,
        initial_health=plugin.initial_health,
        execution_adapter=registration.execution_adapter,
        validated_parameters=request.parameters,
        viewer_bridge_capability=viewer_bridge_capability,
        control_mapping_selection=resolved_mapping_selection,
        control_mapping=control_mapping,
        control_mapping_parameters=resolved_mapping_parameters,
        command_semantics_route_selection=(
            resolved_command_semantics_route.identity
            if resolved_command_semantics_route is not None
            else None
        ),
        resolved_command_semantics_route=resolved_command_semantics_route,
        mapping_input_sample_schema=(
            effective_mapping_schema if control_mapping is not None else None
        ),
        mapping_input_adapter=plugin.mapping_input_adapter,
    )


__all__ = [
    "RuntimeInputSourceSelection",
    "select_runtime_input_source",
]
