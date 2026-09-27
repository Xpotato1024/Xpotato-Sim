"""Generic versioned Input Source Plugin contracts.

This module deliberately owns no concrete device, robot, task, or viewer
implementation. ``InputSource`` is the canonical generic runtime reader
contract; this module also owns the versioned composition metadata and
optional lifecycle capability around it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from xpotato_sim.runtime.experiment.contracts import (
    ParameterContract,
    VersionedIdentity,
)
from xpotato_sim.schemas import RawInputFrame, ViewerControlMessage


@runtime_checkable
class InputSource(Protocol):
    """Structural runtime reader contract for raw input frames."""

    def read_frame(self) -> RawInputFrame:
        ...


class InputSourceMode(str, Enum):
    OFFLINE = "offline"
    REPLAY = "replay"
    LIVE = "live"
    VIEWER_BRIDGE = "viewer_bridge"


class InputSourceHealthStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    STALE = "stale"
    INVALID = "invalid"
    DISCONNECTED = "disconnected"


@runtime_checkable
class InputSourceHealthProvider(Protocol):
    """Side-effect-free source-owned health capability."""

    def current_health(self) -> InputSourceHealth:
        ...


@runtime_checkable
class HealthyInputSource(InputSource, InputSourceHealthProvider, Protocol):
    """Canonical reader boundary: frames and source-owned health are inseparable."""


@runtime_checkable
class ViewerBridgeRuntimeCapability(Protocol):
    """Optional, viewer-only runtime bridge capability.

    This is deliberately not part of the generic input-source reader
    interface. The viewer registration may expose it to runtime ingress and
    endpoint continuity code when the reader is plugin-backed.
    """

    def ingest_control_message(self, message: ViewerControlMessage) -> RawInputFrame:
        ...

    def ingest_control_message_json(self, message: str) -> RawInputFrame:
        ...

    def record_ingress_failure(self, reason: str) -> None:
        ...

    def rebase_current_endpoint_m(self, endpoint_m: Sequence[float]) -> None:
        ...

    def rebind_clock(self, clock: Callable[[], float]) -> None:
        ...


@runtime_checkable
class ViewerEndpointRebaseCapability(Protocol):
    """Narrow typed capability used only by endpoint continuity code."""

    def rebase_current_endpoint_m(self, endpoint_m: Sequence[float]) -> None:
        ...


@dataclass(frozen=True, slots=True)
class InputSourceRuntimeDependencies:
    """Typed non-manifest dependencies kept outside canonical plugin parameters."""

    replay_frames: tuple[RawInputFrame, ...] | None = None
    clock: Callable[[], float] | None = None
    line_source: tuple[str, ...] | None = None


def _freeze_metadata(name: str, value: object) -> object:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        return 0.0 if value == 0.0 else value
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_metadata(f"{name}[{index}]", item)
            for index, item in enumerate(value)
        )
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{name} keys must be strings")
            frozen[key] = _freeze_metadata(f"{name}.{key}", item)
        return MappingProxyType(dict(sorted(frozen.items())))
    raise TypeError(
        f"{name} must be a deterministic JSON-compatible value; "
        f"got {type(value).__name__}"
    )


@dataclass(frozen=True, slots=True)
class InputSourceHealth:
    status: InputSourceHealthStatus
    reason: str | None = None
    age_ms: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, InputSourceHealthStatus):
            raise TypeError("input source health status must use InputSourceHealthStatus")
        if self.status in (
            InputSourceHealthStatus.ACTIVE,
            InputSourceHealthStatus.INACTIVE,
        ):
            if self.reason is not None:
                raise ValueError(
                    f"{self.status.value} input source health must not have a failure reason"
                )
        elif not isinstance(self.reason, str) or not self.reason:
            raise ValueError(
                f"{self.status.value} input source health requires a reason"
            )
        if self.age_ms is not None and (
            type(self.age_ms) is not int or self.age_ms < 0
        ):
            raise ValueError(
                "input source health age_ms must be None or a non-negative integer"
            )
        if not isinstance(self.metadata, Mapping):
            raise TypeError("input source health metadata must use a mapping")
        object.__setattr__(
            self,
            "metadata",
            _freeze_metadata("input source health metadata", self.metadata),
        )


@runtime_checkable
class ManagedInputSource(Protocol):
    """Optional lifecycle capability for live and viewer-bridge instances."""

    def start(self) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class ManagedHealthyInputSource(
    HealthyInputSource,
    ManagedInputSource,
    Protocol,
):
    """Canonical managed reader boundary for explicit acquisition lifecycle."""


@runtime_checkable
class ViewerBridgeInputSource(ManagedHealthyInputSource, Protocol):
    @property
    def viewer_bridge_capability(self) -> ViewerBridgeRuntimeCapability: ...


@runtime_checkable
class InputSourceFactory(Protocol):
    def __call__(
        self,
        parameters: Mapping[str, object],
        *,
        runtime_dependencies: InputSourceRuntimeDependencies | None = None,
    ) -> HealthyInputSource: ...


@dataclass(frozen=True, slots=True)
class InputSourceMappingAdapterContract:
    """Versioned source-owned adaptation at the mapping boundary."""

    input_schema: VersionedIdentity
    output_schema: VersionedIdentity
    adapt: Callable[[RawInputFrame], object]

    def __post_init__(self) -> None:
        if not isinstance(self.input_schema, VersionedIdentity):
            raise TypeError("input source mapping adapter input_schema must be versioned")
        if not isinstance(self.output_schema, VersionedIdentity):
            raise TypeError("input source mapping adapter output_schema must be versioned")
        if not callable(self.adapt):
            raise TypeError("input source mapping adapter adapt must be callable")

    def __call__(self, frame: RawInputFrame) -> object:
        if not isinstance(frame, RawInputFrame):
            raise TypeError("input source mapping adapter requires RawInputFrame")
        return self.adapt(frame)


class ValidatedInputSourceReader(HealthyInputSource):
    """Read-time validation adapter for offline and replay source instances."""

    def __init__(self, delegate: HealthyInputSource) -> None:
        self._delegate = delegate

    def read_frame(self) -> RawInputFrame:
        frame = self._delegate.read_frame()
        if not isinstance(frame, RawInputFrame):
            raise TypeError(
                "input source reader returned an invalid frame: expected RawInputFrame, "
                f"got {type(frame).__name__}"
            )
        return frame

    def current_health(self) -> InputSourceHealth:
        health = self._delegate.current_health()
        if not isinstance(health, InputSourceHealth):
            raise TypeError(
                "input source reader returned invalid health: expected "
                f"InputSourceHealth, got {type(health).__name__}"
            )
        return health


class ValidatedManagedInputSourceReader(ValidatedInputSourceReader):
    """Validated reader adapter with lifecycle passthrough for managed modes."""

    def __init__(
        self,
        delegate: ManagedHealthyInputSource,
        *,
        viewer_bridge_capability: ViewerBridgeRuntimeCapability | None = None,
    ) -> None:
        super().__init__(delegate)
        self._viewer_bridge_capability = viewer_bridge_capability

    def start(self) -> None:
        self._delegate.start()

    def close(self) -> None:
        self._delegate.close()

    @property
    def viewer_bridge_capability(self) -> ViewerBridgeRuntimeCapability | None:
        return self._viewer_bridge_capability


@dataclass(frozen=True, slots=True)
class InputSourcePlugin:
    identity: VersionedIdentity
    produced_sample_schema: VersionedIdentity
    mode: InputSourceMode
    factory: InputSourceFactory
    parameter_contract: ParameterContract
    initial_health: InputSourceHealth
    initial_metadata: Mapping[str, object]
    produced_evidence: frozenset[VersionedIdentity] = field(default_factory=frozenset)
    mapping_input_adapter: InputSourceMappingAdapterContract | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, VersionedIdentity):
            raise TypeError("input source plugin identity must use VersionedIdentity")
        if not isinstance(self.produced_sample_schema, VersionedIdentity):
            raise TypeError(
                "input source produced sample schema must use VersionedIdentity"
            )
        if not isinstance(self.mode, InputSourceMode):
            raise TypeError("input source mode must use InputSourceMode")
        if not isinstance(self.factory, InputSourceFactory):
            raise TypeError("input source plugin requires a typed factory")
        if not isinstance(self.parameter_contract, ParameterContract):
            raise TypeError("input source plugin requires a ParameterContract")
        if not isinstance(self.initial_health, InputSourceHealth):
            raise TypeError("input source plugin requires typed initial health")
        if not isinstance(self.initial_metadata, Mapping):
            raise TypeError("input source initial metadata must use a mapping")
        object.__setattr__(
            self,
            "initial_metadata",
            _freeze_metadata("input source initial metadata", self.initial_metadata),
        )
        evidence = frozenset(self.produced_evidence)
        if any(not isinstance(identity, VersionedIdentity) for identity in evidence):
            raise TypeError("input source produced evidence must use VersionedIdentity")
        object.__setattr__(self, "produced_evidence", evidence)
        if self.mapping_input_adapter is not None:
            if not isinstance(self.mapping_input_adapter, InputSourceMappingAdapterContract):
                raise TypeError(
                    "input source mapping_input_adapter must use the versioned contract"
                )
            if self.mapping_input_adapter.input_schema != self.produced_sample_schema:
                raise ValueError(
                    "input source mapping adapter input_schema must match produced_sample_schema"
                )

    def create_runtime_reader(
        self,
        parameters: Mapping[str, object],
        *,
        runtime_dependencies: InputSourceRuntimeDependencies | None = None,
    ) -> ValidatedInputSourceReader | ValidatedManagedInputSourceReader:
        """Validate parameters and create one reader without starting it."""

        if not isinstance(parameters, Mapping):
            raise TypeError("input source parameters must use a mapping")
        frozen_parameters = _freeze_metadata("input source parameters", parameters)
        if not isinstance(frozen_parameters, Mapping):
            raise TypeError("input source parameters must freeze to a mapping")
        self.parameter_contract.validate(frozen_parameters)
        if runtime_dependencies is None:
            reader = self.factory(frozen_parameters)
        else:
            reader = self.factory(
                frozen_parameters,
                runtime_dependencies=runtime_dependencies,
            )
        if not isinstance(reader, HealthyInputSource):
            raise TypeError(
                "input source factory output must satisfy "
                "HealthyInputSource.read_frame()/current_health()"
            )
        managed = self.mode in (
            InputSourceMode.LIVE,
            InputSourceMode.VIEWER_BRIDGE,
        )
        if managed and not isinstance(reader, ManagedHealthyInputSource):
            raise TypeError(
                f"{self.mode.value} input source factory output must provide "
                "ManagedInputSource.start()/close()"
            )
        initial_health = reader.current_health()
        if not isinstance(initial_health, InputSourceHealth):
            raise TypeError(
                "input source factory returned invalid initial health: expected "
                f"InputSourceHealth, got {type(initial_health).__name__}"
            )
        if initial_health != self.initial_health:
            raise ValueError(
                "input source factory initial health does not match plugin initial health"
            )
        if managed:
            viewer_bridge_capability = None
            if self.mode is InputSourceMode.VIEWER_BRIDGE:
                if not isinstance(reader, ViewerBridgeInputSource):
                    raise TypeError(
                        "viewer bridge source must satisfy ViewerBridgeInputSource"
                    )
                viewer_bridge_capability = reader.viewer_bridge_capability
            return ValidatedManagedInputSourceReader(
                reader,
                viewer_bridge_capability=viewer_bridge_capability,
            )
        return ValidatedInputSourceReader(reader)

    @property
    def effective_mapping_input_sample_schema(self) -> VersionedIdentity:
        """Return the versioned representation consumed by the mapping strategy."""

        if self.mapping_input_adapter is None:
            return self.produced_sample_schema
        return self.mapping_input_adapter.output_schema

__all__ = [
    "InputSourceFactory",
    "InputSourceMappingAdapterContract",
    "InputSourceHealth",
    "InputSourceHealthStatus",
    "InputSourceHealthProvider",
    "HealthyInputSource",
    "InputSourceRuntimeDependencies",
    "InputSourceMode",
    "InputSourcePlugin",
    "InputSource",
    "ManagedInputSource",
    "ManagedHealthyInputSource",
    "RawInputFrame",
    "ViewerBridgeRuntimeCapability",
    "ViewerBridgeInputSource",
    "ViewerEndpointRebaseCapability",
    "ValidatedInputSourceReader",
    "ValidatedManagedInputSourceReader",
]
