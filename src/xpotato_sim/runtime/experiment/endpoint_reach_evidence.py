"""endpoint reachのTask context、観測、canonical evidence契約。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from math import isfinite, sqrt

from xpotato_sim.runtime.experiment.contracts import (
    CanonicalEvidenceSet,
    EvidenceStatus,
    TaskTerminalClassification,
    VersionedIdentity,
)


Vector3 = tuple[float, float, float]

ENDPOINT_REACH_TERMINAL_EVIDENCE = VersionedIdentity(
    "endpoint_reach_terminal_classification", 1
)
ENDPOINT_REACH_TRAJECTORY_EVIDENCE = VersionedIdentity(
    "endpoint_reach_measured_trajectory", 1
)
ENDPOINT_REACH_TERMINAL_PROVENANCE = "endpoint_reach_task/v1:terminal"
ENDPOINT_REACH_TRAJECTORY_PROVENANCE = (
    "endpoint_reach_task/v1:measured_trajectory"
)


def _number(name: str, value: object, *, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not isfinite(result) or (non_negative and result < 0.0):
        raise ValueError(f"{name} must be a finite non-negative number")
    return result


def _vector3(name: str, value: object) -> Vector3:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 3
    ):
        raise ValueError(f"{name} must contain exactly three finite numbers")
    return tuple(  # type: ignore[return-value]
        _number(f"{name}[{index}]", item) for index, item in enumerate(value)
    )


def _distance(left: Vector3, right: Vector3) -> float:
    return sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


class EndpointReachMotionStatus(str, Enum):
    """measured sampleに対応するcommand streamの状態。"""

    NOMINAL = "nominal"
    HELD = "held"
    REJECTED = "rejected"
    STALE = "stale"
    RESET = "reset"
    TECHNICAL_INVALID = "technical_invalid"


@dataclass(frozen=True, slots=True)
class EndpointReachTaskContext:
    """upper manifestから一度だけbindするworld-frame task条件。"""

    initial_position_world_m: Vector3
    target_position_world_m: Vector3
    target_tolerance_m: float
    dwell_interval_s: float
    timeout_s: float

    def __post_init__(self) -> None:
        initial = _vector3("initial_position_world_m", self.initial_position_world_m)
        target = _vector3("target_position_world_m", self.target_position_world_m)
        if _distance(initial, target) == 0.0:
            raise ValueError("endpoint reach target must differ from the initial position")
        tolerance = _number("target_tolerance_m", self.target_tolerance_m)
        dwell = _number("dwell_interval_s", self.dwell_interval_s)
        timeout = _number("timeout_s", self.timeout_s)
        if tolerance <= 0.0 or dwell <= 0.0 or timeout <= 0.0:
            raise ValueError("endpoint reach tolerance, dwell, and timeout must be positive")
        if tolerance >= _distance(initial, target):
            raise ValueError("endpoint reach tolerance must be smaller than target distance")
        if dwell > timeout:
            raise ValueError("endpoint reach dwell must not exceed timeout")
        object.__setattr__(self, "initial_position_world_m", initial)
        object.__setattr__(self, "target_position_world_m", target)
        object.__setattr__(self, "target_tolerance_m", tolerance)
        object.__setattr__(self, "dwell_interval_s", dwell)
        object.__setattr__(self, "timeout_s", timeout)


@dataclass(frozen=True, slots=True)
class EndpointReachObservation:
    """runnerがTaskへ渡す単一時点のworld-frame measured observation。"""

    elapsed_time_s: float
    position_world_m: Vector3 | None
    measurement_status: EvidenceStatus = EvidenceStatus.MEASURED
    motion_status: EndpointReachMotionStatus = EndpointReachMotionStatus.NOMINAL
    reason: str | None = None

    def __post_init__(self) -> None:
        elapsed = _number("elapsed_time_s", self.elapsed_time_s, non_negative=True)
        object.__setattr__(self, "elapsed_time_s", elapsed)
        if self.measurement_status not in {
            EvidenceStatus.MEASURED,
            EvidenceStatus.UNAVAILABLE,
            EvidenceStatus.INVALID,
        }:
            raise ValueError(
                "endpoint observation status must be measured, unavailable, or invalid"
            )
        if not isinstance(self.motion_status, EndpointReachMotionStatus):
            raise TypeError("endpoint observation motion_status must be typed")
        if self.measurement_status is EvidenceStatus.MEASURED:
            if self.position_world_m is None:
                raise ValueError("measured endpoint observation requires position_world_m")
            object.__setattr__(
                self,
                "position_world_m",
                _vector3("position_world_m", self.position_world_m),
            )
        elif self.position_world_m is not None:
            raise ValueError("non-measured endpoint observation must not carry a position")
        if (
            self.measurement_status is not EvidenceStatus.MEASURED
            or self.motion_status is not EndpointReachMotionStatus.NOMINAL
        ) and (not isinstance(self.reason, str) or not self.reason.strip()):
            raise ValueError("non-nominal endpoint observation requires a reason")
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason.strip()
        ):
            raise ValueError("endpoint observation reason must be non-empty or null")


@dataclass(frozen=True, slots=True)
class EndpointReachTerminalEvidence:
    """Taskが生成した終端分類と経過時間。"""

    classification: TaskTerminalClassification
    elapsed_time_s: float | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class EndpointReachTrajectorySample:
    """MuJoCo world frameの時刻付きendpoint位置。"""

    elapsed_time_s: float
    position_world_m: Vector3


@dataclass(frozen=True, slots=True)
class EndpointReachTrajectoryEvidence:
    """初期位置、target、measured sample列からなるtrajectory。"""

    initial_position_world_m: Vector3
    target_position_world_m: Vector3
    samples: tuple[EndpointReachTrajectorySample, ...]


def decode_endpoint_reach_terminal_evidence(
    evidence: CanonicalEvidenceSet,
) -> EndpointReachTerminalEvidence:
    """Task-owned terminal evidenceをdefaultなしで厳密にdecodeする。"""

    entry = evidence.require(ENDPOINT_REACH_TERMINAL_EVIDENCE)
    if entry.status is not EvidenceStatus.MEASURED:
        raise ValueError("endpoint reach terminal evidence must be measured")
    if entry.provenance != ENDPOINT_REACH_TERMINAL_PROVENANCE:
        raise ValueError("endpoint reach terminal evidence has an invalid producer")
    value = entry.value
    if not isinstance(value, Mapping):
        raise ValueError("endpoint reach terminal evidence must be an object")
    expected = {"classification", "elapsed_time_s", "reason"}
    if set(value) != expected:
        raise ValueError(
            "endpoint reach terminal evidence fields must be exactly "
            f"{sorted(expected)!r}"
        )
    try:
        classification = TaskTerminalClassification(value["classification"])
    except (TypeError, ValueError) as exc:
        raise ValueError("unknown endpoint reach terminal classification") from exc
    elapsed_value = value["elapsed_time_s"]
    elapsed = (
        None
        if elapsed_value is None
        else _number("elapsed_time_s", elapsed_value, non_negative=True)
    )
    reason = value["reason"]
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        raise ValueError("terminal reason must be a non-empty string or null")
    if classification is TaskTerminalClassification.SUCCESS and elapsed is None:
        raise ValueError("successful endpoint reach evidence requires elapsed_time_s")
    if classification is TaskTerminalClassification.TECHNICAL_INVALID and reason is None:
        raise ValueError("technical-invalid endpoint reach evidence requires a reason")
    return EndpointReachTerminalEvidence(classification, elapsed, reason)


def decode_endpoint_reach_trajectory_evidence(
    evidence: CanonicalEvidenceSet,
) -> EndpointReachTrajectoryEvidence:
    """measured world-frame trajectoryを補間せず厳密にdecodeする。"""

    entry = evidence.require(ENDPOINT_REACH_TRAJECTORY_EVIDENCE)
    if entry.status is not EvidenceStatus.MEASURED:
        raise ValueError("endpoint reach trajectory evidence must be measured")
    if entry.provenance != ENDPOINT_REACH_TRAJECTORY_PROVENANCE:
        raise ValueError("endpoint reach trajectory evidence has an invalid producer")
    value = entry.value
    if not isinstance(value, Mapping):
        raise ValueError("endpoint reach trajectory evidence must be an object")
    expected = {
        "initial_position_world_m",
        "target_position_world_m",
        "samples",
    }
    if set(value) != expected:
        raise ValueError(
            "endpoint reach trajectory evidence fields must be exactly "
            f"{sorted(expected)!r}"
        )
    initial = _vector3(
        "initial_position_world_m", value["initial_position_world_m"]
    )
    target = _vector3("target_position_world_m", value["target_position_world_m"])
    if _distance(initial, target) == 0.0:
        raise ValueError("endpoint reach target must differ from the initial position")
    raw_samples = value["samples"]
    if not isinstance(raw_samples, Sequence) or isinstance(raw_samples, (str, bytes)):
        raise ValueError("endpoint reach trajectory samples must be a non-empty sequence")
    samples: list[EndpointReachTrajectorySample] = []
    for index, raw_sample in enumerate(raw_samples):
        if not isinstance(raw_sample, Mapping) or set(raw_sample) != {
            "elapsed_time_s",
            "position_world_m",
        }:
            raise ValueError(
                "each trajectory sample must contain elapsed_time_s and position_world_m"
            )
        sample = EndpointReachTrajectorySample(
            elapsed_time_s=_number(
                f"samples[{index}].elapsed_time_s",
                raw_sample["elapsed_time_s"],
                non_negative=True,
            ),
            position_world_m=_vector3(
                f"samples[{index}].position_world_m",
                raw_sample["position_world_m"],
            ),
        )
        if samples and sample.elapsed_time_s < samples[-1].elapsed_time_s:
            raise ValueError("trajectory sample times must be non-decreasing")
        samples.append(sample)
    if not samples:
        raise ValueError("endpoint reach trajectory samples must not be empty")
    if samples[0].position_world_m != initial:
        raise ValueError("first trajectory sample must equal the initial measured position")
    return EndpointReachTrajectoryEvidence(initial, target, tuple(samples))


__all__ = [
    "ENDPOINT_REACH_TERMINAL_EVIDENCE",
    "ENDPOINT_REACH_TERMINAL_PROVENANCE",
    "ENDPOINT_REACH_TRAJECTORY_EVIDENCE",
    "ENDPOINT_REACH_TRAJECTORY_PROVENANCE",
    "EndpointReachMotionStatus",
    "EndpointReachObservation",
    "EndpointReachTaskContext",
    "EndpointReachTerminalEvidence",
    "EndpointReachTrajectoryEvidence",
    "EndpointReachTrajectorySample",
    "decode_endpoint_reach_terminal_evidence",
    "decode_endpoint_reach_trajectory_evidence",
]
