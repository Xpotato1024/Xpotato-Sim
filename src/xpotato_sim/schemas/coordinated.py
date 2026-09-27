"""複数手先の要求・観測。入力受信の時計とsimulation時間を混同しない。"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite


def identifier(value: object, label: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 256 or value != value.strip() or "\x00" in value:
        raise ValueError(f"{label} requires a bounded canonical identifier")
    return value


def number(value: object, label: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} requires a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} requires a finite number") from exc
    if not isfinite(result) or (result <= 0 if positive else result < 0):
        raise ValueError(f"{label} is outside its finite range")
    return result


def vector(value: object, size: int, label: str) -> tuple[float, ...]:
    if type(value) not in (list, tuple) or len(value) != size or any(type(v) not in (int, float) for v in value):
        raise ValueError(f"{label} requires {size} finite components")
    try:
        result = tuple(float(v) for v in value)
    except OverflowError as exc:
        raise ValueError(f"{label} requires finite components") from exc
    if not all(isfinite(v) for v in result):
        raise ValueError(f"{label} requires finite components")
    return result


@dataclass(frozen=True, slots=True)
class EndpointVelocity:
    endpoint_id: str
    velocity_m_s: tuple[float, float, float]
    control_frame: str

    def __post_init__(self) -> None:
        identifier(self.endpoint_id, "endpoint_id")
        object.__setattr__(self, "velocity_m_s", vector(self.velocity_m_s, 3, "velocity_m_s"))
        if self.control_frame not in ("world", "tool"):
            raise ValueError("explicit world/tool control frame required")


@dataclass(frozen=True, slots=True)
class CoordinatedInput:
    """Mapping発行の要求。未取得と新規中立測定は別。received_at_sはhost monotonic。"""
    endpoints: tuple[EndpointVelocity, ...]
    source_epoch: str | None
    source_sequence: int | None
    source_timestamp_s: float
    received_at_s: float
    available: bool
    neutral: bool
    source_payload_sha256: str

    def __post_init__(self) -> None:
        if type(self.endpoints) is not tuple or not 1 <= len(self.endpoints) <= 2:
            raise ValueError("one or two explicit endpoint commands required")
        if any(type(v) is not EndpointVelocity for v in self.endpoints):
            raise TypeError("typed EndpointVelocity values required")
        if len({v.endpoint_id for v in self.endpoints}) != len(self.endpoints):
            raise ValueError("duplicate endpoint_id")
        if type(self.available) is not bool or type(self.neutral) is not bool:
            raise TypeError("health and neutral must be bool")
        if self.available:
            identifier(self.source_epoch, "source_epoch")
            if type(self.source_sequence) is not int or self.source_sequence < 0:
                raise ValueError("available input requires a sequence")
        elif self.neutral:
            raise ValueError("unavailable input cannot prove neutral")
        number(self.source_timestamp_s, "source_timestamp_s")
        number(self.received_at_s, "received_at_s")
        if (type(self.source_payload_sha256) is not str or len(self.source_payload_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.source_payload_sha256)):
            raise ValueError("source_payload_sha256 requires lowercase SHA-256")
        if (self.neutral or not self.available) and any(any(v != 0 for v in e.velocity_m_s) for e in self.endpoints):
            raise ValueError("neutral/unavailable input cannot request nonzero velocity")


@dataclass(frozen=True, slots=True)
class EndpointObservation:
    endpoint_id: str
    position_m: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class CoordinatedSnapshot:
    model_sha256: str
    generation: int
    simulation_time_s: float
    joint_names: tuple[str, ...]
    joint_positions_rad: tuple[float, ...]
    endpoints: tuple[EndpointObservation, ...]
    execution_semantics: str
