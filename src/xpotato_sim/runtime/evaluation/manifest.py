"""immutable evaluation manifestとsoftware-only startup readiness。

6軸composition、command route、upper manifestからTask contextへのbindingを
side effect前に確定する。model load、physics step、Task実行、log出力、metric導出は
このmoduleの責務ではない。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from xpotato_sim.runtime.experiment.composition import (
    EvidenceProducerBinding,
    ExperimentPluginManifest,
    ExperimentPluginRegistries,
    PluginParameters,
    ResolvedExperimentComposition,
    compose_experiment,
    freeze_parameter_value,
    parameter_value_to_document,
)
from xpotato_sim.runtime.experiment.contracts import (
    CommandSemanticsRoute,
    EnvironmentRole,
    PluginAxis,
    PluginParameterOwner,
    PluginSelection,
    TaskExecutionBinding,
    VersionedIdentity,
)
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    EndpointReachTaskContext,
)
from xpotato_sim.runtime.composition.robot_bundle import (
    InitialStateContract,
    InitialStateContractProvider,
    InitialStateReference,
    RESET_INITIAL_STATE_V1,
    ResetInitialStateProvider,
)


EVALUATION_MANIFEST_SCHEMA_VERSION = "evaluation-manifest/v3"
EVALUATION_MANIFEST_CONTRACT_VERSION = 3
EVALUATION_MANIFEST_DIGEST_ALGORITHM = "sha256"
EVALUATION_FREEZE_SCHEMA_VERSION = "evaluation-freeze/v1"
_CONTROL_FRAMES = frozenset({"world", "tool"})
_QUATERNION_ORDER = "wxyz"
_QUATERNION_UNIT = "unit_quaternion"
_FLOAT_TOLERANCE = 1e-12
_GIT_SHA1_REVISION = re.compile(r"git-sha1:[0-9a-f]{40}\Z")
_GIT_SHA256_REVISION = re.compile(r"git-sha256:[0-9a-f]{64}\Z")
_TEST_REVISION = re.compile(r"test-revision:[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class ReadinessStatus(str, Enum):
    READY = "ready"


class EvaluationManifestError(ValueError):
    """Base class for strict manifest and readiness failures."""


class EvaluationManifestDecodeError(EvaluationManifestError):
    """Raised when a canonical document cannot be decoded strictly."""


class EvaluationReadinessError(EvaluationManifestError):
    """Raised when a condition cannot be handed to a runner."""


def _identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise EvaluationManifestError(f"{name} must be a non-empty string")
    if "\x00" in value:
        raise EvaluationManifestError(f"{name} must not contain NUL")
    return value


def _stable_identity(name: str, value: object) -> str:
    result = _identifier(name, value)
    if result.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", result):
        raise EvaluationManifestError(f"{name} must not be a local path")
    return result


def _software_revision_identity(value: object) -> str:
    result = _stable_identity("software_revision_identity", value)
    if not (
        _GIT_SHA1_REVISION.fullmatch(result)
        or _GIT_SHA256_REVISION.fullmatch(result)
        or _TEST_REVISION.fullmatch(result)
    ):
        raise EvaluationManifestError(
            "software_revision_identity must use an explicit stable scheme "
            "(git-sha1, git-sha256, or test-revision)"
        )
    return result


@dataclass(frozen=True, slots=True)
class SoftwareExecutionIdentity:
    """Actual repository/software identity observed by the startup caller."""

    repository_identity: str
    software_revision_identity: str

    def __post_init__(self) -> None:
        _stable_identity("execution repository_identity", self.repository_identity)
        _software_revision_identity(self.software_revision_identity)


def _enum(name: str, value: object, choices: frozenset[str]) -> str:
    result = _identifier(name, value)
    if result not in choices:
        raise EvaluationManifestError(f"{name} must be one of {sorted(choices)!r}")
    return result


def _finite_float(
    name: str,
    value: object,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationManifestError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise EvaluationManifestError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise EvaluationManifestError(f"{name} must be finite")
    if positive and result <= 0.0:
        raise EvaluationManifestError(f"{name} must be positive")
    if non_negative and result < 0.0:
        raise EvaluationManifestError(f"{name} must be non-negative")
    return 0.0 if result == 0.0 else result


def _non_negative_int(name: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise EvaluationManifestError(f"{name} must be a non-negative integer")
    return value


def _positive_int(name: str, value: object) -> int:
    if type(value) is not int or value < 1:
        raise EvaluationManifestError(f"{name} must be a positive integer")
    return value


def _vector(name: str, value: object, *, length: int | None = None) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise EvaluationManifestError(f"{name} must be a numeric array")
    result = tuple(_finite_float(f"{name}[{index}]", item) for index, item in enumerate(value))
    if not result:
        raise EvaluationManifestError(f"{name} must not be empty")
    if length is not None and len(result) != length:
        raise EvaluationManifestError(
            f"{name} must contain exactly {length} values; got {len(result)}"
        )
    return result


def _unit_quaternion(name: str, value: object) -> tuple[float, float, float, float]:
    result = _vector(name, value, length=4)
    norm = math.sqrt(sum(component * component for component in result))
    if abs(norm - 1.0) > _FLOAT_TOLERANCE:
        raise EvaluationManifestError(f"{name} must be a unit quaternion")
    return result  # type: ignore[return-value]


def _selection(name: str, value: object) -> PluginSelection:
    if not isinstance(value, PluginSelection):
        raise EvaluationManifestError(f"{name} must use PluginSelection")
    _stable_identity(f"{name}.plugin_id", value.plugin_id)
    if type(value.contract_version) is not int or value.contract_version < 1:
        raise EvaluationManifestError(f"{name} has an invalid contract version")
    return value


def _identity(name: str, value: object) -> VersionedIdentity:
    if not isinstance(value, VersionedIdentity):
        raise EvaluationManifestError(f"{name} must use VersionedIdentity")
    _stable_identity(f"{name}.name", value.name)
    if type(value.version) is not int or value.version < 1:
        raise EvaluationManifestError(f"{name} has an invalid version")
    return value


def _parameter_values(item: PluginParameters) -> Mapping[str, object]:
    values = item.values
    if not isinstance(values, Mapping):
        raise EvaluationManifestError("plugin parameter values must use a mapping")
    for name, value in values.items():
        _identifier("plugin parameter name", name)
        try:
            freeze_parameter_value(f"plugin parameter {name!r}", value)
        except (TypeError, ValueError) as exc:
            raise EvaluationManifestError(str(exc)) from exc
    return values


def _selection_document(selection: PluginSelection) -> dict[str, object]:
    return {
        "plugin_id": selection.plugin_id,
        "contract_version": selection.contract_version,
    }


def _identity_document(identity: VersionedIdentity) -> dict[str, object]:
    return {"name": identity.name, "version": identity.version}


def _parameter_owner_document(owner: PluginParameterOwner) -> dict[str, object]:
    return {
        "axis": owner.axis.value,
        "selection": _selection_document(owner.selection),
    }


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: bytes) -> str:
    return f"{EVALUATION_MANIFEST_DIGEST_ALGORITHM}:{hashlib.sha256(value).hexdigest()}"


def _digest_value(name: str, value: object) -> str:
    result = _identifier(name, value)
    prefix = f"{EVALUATION_MANIFEST_DIGEST_ALGORITHM}:"
    digest_hex = result.removeprefix(prefix)
    if not result.startswith(prefix) or len(digest_hex) != 64:
        raise EvaluationManifestError(f"{name} must be a canonical {prefix} digest")
    try:
        int(digest_hex, 16)
    except ValueError as exc:
        raise EvaluationManifestError(f"{name} must contain hexadecimal digest bytes") from exc
    return result


def _require_object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise EvaluationManifestDecodeError(f"{name} must be an object")
    return value


def _require_fields(
    value: object,
    name: str,
    expected: frozenset[str],
) -> Mapping[str, object]:
    result = _require_object(value, name)
    actual = frozenset(result)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise EvaluationManifestDecodeError(f"{name} has unknown fields: {unknown}")
    if missing:
        raise EvaluationManifestDecodeError(f"{name} is missing fields: {missing}")
    return result


def _clone_document(value: object, name: str = "document") -> object:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise EvaluationManifestDecodeError(f"{name} object keys must be strings")
        return {
            key: _clone_document(item, f"{name}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_clone_document(item, f"{name}[{index}]") for index, item in enumerate(value)]
    if value is None or type(value) in (bool, int, float, str):
        return value
    raise EvaluationManifestDecodeError(f"{name} contains a non-JSON value")


def _parse_json_document(document: bytes | str | Mapping[str, object]) -> Mapping[str, object]:
    if isinstance(document, Mapping):
        value = _clone_document(document)
        return _require_object(value, "manifest")
    if isinstance(document, bytes):
        try:
            text = document.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise EvaluationManifestDecodeError("manifest must be valid UTF-8") from exc
    elif isinstance(document, str):
        text = document
    else:
        raise TypeError("manifest document must be UTF-8 bytes, text, or an object")

    def reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise EvaluationManifestDecodeError(
                    f"duplicate field in manifest object: {key!r}"
                )
            result[key] = value
        return result

    def reject_non_finite_constant(value: str) -> object:
        raise EvaluationManifestDecodeError(f"non-finite JSON constant is forbidden: {value}")

    try:
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicate_fields,
            parse_constant=reject_non_finite_constant,
        )
    except EvaluationManifestDecodeError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise EvaluationManifestDecodeError("manifest is not valid JSON") from exc
    return _require_object(value, "manifest")


def _document_string(document: Mapping[str, object], name: str) -> str:
    field = name.rsplit(".", 1)[-1]
    key = name if name in document else field
    return _identifier(name, document[key])


def _document_selection(document: Mapping[str, object], name: str) -> PluginSelection:
    value = _require_fields(
        document,
        name,
        frozenset({"plugin_id", "contract_version"}),
    )
    return PluginSelection(
        plugin_id=_document_string(value, f"{name}.plugin_id"),
        contract_version=_positive_int(
            f"{name}.contract_version", value["contract_version"]
        ),
    )


def _document_identity(document: Mapping[str, object], name: str) -> VersionedIdentity:
    value = _require_fields(document, name, frozenset({"name", "version"}))
    return VersionedIdentity(
        name=_document_string(value, f"{name}.name"),
        version=_positive_int(f"{name}.version", value["version"]),
    )


def _document_vector(
    document: Mapping[str, object],
    name: str,
    *,
    length: int | None = None,
) -> tuple[float, ...]:
    value = document[name]
    if not isinstance(value, list):
        raise EvaluationManifestDecodeError(f"{name} must be an array")
    try:
        return _vector(name, value, length=length)
    except EvaluationManifestError:
        raise


def _document_parameter_values(value: object, name: str) -> dict[str, object]:
    document = _require_object(value, name)
    result: dict[str, object] = {}
    for key, item in document.items():
        _identifier(f"{name} field", key)
        try:
            result[key] = freeze_parameter_value(f"{name}.{key}", item)
        except (TypeError, ValueError) as exc:
            raise EvaluationManifestDecodeError(str(exc)) from exc
    return result


def _document_axis(document: Mapping[str, object], name: str) -> PluginAxis:
    value = _document_string(document, name)
    try:
        return PluginAxis(value)
    except ValueError as exc:
        raise EvaluationManifestDecodeError(
            f"{name} is not a valid plugin axis: {value!r}"
        ) from exc


@dataclass(frozen=True, slots=True)
class EvaluationManifest:
    """Versioned, immutable, condition-level evaluation configuration."""

    schema_version: str
    contract_version: int
    repository_identity: str
    software_revision_identity: str
    robot_bundle: PluginSelection
    robot_profile_identity: VersionedIdentity
    runtime_plugin_identity: VersionedIdentity
    model_contract_identity: VersionedIdentity
    initial_state_contract_identity: VersionedIdentity
    environment: PluginSelection
    control_mapping: PluginSelection
    task: PluginSelection
    input_source: PluginSelection
    command_semantics_route_identity: VersionedIdentity
    evaluators: tuple[PluginSelection, ...]
    parameters: tuple[PluginParameters, ...]
    initial_keyframe_name: str
    initial_qpos_rad: tuple[float, ...]
    initial_tip_position_m: tuple[float, float, float]
    initial_tip_frame: str
    initial_tip_unit: str
    initial_tool_orientation_wxyz: tuple[float, float, float, float]
    initial_tool_orientation_frame: str
    initial_tool_orientation_unit: str
    initial_tool_orientation_order: str
    target_family: str
    target_identity: str
    target_world_position_m: tuple[float, float, float]
    initial_tip_to_target_distance_m: float
    target_tolerance_m: float
    dwell_interval_s: float
    timeout_s: float
    input_source_identity: str
    fixture_identity: str
    normalized_input_range: tuple[float, float]
    gain: float
    deadzone: float
    cadence_s: float
    maximum_per_step_delta_m: float
    requested_control_frame: str
    condition_id: str
    condition_order: int
    task_order: int
    deterministic_seed: int
    camera_identity: str
    visual_feedback_identity: str
    presentation_identity: str

    def __post_init__(self) -> None:
        if self.schema_version != EVALUATION_MANIFEST_SCHEMA_VERSION:
            raise EvaluationManifestError(
                f"unsupported evaluation manifest schema version: {self.schema_version!r}"
            )
        if (
            type(self.contract_version) is not int
            or self.contract_version != EVALUATION_MANIFEST_CONTRACT_VERSION
        ):
            raise EvaluationManifestError(
                f"unsupported evaluation manifest contract version: {self.contract_version!r}"
            )
        _stable_identity("repository_identity", self.repository_identity)
        _software_revision_identity(self.software_revision_identity)
        for name in (
            "robot_bundle",
            "environment",
            "control_mapping",
            "task",
            "input_source",
        ):
            _selection(name, getattr(self, name))
        _identity("robot_profile_identity", self.robot_profile_identity)
        _identity("runtime_plugin_identity", self.runtime_plugin_identity)
        _identity("model_contract_identity", self.model_contract_identity)
        _identity("initial_state_contract_identity", self.initial_state_contract_identity)
        _identity(
            "command_semantics_route_identity",
            self.command_semantics_route_identity,
        )

        evaluators = tuple(self.evaluators)
        if any(not isinstance(item, PluginSelection) for item in evaluators):
            raise EvaluationManifestError("evaluators must use PluginSelection")
        if len(evaluators) != len(set(evaluators)):
            raise EvaluationManifestError("duplicate evaluator selection")
        object.__setattr__(self, "evaluators", evaluators)

        parameters = tuple(self.parameters)
        if any(not isinstance(item, PluginParameters) for item in parameters):
            raise EvaluationManifestError("parameters must use PluginParameters")
        owners = tuple(item.owner for item in parameters)
        if len(owners) != len(set(owners)):
            raise EvaluationManifestError("duplicate plugin parameter owner")
        for item in parameters:
            _parameter_values(item)
        object.__setattr__(self, "parameters", parameters)

        object.__setattr__(
            self,
            "initial_qpos_rad",
            _vector("initial_qpos_rad", self.initial_qpos_rad),
        )
        object.__setattr__(
            self,
            "initial_tip_position_m",
            _vector("initial_tip_position_m", self.initial_tip_position_m, length=3),
        )
        object.__setattr__(
            self,
            "initial_tool_orientation_wxyz",
            _unit_quaternion(
                "initial_tool_orientation_wxyz", self.initial_tool_orientation_wxyz
            ),
        )
        for name in (
            "initial_keyframe_name",
            "initial_tip_frame",
            "initial_tip_unit",
            "initial_tool_orientation_frame",
            "initial_tool_orientation_unit",
            "initial_tool_orientation_order",
            "target_family",
            "target_identity",
            "input_source_identity",
            "fixture_identity",
            "condition_id",
            "camera_identity",
            "visual_feedback_identity",
            "presentation_identity",
        ):
            _identifier(name, getattr(self, name))
        if self.initial_tool_orientation_order != _QUATERNION_ORDER:
            raise EvaluationManifestError(
                f"initial_tool_orientation_order must be {_QUATERNION_ORDER!r}"
            )
        if self.initial_tool_orientation_unit != _QUATERNION_UNIT:
            raise EvaluationManifestError(
                f"initial_tool_orientation_unit must be {_QUATERNION_UNIT!r}"
            )
        object.__setattr__(
            self,
            "target_world_position_m",
            _vector("target_world_position_m", self.target_world_position_m, length=3),
        )
        target_distance = math.sqrt(
            sum(
                (self.initial_tip_position_m[index] - self.target_world_position_m[index])
                ** 2
                for index in range(3)
            )
        )
        declared_distance = _finite_float(
            "initial_tip_to_target_distance_m",
            self.initial_tip_to_target_distance_m,
            positive=True,
        )
        if not math.isclose(
            target_distance,
            declared_distance,
            rel_tol=0.0,
            abs_tol=_FLOAT_TOLERANCE,
        ):
            raise EvaluationManifestError(
                "initial-tip target distance identity mismatch: "
                f"declared={declared_distance!r}, calculated={target_distance!r}"
            )
        object.__setattr__(self, "initial_tip_to_target_distance_m", declared_distance)
        for name in (
            "target_tolerance_m",
            "dwell_interval_s",
            "timeout_s",
            "gain",
            "cadence_s",
            "maximum_per_step_delta_m",
        ):
            object.__setattr__(
                self,
                name,
                _finite_float(name, getattr(self, name), positive=True),
            )
        if self.target_tolerance_m >= declared_distance:
            raise EvaluationManifestError(
                "target tolerance must be smaller than initial-tip target distance"
            )
        if self.dwell_interval_s > self.timeout_s:
            raise EvaluationManifestError("dwell interval must not exceed timeout")
        if self.cadence_s > self.timeout_s:
            raise EvaluationManifestError("cadence must not exceed timeout")
        deadzone = _finite_float("deadzone", self.deadzone, non_negative=True)
        if deadzone > 1.0:
            raise EvaluationManifestError("deadzone must be within [0.0, 1.0]")
        object.__setattr__(self, "deadzone", deadzone)
        normalized_range = _vector(
            "normalized_input_range", self.normalized_input_range, length=2
        )
        if not (-1.0 <= normalized_range[0] < normalized_range[1] <= 1.0):
            raise EvaluationManifestError(
                "normalized_input_range must be an ordered interval within [-1.0, 1.0]"
            )
        object.__setattr__(self, "normalized_input_range", normalized_range)
        object.__setattr__(
            self,
            "requested_control_frame",
            _enum("requested_control_frame", self.requested_control_frame, _CONTROL_FRAMES),
        )
        object.__setattr__(
            self,
            "condition_order",
            _non_negative_int("condition_order", self.condition_order),
        )
        object.__setattr__(self, "task_order", _non_negative_int("task_order", self.task_order))
        object.__setattr__(
            self,
            "deterministic_seed",
            _non_negative_int("deterministic_seed", self.deterministic_seed),
        )

    @property
    def manifest_schema_version(self) -> str:
        return self.schema_version

    @property
    def manifest_contract_version(self) -> int:
        return self.contract_version

    @property
    def repository(self) -> str:
        return self.repository_identity

    @property
    def software_revision(self) -> str:
        return self.software_revision_identity

    @property
    def target_id(self) -> str:
        return self.target_identity

    @property
    def input_source_name(self) -> str:
        return self.input_source_identity

    @property
    def gain_m_per_s(self) -> float:
        return self.gain

    @property
    def cadence(self) -> float:
        return self.cadence_s

    @property
    def maximum_per_step_delta(self) -> float:
        return self.maximum_per_step_delta_m

    @property
    def plugin_manifest(self) -> ExperimentPluginManifest:
        return ExperimentPluginManifest(
            robot_bundle=self.robot_bundle,
            environment=self.environment,
            control_mapping=self.control_mapping,
            task=self.task,
            input_source=self.input_source,
            command_semantics_route=self.command_semantics_route_identity,
            evaluators=self.evaluators,
            parameters=self.parameters,
        )

    def to_document(self) -> dict[str, object]:
        """Return a detached JSON-compatible document."""

        return {
            "schema_version": self.schema_version,
            "contract_version": self.contract_version,
            "repository_identity": self.repository_identity,
            "software_revision_identity": self.software_revision_identity,
            "robot_bundle": _selection_document(self.robot_bundle),
            "robot_profile_identity": _identity_document(self.robot_profile_identity),
            "runtime_plugin_identity": _identity_document(self.runtime_plugin_identity),
            "model_contract_identity": _identity_document(self.model_contract_identity),
            "initial_state_contract_identity": _identity_document(
                self.initial_state_contract_identity
            ),
            "environment": _selection_document(self.environment),
            "control_mapping": _selection_document(self.control_mapping),
            "task": _selection_document(self.task),
            "input_source": _selection_document(self.input_source),
            "command_semantics_route_identity": _identity_document(
                self.command_semantics_route_identity
            ),
            "evaluators": [_selection_document(item) for item in self.evaluators],
            "parameters": [
                {
                    "owner": _parameter_owner_document(item.owner),
                    "values": {
                        key: parameter_value_to_document(value)
                        for key, value in sorted(item.values.items(), key=lambda pair: pair[0])
                    },
                }
                for item in sorted(self.parameters, key=lambda item: item.owner.canonical_id)
            ],
            "initial_keyframe_name": self.initial_keyframe_name,
            "initial_qpos_rad": list(self.initial_qpos_rad),
            "initial_tip_position_m": list(self.initial_tip_position_m),
            "initial_tip_frame": self.initial_tip_frame,
            "initial_tip_unit": self.initial_tip_unit,
            "initial_tool_orientation_wxyz": list(self.initial_tool_orientation_wxyz),
            "initial_tool_orientation_frame": self.initial_tool_orientation_frame,
            "initial_tool_orientation_unit": self.initial_tool_orientation_unit,
            "initial_tool_orientation_order": self.initial_tool_orientation_order,
            "target_family": self.target_family,
            "target_identity": self.target_identity,
            "target_world_position_m": list(self.target_world_position_m),
            "initial_tip_to_target_distance_m": self.initial_tip_to_target_distance_m,
            "target_tolerance_m": self.target_tolerance_m,
            "dwell_interval_s": self.dwell_interval_s,
            "timeout_s": self.timeout_s,
            "input_source_identity": self.input_source_identity,
            "fixture_identity": self.fixture_identity,
            "normalized_input_range": list(self.normalized_input_range),
            "gain": self.gain,
            "deadzone": self.deadzone,
            "cadence_s": self.cadence_s,
            "maximum_per_step_delta_m": self.maximum_per_step_delta_m,
            "requested_control_frame": self.requested_control_frame,
            "condition_id": self.condition_id,
            "condition_order": self.condition_order,
            "task_order": self.task_order,
            "deterministic_seed": self.deterministic_seed,
            "camera_identity": self.camera_identity,
            "visual_feedback_identity": self.visual_feedback_identity,
            "presentation_identity": self.presentation_identity,
        }


_MANIFEST_FIELDS = frozenset(EvaluationManifest.__dataclass_fields__)
_SELECTION_FIELDS = frozenset({"plugin_id", "contract_version"})
_IDENTITY_FIELDS = frozenset({"name", "version"})
_PARAMETER_FIELDS = frozenset({"owner", "values"})
_OWNER_FIELDS = frozenset({"axis", "selection"})


def decode_evaluation_manifest(
    document: bytes | str | Mapping[str, object],
) -> EvaluationManifest:
    """Strictly decode a canonical JSON document into an immutable manifest."""

    root = _require_fields(_parse_json_document(document), "manifest", _MANIFEST_FIELDS)
    parameter_documents = root["parameters"]
    if not isinstance(parameter_documents, list):
        raise EvaluationManifestDecodeError("parameters must be an array")
    parameters: list[PluginParameters] = []
    for index, item in enumerate(parameter_documents):
        parameter = _require_fields(item, f"parameters[{index}]", _PARAMETER_FIELDS)
        owner_document = _require_fields(
            parameter["owner"], f"parameters[{index}].owner", _OWNER_FIELDS
        )
        owner = PluginParameterOwner(
            axis=_document_axis(
                owner_document, f"parameters[{index}].owner.axis"
            ),
            selection=_document_selection(
                _require_object(owner_document["selection"], "parameter owner selection"),
                f"parameters[{index}].owner.selection",
            ),
        )
        parameters.append(
            PluginParameters(
                owner=owner,
                values=_document_parameter_values(
                    parameter["values"], f"parameters[{index}].values"
                ),
            )
        )
    evaluator_documents = root["evaluators"]
    if not isinstance(evaluator_documents, list):
        raise EvaluationManifestDecodeError("evaluators must be an array")
    evaluators = tuple(
        _document_selection(
            _require_object(item, f"evaluators[{index}]"),
            f"evaluators[{index}]",
        )
        for index, item in enumerate(evaluator_documents)
    )
    return EvaluationManifest(
        schema_version=_document_string(root, "schema_version"),
        contract_version=_positive_int("contract_version", root["contract_version"]),
        repository_identity=_document_string(root, "repository_identity"),
        software_revision_identity=_document_string(
            root, "software_revision_identity"
        ),
        robot_bundle=_document_selection(
            _require_object(root["robot_bundle"], "robot_bundle"), "robot_bundle"
        ),
        robot_profile_identity=_document_identity(
            _require_object(root["robot_profile_identity"], "robot_profile_identity"),
            "robot_profile_identity",
        ),
        runtime_plugin_identity=_document_identity(
            _require_object(root["runtime_plugin_identity"], "runtime_plugin_identity"),
            "runtime_plugin_identity",
        ),
        model_contract_identity=_document_identity(
            _require_object(root["model_contract_identity"], "model_contract_identity"),
            "model_contract_identity",
        ),
        initial_state_contract_identity=_document_identity(
            _require_object(
                root["initial_state_contract_identity"],
                "initial_state_contract_identity",
            ),
            "initial_state_contract_identity",
        ),
        environment=_document_selection(
            _require_object(root["environment"], "environment"), "environment"
        ),
        control_mapping=_document_selection(
            _require_object(root["control_mapping"], "control_mapping"),
            "control_mapping",
        ),
        task=_document_selection(
            _require_object(root["task"], "task"), "task"
        ),
        input_source=_document_selection(
            _require_object(root["input_source"], "input_source"), "input_source"
        ),
        command_semantics_route_identity=_document_identity(
            _require_object(
                root["command_semantics_route_identity"],
                "command_semantics_route_identity",
            ),
            "command_semantics_route_identity",
        ),
        evaluators=evaluators,
        parameters=tuple(parameters),
        initial_keyframe_name=_document_string(root, "initial_keyframe_name"),
        initial_qpos_rad=_document_vector(root, "initial_qpos_rad"),
        initial_tip_position_m=_document_vector(
            root, "initial_tip_position_m", length=3
        ),  # type: ignore[arg-type]
        initial_tip_frame=_document_string(root, "initial_tip_frame"),
        initial_tip_unit=_document_string(root, "initial_tip_unit"),
        initial_tool_orientation_wxyz=_document_vector(
            root, "initial_tool_orientation_wxyz", length=4
        ),  # type: ignore[arg-type]
        initial_tool_orientation_frame=_document_string(
            root, "initial_tool_orientation_frame"
        ),
        initial_tool_orientation_unit=_document_string(
            root, "initial_tool_orientation_unit"
        ),
        initial_tool_orientation_order=_document_string(
            root, "initial_tool_orientation_order"
        ),
        target_family=_document_string(root, "target_family"),
        target_identity=_document_string(root, "target_identity"),
        target_world_position_m=_document_vector(
            root, "target_world_position_m", length=3
        ),  # type: ignore[arg-type]
        initial_tip_to_target_distance_m=_finite_float(
            "initial_tip_to_target_distance_m", root["initial_tip_to_target_distance_m"]
        ),
        target_tolerance_m=_finite_float("target_tolerance_m", root["target_tolerance_m"]),
        dwell_interval_s=_finite_float("dwell_interval_s", root["dwell_interval_s"]),
        timeout_s=_finite_float("timeout_s", root["timeout_s"]),
        input_source_identity=_document_string(root, "input_source_identity"),
        fixture_identity=_document_string(root, "fixture_identity"),
        normalized_input_range=_document_vector(
            root, "normalized_input_range", length=2
        ),  # type: ignore[arg-type]
        gain=_finite_float("gain", root["gain"]),
        deadzone=_finite_float("deadzone", root["deadzone"]),
        cadence_s=_finite_float("cadence_s", root["cadence_s"]),
        maximum_per_step_delta_m=_finite_float(
            "maximum_per_step_delta_m", root["maximum_per_step_delta_m"]
        ),
        requested_control_frame=_document_string(root, "requested_control_frame"),
        condition_id=_document_string(root, "condition_id"),
        condition_order=_non_negative_int("condition_order", root["condition_order"]),
        task_order=_non_negative_int("task_order", root["task_order"]),
        deterministic_seed=_non_negative_int(
            "deterministic_seed", root["deterministic_seed"]
        ),
        camera_identity=_document_string(root, "camera_identity"),
        visual_feedback_identity=_document_string(
            root, "visual_feedback_identity"
        ),
        presentation_identity=_document_string(root, "presentation_identity"),
    )


def encode_evaluation_manifest(manifest: EvaluationManifest) -> bytes:
    """Encode a typed manifest to canonical UTF-8 JSON bytes."""

    if not isinstance(manifest, EvaluationManifest):
        raise TypeError("encode_evaluation_manifest requires EvaluationManifest")
    return _canonical_json_bytes(manifest.to_document())


def evaluation_manifest_digest(manifest: EvaluationManifest) -> str:
    return _digest(encode_evaluation_manifest(manifest))


def _model_contract_identity(value: object) -> VersionedIdentity:
    text = _identifier("model_contract_version", value)
    name, separator, version_text = text.rpartition("/v")
    if not separator or not name or not version_text.isdigit():
        raise EvaluationManifestError(
            f"model_contract_version is not a canonical versioned identity: {text!r}"
        )
    return VersionedIdentity(name, int(version_text))


def _parameter_item_for_owner(
    manifest: EvaluationManifest,
    axis: PluginAxis,
) -> PluginParameters | None:
    expected = PluginParameterOwner(axis, getattr(manifest, axis.value))
    return next((item for item in manifest.parameters if item.owner == expected), None)


def _initial_state_contract_document(contract: InitialStateContract) -> dict[str, object]:
    return {
        "identity": _identity_document(contract.identity),
        "source_kind": contract.source_kind,
        "source_id": contract.source_id,
        "qpos_rad": list(contract.qpos_rad),
        "tip_position_m": list(contract.tip_position_m),
        "tool_orientation_wxyz": list(contract.tool_orientation_wxyz),
        "frame": contract.frame,
        "position_unit": contract.position_unit,
        "orientation_unit": contract.orientation_unit,
        "quaternion_order": contract.quaternion_order,
    }


def _initial_state_verification_identity(
    contract: InitialStateContract,
) -> str:
    return _digest(
        _canonical_json_bytes(_initial_state_contract_document(contract))
    )


def _require_initial_state_values_match(
    name: str,
    declared: Sequence[float],
    canonical: Sequence[float],
) -> None:
    if len(declared) != len(canonical) or any(
        not math.isclose(left, right, rel_tol=0.0, abs_tol=_FLOAT_TOLERANCE)
        for left, right in zip(declared, canonical, strict=False)
    ):
        raise EvaluationReadinessError(
            f"{name} mismatch with resolved canonical initial-state contract"
        )


def _validate_initial_state(
    manifest: EvaluationManifest,
    composition: ResolvedExperimentComposition,
) -> tuple[str, VersionedIdentity]:
    profile = composition.robot_bundle.profile
    if manifest.robot_profile_identity != VersionedIdentity(
        profile.profile_id, profile.profile_contract_version
    ):
        raise EvaluationReadinessError(
            "robot profile identity mismatch between manifest and resolved Robot Bundle"
        )
    runtime_plugin = composition.robot_bundle.runtime_plugin
    actual_runtime_identity = VersionedIdentity(
        runtime_plugin.profile_id, profile.profile_contract_version
    )
    if manifest.runtime_plugin_identity != actual_runtime_identity:
        raise EvaluationReadinessError(
            "runtime plugin identity mismatch between manifest and resolved Robot Bundle"
        )
    if manifest.model_contract_identity != _model_contract_identity(
        profile.model_contract_version
    ):
        raise EvaluationReadinessError(
            "model contract identity mismatch between manifest and Robot Profile"
        )
    if manifest.initial_keyframe_name != profile.initial_keyframe_name:
        raise EvaluationReadinessError(
            "initial keyframe mismatch between manifest and Robot Profile"
        )
    provider = composition.robot_bundle.provider(RESET_INITIAL_STATE_V1)
    if not isinstance(provider, ResetInitialStateProvider):
        raise EvaluationReadinessError("reset initial-state capability has an invalid provider")
    reference = provider.resolve_initial_state()
    if not isinstance(reference, InitialStateReference):
        raise EvaluationReadinessError("reset initial-state provider returned an invalid reference")
    if reference.source_kind != "named_keyframe":
        raise EvaluationReadinessError(
            "evaluation readiness requires a named-keyframe neutral initial state"
        )
    if reference.source_id != manifest.initial_keyframe_name:
        raise EvaluationReadinessError(
            "initial keyframe mismatch between manifest and resolved provider"
        )
    if not isinstance(provider, InitialStateContractProvider):
        raise EvaluationReadinessError(
            "reset initial-state provider has no canonical initial-state contract"
        )
    contract = provider.initial_state_contract()
    if not isinstance(contract, InitialStateContract):
        raise EvaluationReadinessError(
            "reset initial-state provider returned an invalid canonical contract"
        )
    if manifest.initial_state_contract_identity != contract.identity:
        raise EvaluationReadinessError(
            "initial-state contract identity mismatch between manifest and resolved provider"
        )
    if contract.source_kind != reference.source_kind or contract.source_id != reference.source_id:
        raise EvaluationReadinessError(
            "initial-state contract source mismatch with resolved provider"
        )
    if contract.source_kind != "named_keyframe":
        raise EvaluationReadinessError(
            "evaluation readiness requires a named-keyframe canonical initial state"
        )
    if len(contract.qpos_rad) != profile.qpos_dimension:
        raise EvaluationReadinessError(
            "canonical initial-state qpos dimension mismatch: "
            f"expected {profile.qpos_dimension}, got {len(contract.qpos_rad)}"
        )
    if len(manifest.initial_qpos_rad) != profile.qpos_dimension:
        raise EvaluationReadinessError(
            "initial qpos dimension mismatch: "
            f"expected {profile.qpos_dimension}, got {len(manifest.initial_qpos_rad)}"
        )
    _require_initial_state_values_match(
        "initial qpos", manifest.initial_qpos_rad, contract.qpos_rad
    )
    _require_initial_state_values_match(
        "initial tip position", manifest.initial_tip_position_m, contract.tip_position_m
    )
    _require_initial_state_values_match(
        "initial tool orientation",
        manifest.initial_tool_orientation_wxyz,
        contract.tool_orientation_wxyz,
    )
    if manifest.initial_tip_frame != contract.frame:
        raise EvaluationReadinessError("initial tip coordinate frame mismatch")
    if manifest.initial_tool_orientation_frame != contract.frame:
        raise EvaluationReadinessError("initial tool orientation coordinate frame mismatch")
    if manifest.initial_tip_unit != contract.position_unit:
        raise EvaluationReadinessError("initial tip position unit mismatch")
    if manifest.initial_tool_orientation_unit != contract.orientation_unit:
        raise EvaluationReadinessError("initial tool orientation unit mismatch")
    if manifest.initial_tool_orientation_order != contract.quaternion_order:
        raise EvaluationReadinessError("initial tool orientation quaternion order mismatch")
    if contract.frame != profile.coordinate_units.coordinate_frame:
        raise EvaluationReadinessError("canonical initial-state frame/profile mismatch")
    if contract.position_unit != profile.coordinate_units.position_unit:
        raise EvaluationReadinessError("canonical initial-state unit/profile mismatch")
    if contract.quaternion_order != profile.coordinate_units.quaternion_order:
        raise EvaluationReadinessError(
            "canonical initial-state quaternion order/profile mismatch"
        )
    return _initial_state_verification_identity(contract), contract.identity


def _validate_control_mapping(
    manifest: EvaluationManifest,
    composition: ResolvedExperimentComposition,
) -> tuple[VersionedIdentity, VersionedIdentity]:
    parameter_item = _parameter_item_for_owner(
        manifest, PluginAxis.CONTROL_MAPPING
    )
    parameters = {} if parameter_item is None else parameter_item.values
    declared_frame = composition.control_mapping.resolve_control_frame(parameters)
    if declared_frame is None:
        raise EvaluationReadinessError(
            "mapping plugin must declare control_frame for evaluation readiness"
        )
    if declared_frame != manifest.requested_control_frame:
        raise EvaluationReadinessError(
            "mapping plugin/requested control frame mismatch: "
            f"mapping={declared_frame!r}, requested={manifest.requested_control_frame!r}"
        )
    family_identity = composition.control_mapping.comparison_family_identity
    if family_identity is None:
        raise EvaluationReadinessError(
            "mapping plugin must declare comparison family identity for evaluation readiness"
        )
    semantics_identity = composition.control_mapping.mapping_semantics_identity
    if semantics_identity is None:
        raise EvaluationReadinessError(
            "mapping plugin must declare mapping semantics identity for evaluation readiness"
        )
    _identity("mapping comparison family identity", family_identity)
    _identity("mapping semantics identity", semantics_identity)
    strategy_identity = getattr(
        composition.control_mapping.strategy, "mapping_semantics_identity", None
    )
    if strategy_identity != semantics_identity:
        raise EvaluationReadinessError(
            "mapping strategy semantic identity mismatch"
        )
    return family_identity, semantics_identity


def _resolved_identity_document(
    manifest: EvaluationManifest,
    composition: ResolvedExperimentComposition,
    *,
    manifest_digest: str,
    resolved_capabilities: Sequence[VersionedIdentity],
    role_descriptors: Sequence[EnvironmentRole],
    evidence_producers: Sequence[EvidenceProducerBinding],
    robot_profile_identity: VersionedIdentity,
    runtime_plugin_identity: VersionedIdentity,
    model_contract_identity: VersionedIdentity,
    initial_state_identity: str,
    initial_state_contract_identity: VersionedIdentity,
    execution_identity: SoftwareExecutionIdentity,
    mapping_family_identity: VersionedIdentity,
    mapping_semantics_identity: VersionedIdentity,
) -> dict[str, object]:
    return {
        "freeze_schema_version": EVALUATION_FREEZE_SCHEMA_VERSION,
        "requested_manifest_identity": manifest_digest,
        "requested_plugin_selections": {
            "robot_bundle": _selection_document(manifest.robot_bundle),
            "environment": _selection_document(manifest.environment),
            "control_mapping": _selection_document(manifest.control_mapping),
            "task": _selection_document(manifest.task),
            "input_source": _selection_document(manifest.input_source),
            "evaluators": [_selection_document(item) for item in manifest.evaluators],
        },
        "requested_command_semantics_route_identity": _identity_document(
            manifest.command_semantics_route_identity
        ),
        "resolved_plugin_identities": {
            "robot_bundle": _identity_document(composition.robot_bundle.identity),
            "environment": _identity_document(composition.environment.identity),
            "control_mapping": _identity_document(composition.control_mapping.identity),
            "task": _identity_document(composition.task.identity),
            "input_source": _identity_document(composition.input_source.identity),
            "evaluators": [
                _identity_document(item.identity) for item in composition.evaluators
            ],
        },
        "resolved_input_sample_schema": _identity_document(
            composition.resolved_input_sample_schema
        ),
        "resolved_control_frame": composition.control_mapping.control_frame,
        "resolved_mapping_comparison": {
            "family_identity": _identity_document(mapping_family_identity),
            "mapping_semantics_identity": _identity_document(mapping_semantics_identity),
            "control_frame": composition.control_mapping.control_frame,
        },
        "resolved_command_semantics_route": {
            "route_identity": _identity_document(
                composition.resolved_command_semantics_route.identity
            ),
            "control_semantics_identity": _identity_document(
                composition.resolved_command_semantics_route.control_semantics_identity
            ),
            "robot_command_semantics_identity": _identity_document(
                composition.resolved_command_semantics_route.robot_command_semantics_identity
            ),
        },
        "software_execution_identity": {
            "repository_identity": execution_identity.repository_identity,
            "software_revision_identity": execution_identity.software_revision_identity,
        },
        "resolved_compatibility_identity": {
            "robot_bundle": _identity_document(composition.robot_bundle.identity),
            "environment": _identity_document(composition.environment.identity),
            "task": _identity_document(composition.task.identity),
            "backend_kind": composition.robot_bundle.profile.backend_kind,
        },
        "resolved_capability_identities": [
            _identity_document(item) for item in sorted(resolved_capabilities)
        ],
        "resolved_semantic_role_descriptors": [
            {
                "role": descriptor.role.name,
                "object_kind": descriptor.object_kind,
                "frame": descriptor.frame,
                "unit": descriptor.unit,
            }
            for descriptor in sorted(
                role_descriptors,
                key=lambda item: (
                    item.role.name,
                    item.object_kind,
                    item.frame,
                    item.unit,
                ),
            )
        ],
        "evidence_producers": [
            {
                "producer_axis": binding.producer_axis.value,
                "producer_identity": _identity_document(binding.producer_identity),
                "evidence_identity": _identity_document(binding.evidence_identity),
            }
            for binding in sorted(
                evidence_producers,
                key=lambda item: (
                    item.evidence_identity.name,
                    item.evidence_identity.version,
                    item.producer_axis.value,
                    item.producer_identity.name,
                    item.producer_identity.version,
                ),
            )
        ],
        "robot_profile_identity": _identity_document(robot_profile_identity),
        "runtime_plugin_identity": _identity_document(runtime_plugin_identity),
        "model_contract_identity": _identity_document(model_contract_identity),
        "initial_state_contract_identity": _identity_document(
            initial_state_contract_identity
        ),
        "initial_state_verification_identity": initial_state_identity,
    }


@dataclass(frozen=True, slots=True)
class FreezeRecord:
    """Detached canonical material for one manifest/readiness freeze."""

    manifest_digest: str
    resolved_identity_digest: str
    frozen_digest: str
    canonical_manifest_bytes: bytes
    canonical_resolved_identity_bytes: bytes

    def __post_init__(self) -> None:
        _digest_value("manifest_digest", self.manifest_digest)
        _digest_value("resolved_identity_digest", self.resolved_identity_digest)
        _digest_value("frozen_digest", self.frozen_digest)
        if type(self.canonical_manifest_bytes) is not bytes:
            raise EvaluationManifestError("freeze record manifest material must use bytes")
        if type(self.canonical_resolved_identity_bytes) is not bytes:
            raise EvaluationManifestError("freeze record resolved material must use bytes")
        if not self.canonical_manifest_bytes or not self.canonical_resolved_identity_bytes:
            raise EvaluationManifestError("freeze record canonical material must not be empty")
        if _digest(self.canonical_manifest_bytes) != self.manifest_digest:
            raise EvaluationManifestError("freeze record manifest digest mismatch")
        if _digest(self.canonical_resolved_identity_bytes) != self.resolved_identity_digest:
            raise EvaluationManifestError("freeze record resolved digest mismatch")
        expected = _digest(
            EVALUATION_FREEZE_SCHEMA_VERSION.encode("ascii")
            + b"\0"
            + self.canonical_manifest_bytes
            + b"\0"
            + self.canonical_resolved_identity_bytes
        )
        if expected != self.frozen_digest:
            raise EvaluationManifestError("freeze record frozen digest mismatch")

    @property
    def identity(self) -> str:
        return self.frozen_digest


@dataclass(frozen=True, slots=True)
class EvaluationReadiness:
    """runnerがside effect開始前に受け取る解決済みidentityとTask binding。"""

    manifest: EvaluationManifest
    composition: ResolvedExperimentComposition
    manifest_digest: str
    resolved_capability_identities: tuple[VersionedIdentity, ...]
    resolved_semantic_role_descriptors: tuple[EnvironmentRole, ...]
    evidence_producers: tuple[EvidenceProducerBinding, ...]
    robot_profile_identity: VersionedIdentity
    runtime_plugin_identity: VersionedIdentity
    model_contract_identity: VersionedIdentity
    initial_state_contract_identity: VersionedIdentity
    initial_state_verification_identity: str
    software_execution_identity: SoftwareExecutionIdentity
    mapping_comparison_family_identity: VersionedIdentity
    mapping_semantics_identity: VersionedIdentity
    command_semantics_route: CommandSemanticsRoute
    task_execution_binding: TaskExecutionBinding
    readiness_status: ReadinessStatus
    resolved_identity_digest: str
    freeze_record: FreezeRecord

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, EvaluationManifest):
            raise EvaluationManifestError("readiness manifest must use EvaluationManifest")
        if not isinstance(self.composition, ResolvedExperimentComposition):
            raise EvaluationManifestError(
                "readiness composition must use ResolvedExperimentComposition"
            )
        if self.readiness_status is not ReadinessStatus.READY:
            raise EvaluationManifestError("readiness status must be ready")
        _digest_value("manifest_digest", self.manifest_digest)
        _digest_value("resolved_identity_digest", self.resolved_identity_digest)
        if not isinstance(self.freeze_record, FreezeRecord):
            raise EvaluationManifestError("readiness freeze_record must use FreezeRecord")
        if self.freeze_record.manifest_digest != self.manifest_digest:
            raise EvaluationManifestError("readiness manifest digest mismatch")
        if self.freeze_record.resolved_identity_digest != self.resolved_identity_digest:
            raise EvaluationManifestError("readiness resolved digest mismatch")
        capabilities = tuple(self.resolved_capability_identities)
        roles = tuple(self.resolved_semantic_role_descriptors)
        evidence = tuple(self.evidence_producers)
        if any(not isinstance(item, VersionedIdentity) for item in capabilities):
            raise EvaluationManifestError("readiness capabilities must use VersionedIdentity")
        if any(not isinstance(item, EnvironmentRole) for item in roles):
            raise EvaluationManifestError("readiness roles must use EnvironmentRole")
        if any(not isinstance(item, EvidenceProducerBinding) for item in evidence):
            raise EvaluationManifestError(
                "readiness evidence producers must use EvidenceProducerBinding"
            )
        if len(capabilities) != len(set(capabilities)):
            raise EvaluationManifestError("readiness capabilities must be unique")
        if len(evidence) != len({item.evidence_identity for item in evidence}):
            raise EvaluationManifestError("readiness evidence producers must be unique")
        object.__setattr__(self, "resolved_capability_identities", capabilities)
        object.__setattr__(self, "resolved_semantic_role_descriptors", roles)
        object.__setattr__(self, "evidence_producers", evidence)
        object.__setattr__(
            self,
            "initial_state_verification_identity",
            _digest_value(
                "initial_state_verification_identity",
                self.initial_state_verification_identity,
            ),
        )
        if self.robot_profile_identity is None:
            raise EvaluationManifestError("readiness robot profile identity is required")
        _identity("readiness robot profile identity", self.robot_profile_identity)
        _identity("readiness runtime plugin identity", self.runtime_plugin_identity)
        _identity("readiness model contract identity", self.model_contract_identity)
        _identity(
            "readiness initial-state contract identity",
            self.initial_state_contract_identity,
        )
        if not isinstance(self.software_execution_identity, SoftwareExecutionIdentity):
            raise EvaluationManifestError(
                "readiness software execution identity must use SoftwareExecutionIdentity"
            )
        _identity(
            "readiness mapping comparison family identity",
            self.mapping_comparison_family_identity,
        )
        _identity(
            "readiness mapping semantics identity",
            self.mapping_semantics_identity,
        )
        if not isinstance(self.command_semantics_route, CommandSemanticsRoute):
            raise EvaluationManifestError(
                "readiness command semantics must use CommandSemanticsRoute"
            )
        if (
            self.command_semantics_route
            != self.composition.resolved_command_semantics_route
        ):
            raise EvaluationManifestError(
                "readiness command semantics/composition mismatch"
            )
        if not isinstance(self.task_execution_binding, TaskExecutionBinding):
            raise EvaluationManifestError(
                "readiness task binding must use TaskExecutionBinding"
            )

    @property
    def canonical_requested_manifest_identity(self) -> str:
        return self.manifest_digest

    @property
    def frozen_digest(self) -> str:
        return self.freeze_record.frozen_digest

    @property
    def freeze_identity(self) -> str:
        return self.freeze_record.frozen_digest

    @property
    def resolved_identity(self) -> str:
        return self.resolved_identity_digest


def comparison_parameters_for_readiness(
    readiness: EvaluationReadiness,
) -> tuple[tuple[str, object], ...]:
    """configuration recordへ保存するcanonical comparison projection。"""

    if not isinstance(readiness, EvaluationReadiness):
        raise TypeError("comparison parameter projection requires EvaluationReadiness")
    manifest = readiness.manifest
    return (
        ("cadence_s", manifest.cadence_s),
        ("camera_identity", manifest.camera_identity),
        ("condition_id", manifest.condition_id),
        ("condition_order", manifest.condition_order),
        ("deterministic_seed", manifest.deterministic_seed),
        ("fixture_identity", manifest.fixture_identity),
        ("input_source_identity", manifest.input_source_identity),
        ("manifest_digest", readiness.manifest_digest),
        ("normalized_input_max", manifest.normalized_input_range[1]),
        ("normalized_input_min", manifest.normalized_input_range[0]),
        ("presentation_identity", manifest.presentation_identity),
        ("requested_control_frame", manifest.requested_control_frame),
        ("resolved_identity_digest", readiness.resolved_identity_digest),
        ("task_order", manifest.task_order),
        ("visual_feedback_identity", manifest.visual_feedback_identity),
    )


def _readiness_from_composition(
    manifest: EvaluationManifest,
    composition: ResolvedExperimentComposition,
    execution_identity: SoftwareExecutionIdentity,
) -> EvaluationReadiness:
    try:
        if not isinstance(execution_identity, SoftwareExecutionIdentity):
            raise EvaluationReadinessError(
                "readiness requires SoftwareExecutionIdentity for actual execution"
            )
        expected_execution_identity = SoftwareExecutionIdentity(
            manifest.repository_identity,
            manifest.software_revision_identity,
        )
        if execution_identity != expected_execution_identity:
            raise EvaluationReadinessError(
                "manifest software identity does not match actual execution identity"
            )
        if composition.manifest != manifest.plugin_manifest:
            raise EvaluationReadinessError(
                "resolved composition manifest does not match requested manifest"
            )
        mapping_family_identity, mapping_semantics_identity = _validate_control_mapping(
            manifest, composition
        )
        initial_state_identity, initial_state_contract_identity = _validate_initial_state(
            manifest, composition
        )
        if len(composition.evidence_producers) != len(
            {item.evidence_identity for item in composition.evidence_producers}
        ):
            raise EvaluationReadinessError("evidence producer identity is not unique")
        manifest_bytes = encode_evaluation_manifest(manifest)
        manifest_identity = _digest(manifest_bytes)
        resolved_capabilities = tuple(sorted(composition.resolved_capabilities))
        role_descriptors = tuple(
            sorted(
                composition.resolved_role_descriptors,
                key=lambda item: (
                    item.role.name,
                    item.object_kind,
                    item.frame,
                    item.unit,
                ),
            )
        )
        evidence_producers = tuple(
            sorted(
                composition.evidence_producers,
                key=lambda item: (
                    item.evidence_identity.name,
                    item.evidence_identity.version,
                    item.producer_axis.value,
                    item.producer_identity.name,
                    item.producer_identity.version,
                ),
            )
        )
        profile_identity = VersionedIdentity(
            composition.robot_bundle.profile.profile_id,
            composition.robot_bundle.profile.profile_contract_version,
        )
        runtime_identity = VersionedIdentity(
            composition.robot_bundle.runtime_plugin.profile_id,
            composition.robot_bundle.profile.profile_contract_version,
        )
        model_identity = _model_contract_identity(
            composition.robot_bundle.profile.model_contract_version
        )
        resolved_document = _resolved_identity_document(
            manifest,
            composition,
            manifest_digest=manifest_identity,
            resolved_capabilities=resolved_capabilities,
            role_descriptors=role_descriptors,
            evidence_producers=evidence_producers,
            robot_profile_identity=profile_identity,
            runtime_plugin_identity=runtime_identity,
            model_contract_identity=model_identity,
            initial_state_identity=initial_state_identity,
            initial_state_contract_identity=initial_state_contract_identity,
            execution_identity=execution_identity,
            mapping_family_identity=mapping_family_identity,
            mapping_semantics_identity=mapping_semantics_identity,
        )
        resolved_bytes = _canonical_json_bytes(resolved_document)
        resolved_identity = _digest(resolved_bytes)
        frozen_bytes = (
            EVALUATION_FREEZE_SCHEMA_VERSION.encode("ascii")
            + b"\0"
            + manifest_bytes
            + b"\0"
            + resolved_bytes
        )
        freeze_record = FreezeRecord(
            manifest_digest=manifest_identity,
            resolved_identity_digest=resolved_identity,
            frozen_digest=_digest(frozen_bytes),
            canonical_manifest_bytes=manifest_bytes,
            canonical_resolved_identity_bytes=resolved_bytes,
        )
        task_parameter_item = _parameter_item_for_owner(manifest, PluginAxis.TASK)
        task_parameters = (
            {} if task_parameter_item is None else task_parameter_item.values
        )
        task_context = EndpointReachTaskContext(
            initial_position_world_m=manifest.initial_tip_position_m,
            target_position_world_m=manifest.target_world_position_m,
            target_tolerance_m=manifest.target_tolerance_m,
            dwell_interval_s=manifest.dwell_interval_s,
            timeout_s=manifest.timeout_s,
        )
        task_binding = composition.task.bind_context(task_context, task_parameters)
        return EvaluationReadiness(
            manifest=manifest,
            composition=composition,
            manifest_digest=manifest_identity,
            resolved_capability_identities=resolved_capabilities,
            resolved_semantic_role_descriptors=role_descriptors,
            evidence_producers=evidence_producers,
            robot_profile_identity=profile_identity,
            runtime_plugin_identity=runtime_identity,
            model_contract_identity=model_identity,
            initial_state_contract_identity=initial_state_contract_identity,
            initial_state_verification_identity=initial_state_identity,
            software_execution_identity=execution_identity,
            mapping_comparison_family_identity=mapping_family_identity,
            mapping_semantics_identity=mapping_semantics_identity,
            command_semantics_route=composition.resolved_command_semantics_route,
            task_execution_binding=task_binding,
            readiness_status=ReadinessStatus.READY,
            resolved_identity_digest=resolved_identity,
            freeze_record=freeze_record,
        )
    except EvaluationReadinessError:
        raise
    except (TypeError, ValueError) as exc:
        raise EvaluationReadinessError(str(exc)) from exc


def build_evaluation_readiness(
    manifest: EvaluationManifest,
    registries: ExperimentPluginRegistries,
    *,
    execution_identity: SoftwareExecutionIdentity,
) -> EvaluationReadiness:
    """runner開始前に1 conditionの6軸とTask contextを検証する。"""

    if not isinstance(manifest, EvaluationManifest):
        raise TypeError("build_evaluation_readiness requires EvaluationManifest")
    try:
        composition = compose_experiment(manifest.plugin_manifest, registries)
    except (TypeError, ValueError) as exc:
        raise EvaluationReadinessError(str(exc)) from exc
    return _readiness_from_composition(manifest, composition, execution_identity)


def verify_freeze_identity(
    record: FreezeRecord,
    manifest: EvaluationManifest,
    readiness: EvaluationReadiness,
) -> None:
    """Raise when requested or resolved identity differs from the freeze."""

    if not isinstance(record, FreezeRecord):
        raise TypeError("record must use FreezeRecord")
    if not isinstance(manifest, EvaluationManifest):
        raise TypeError("manifest must use EvaluationManifest")
    if not isinstance(readiness, EvaluationReadiness):
        raise TypeError("readiness must use EvaluationReadiness")
    current_manifest_bytes = encode_evaluation_manifest(manifest)
    if _digest(current_manifest_bytes) != record.manifest_digest:
        raise EvaluationReadinessError("frozen manifest value changed after readiness")
    current_resolved_document = _resolved_identity_document(
        manifest,
        readiness.composition,
        manifest_digest=readiness.manifest_digest,
        resolved_capabilities=readiness.resolved_capability_identities,
        role_descriptors=readiness.resolved_semantic_role_descriptors,
        evidence_producers=readiness.evidence_producers,
        robot_profile_identity=readiness.robot_profile_identity,
        runtime_plugin_identity=readiness.runtime_plugin_identity,
        model_contract_identity=readiness.model_contract_identity,
        initial_state_identity=readiness.initial_state_verification_identity,
        initial_state_contract_identity=readiness.initial_state_contract_identity,
        execution_identity=readiness.software_execution_identity,
        mapping_family_identity=readiness.mapping_comparison_family_identity,
        mapping_semantics_identity=readiness.mapping_semantics_identity,
    )
    current_resolved_bytes = _canonical_json_bytes(current_resolved_document)
    if _digest(current_resolved_bytes) != record.resolved_identity_digest:
        raise EvaluationReadinessError("resolved readiness identity changed after freeze")
    expected_frozen = _digest(
        EVALUATION_FREEZE_SCHEMA_VERSION.encode("ascii")
        + b"\0"
        + current_manifest_bytes
        + b"\0"
        + current_resolved_bytes
    )
    if expected_frozen != record.frozen_digest:
        raise EvaluationReadinessError("freeze identity mismatch")


@dataclass(frozen=True, slots=True)
class EvaluationConditionPair:
    """The world/tool pair with shared non-condition configuration."""

    world: EvaluationManifest
    tool: EvaluationManifest

    def __post_init__(self) -> None:
        if not isinstance(self.world, EvaluationManifest) or not isinstance(
            self.tool, EvaluationManifest
        ):
            raise TypeError("condition pair requires two EvaluationManifest values")
        if self.world.condition_id != "world" or self.tool.condition_id != "tool":
            raise EvaluationManifestError(
                "world/tool condition IDs must be exactly 'world' and 'tool'"
            )
        if self.world.requested_control_frame != "world":
            raise EvaluationManifestError("world condition must request world control")
        if self.tool.requested_control_frame != "tool":
            raise EvaluationManifestError("tool condition must request tool control")
        if self.world.condition_order == self.tool.condition_order:
            raise EvaluationManifestError("condition order must be unique")
        if {self.world.condition_order, self.tool.condition_order} != {0, 1}:
            raise EvaluationManifestError("condition order must contain exactly 0 and 1")
        if self.world.task_order != self.tool.task_order:
            raise EvaluationManifestError("task order must match across world/tool conditions")
        left = _shared_condition_document(self.world)
        right = _shared_condition_document(self.tool)
        differences = _document_differences(left, right)
        if differences:
            raise EvaluationManifestError(
                "world/tool shared invariant mismatch: " + ", ".join(differences)
            )


def _shared_condition_document(manifest: EvaluationManifest) -> dict[str, object]:
    document = manifest.to_document()
    for key in ("condition_id", "condition_order", "requested_control_frame", "control_mapping"):
        document.pop(key, None)
    document["parameters"] = [
        item
        for item in document["parameters"]  # type: ignore[index]
        if item["owner"]["axis"] != PluginAxis.CONTROL_MAPPING.value  # type: ignore[index]
    ]
    return document


def _document_differences(left: object, right: object, prefix: str = "") -> tuple[str, ...]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                paths.append(path)
            else:
                paths.extend(_document_differences(left[key], right[key], path))
        return tuple(paths)
    if isinstance(left, list) and isinstance(right, list):
        paths = []
        for index in range(max(len(left), len(right))):
            path = f"{prefix}[{index}]"
            if index >= len(left) or index >= len(right):
                paths.append(path)
            else:
                paths.extend(_document_differences(left[index], right[index], path))
        return tuple(paths)
    return () if left == right else (prefix,)


def _mapping_parameter_difference(
    world: EvaluationManifest,
    tool: EvaluationManifest,
    world_composition: ResolvedExperimentComposition,
    tool_composition: ResolvedExperimentComposition,
) -> tuple[str, ...]:
    world_item = _parameter_item_for_owner(world, PluginAxis.CONTROL_MAPPING)
    tool_item = _parameter_item_for_owner(tool, PluginAxis.CONTROL_MAPPING)
    world_values = {} if world_item is None else dict(world_item.values)
    tool_values = {} if tool_item is None else dict(tool_item.values)
    world_fields = {
        field.name: field
        for field in world_composition.control_mapping.parameter_contract.fields
    }
    tool_fields = {
        field.name: field
        for field in tool_composition.control_mapping.parameter_contract.fields
    }
    differences: list[str] = []
    for name in sorted(set(world_values) | set(tool_values)):
        left = world_values.get(name)
        right = tool_values.get(name)
        if name in world_values and name in tool_values and left == right:
            continue
        left_field = world_fields.get(name)
        right_field = tool_fields.get(name)
        if left_field is None or right_field is None:
            differences.append(f"control_mapping.parameters.{name}")
            continue
        if not (left_field.condition_specific and right_field.condition_specific):
            differences.append(f"control_mapping.parameters.{name}")
    return tuple(differences)


def _validate_condition_pair_compositions(
    pair: EvaluationConditionPair,
    world_composition: ResolvedExperimentComposition,
    tool_composition: ResolvedExperimentComposition,
) -> None:
    family_identities = (
        world_composition.control_mapping.comparison_family_identity,
        tool_composition.control_mapping.comparison_family_identity,
    )
    if any(identity is None for identity in family_identities):
        raise EvaluationReadinessError(
            "world/tool mappings must declare comparison family identity"
        )
    if family_identities[0] != family_identities[1]:
        raise EvaluationReadinessError(
            "world/tool mappings must use the same comparison family identity"
        )
    semantics_identities = (
        world_composition.control_mapping.mapping_semantics_identity,
        tool_composition.control_mapping.mapping_semantics_identity,
    )
    if any(identity is None for identity in semantics_identities):
        raise EvaluationReadinessError(
            "world/tool mappings must declare mapping semantics identity"
        )
    if semantics_identities[0] != semantics_identities[1]:
        raise EvaluationReadinessError(
            "world/tool mappings must use the same mapping semantics identity"
        )
    for label, manifest, composition in (
        ("world", pair.world, world_composition),
        ("tool", pair.tool, tool_composition),
    ):
        parameter_item = _parameter_item_for_owner(
            manifest, PluginAxis.CONTROL_MAPPING
        )
        parameters = {} if parameter_item is None else parameter_item.values
        resolved_frame = composition.control_mapping.resolve_control_frame(parameters)
        if resolved_frame != manifest.requested_control_frame:
            raise EvaluationReadinessError(
                f"{label} mapping selection/requested frame mismatch"
            )
    differences = _mapping_parameter_difference(
        pair.world, pair.tool, world_composition, tool_composition
    )
    if differences:
        raise EvaluationReadinessError(
            "world/tool condition-specific mapping invariant mismatch: "
            + ", ".join(differences)
        )


@dataclass(frozen=True, slots=True)
class EvaluationConditionPairReadiness:
    world: EvaluationReadiness
    tool: EvaluationReadiness
    pair_identity: str

    def __post_init__(self) -> None:
        if not isinstance(self.world, EvaluationReadiness) or not isinstance(
            self.tool, EvaluationReadiness
        ):
            raise EvaluationManifestError(
                "condition pair readiness requires two EvaluationReadiness values"
            )
        if self.world.readiness_status is not ReadinessStatus.READY:
            raise EvaluationManifestError("world readiness must be ready")
        if self.tool.readiness_status is not ReadinessStatus.READY:
            raise EvaluationManifestError("tool readiness must be ready")
        _digest_value("pair_identity", self.pair_identity)

    @property
    def frozen_digest(self) -> str:
        return self.pair_identity


def build_evaluation_condition_pair_readiness(
    pair: EvaluationConditionPair,
    registries: ExperimentPluginRegistries,
    *,
    execution_identity: SoftwareExecutionIdentity,
) -> EvaluationConditionPairReadiness:
    """Resolve both conditions and return a pair only if both are ready."""

    if not isinstance(pair, EvaluationConditionPair):
        raise TypeError("pair must use EvaluationConditionPair")
    try:
        world_composition = compose_experiment(pair.world.plugin_manifest, registries)
        tool_composition = compose_experiment(pair.tool.plugin_manifest, registries)
        _validate_condition_pair_compositions(pair, world_composition, tool_composition)
        world = _readiness_from_composition(
            pair.world, world_composition, execution_identity
        )
        tool = _readiness_from_composition(
            pair.tool, tool_composition, execution_identity
        )
        pair_bytes = _canonical_json_bytes(
            {
                "schema_version": EVALUATION_FREEZE_SCHEMA_VERSION,
                "world": world.freeze_record.frozen_digest,
                "tool": tool.freeze_record.frozen_digest,
            }
        )
        return EvaluationConditionPairReadiness(
            world=world,
            tool=tool,
            pair_identity=_digest(pair_bytes),
        )
    except EvaluationManifestError:
        raise
    except (TypeError, ValueError) as exc:
        raise EvaluationReadinessError(str(exc)) from exc


def validate_world_tool_condition_pair(
    pair: EvaluationConditionPair,
    registries: ExperimentPluginRegistries,
    *,
    execution_identity: SoftwareExecutionIdentity,
) -> EvaluationConditionPairReadiness:
    return build_evaluation_condition_pair_readiness(
        pair, registries, execution_identity=execution_identity
    )


def assert_freeze_identity(
    record: FreezeRecord,
    manifest: EvaluationManifest,
    readiness: EvaluationReadiness,
) -> None:
    verify_freeze_identity(record, manifest, readiness)


# Descriptive aliases for callers that use the issue terminology.
canonical_encode = encode_evaluation_manifest
canonical_decode = decode_evaluation_manifest
compute_manifest_digest = evaluation_manifest_digest
WorldToolConditionPair = EvaluationConditionPair
ReadinessResult = EvaluationReadiness


__all__ = [
    "EVALUATION_FREEZE_SCHEMA_VERSION",
    "EVALUATION_MANIFEST_CONTRACT_VERSION",
    "EVALUATION_MANIFEST_DIGEST_ALGORITHM",
    "EVALUATION_MANIFEST_SCHEMA_VERSION",
    "EvaluationConditionPair",
    "EvaluationConditionPairReadiness",
    "EvaluationManifest",
    "EvaluationManifestDecodeError",
    "EvaluationManifestError",
    "EvaluationReadiness",
    "EvaluationReadinessError",
    "FreezeRecord",
    "ReadinessResult",
    "ReadinessStatus",
    "SoftwareExecutionIdentity",
    "WorldToolConditionPair",
    "assert_freeze_identity",
    "build_evaluation_condition_pair_readiness",
    "comparison_parameters_for_readiness",
    "build_evaluation_readiness",
    "canonical_decode",
    "canonical_encode",
    "compute_manifest_digest",
    "decode_evaluation_manifest",
    "encode_evaluation_manifest",
    "evaluation_manifest_digest",
    "validate_world_tool_condition_pair",
    "verify_freeze_identity",
]
