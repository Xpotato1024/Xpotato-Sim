from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
)
from xpotato_sim.schemas import RawInputFrame
from xpotato_sim.schemas.types import Vector3


def _coerce_vector3(name: str, value: object) -> Vector3:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain exactly three values")

    components = tuple(float(component) for component in value)
    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three values")

    return components


def _add_vector3(lhs: Vector3, rhs: Vector3) -> Vector3:
    return tuple(left + right for left, right in zip(lhs, rhs, strict=True))


def _scale_vector3(vector: Vector3, scalar: float) -> Vector3:
    return tuple(component * scalar for component in vector)


def _interpolate_vector3(start: Vector3, end: Vector3, fraction: float) -> Vector3:
    delta = tuple(end_component - start_component for start_component, end_component in zip(start, end, strict=True))
    return _add_vector3(start, _scale_vector3(delta, fraction))


@dataclass(frozen=True, slots=True)
class ProgrammedTargetFrame:
    """world frameのprogrammed target sample。

    ``t_s`` はtrajectory開始からのs、position/desired endpointはm、velocityはm/sである。
    """

    t_s: float
    target_position_m: Vector3
    desired_endpoint_m: Vector3
    target_velocity_mps: Vector3 | None = None
    phase: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_position_m", _coerce_vector3("target_position_m", self.target_position_m))
        object.__setattr__(self, "desired_endpoint_m", _coerce_vector3("desired_endpoint_m", self.desired_endpoint_m))
        if self.target_velocity_mps is not None:
            object.__setattr__(
                self,
                "target_velocity_mps",
                _coerce_vector3("target_velocity_mps", self.target_velocity_mps),
            )

    def to_raw_input_frame(self, *, trajectory_name: str, frame_index: int) -> RawInputFrame:
        metadata: dict[str, object] = {
            "source_kind": "programmed_target",
            "trajectory_name": trajectory_name,
            "target_position_m": self.target_position_m,
            "desired_endpoint_m": self.desired_endpoint_m,
            "t_s": self.t_s,
            "frame_index": frame_index,
        }
        if self.target_velocity_mps is not None:
            metadata["target_velocity_mps"] = self.target_velocity_mps
        if self.phase is not None:
            metadata["phase"] = self.phase

        return RawInputFrame(
            source="programmed_target",
            timestamp_s=self.t_s,
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class ProgrammedTargetTrajectory:
    """空でないframe列とそのorderingを所有するdeterministic trajectory。"""

    name: str
    frames: tuple[ProgrammedTargetFrame, ...]

    def __post_init__(self) -> None:
        if not self.frames:
            raise ValueError("ProgrammedTargetTrajectory requires at least one frame")

        object.__setattr__(self, "frames", tuple(self.frames))


class ProgrammedTargetInputSource:
    """memory上のtrajectoryをpollするdeterministic programmed target source。

    external I/Oとstart/close side effectはない。non-loopでは最終frameをholdし、
    loopでは先頭へ戻る。healthは常にactiveで、単一runtime loopからの直列readを前提とする。
    """

    def __init__(self, trajectory: ProgrammedTargetTrajectory, *, loop: bool = False) -> None:
        self._trajectory = trajectory
        self._loop = loop
        self._index = 0

    def _next_frame_index(self) -> int:
        frame_count = len(self._trajectory.frames)
        if self._loop:
            frame_index = self._index % frame_count
            self._index = (frame_index + 1) % frame_count
            return frame_index

        frame_index = min(self._index, frame_count - 1)
        if self._index < frame_count - 1:
            self._index += 1
        else:
            self._index = frame_count - 1
        return frame_index

    def read_frame(self) -> RawInputFrame:
        frame_index = self._next_frame_index()
        frame = self._trajectory.frames[frame_index]
        return frame.to_raw_input_frame(trajectory_name=self._trajectory.name, frame_index=frame_index)

    def current_health(self) -> InputSourceHealth:
        return InputSourceHealth(InputSourceHealthStatus.ACTIVE, age_ms=0)


DEFAULT_SWEEP_X_INITIAL_POSITION_M: Vector3 = (0.0, 0.0, 0.0)
DEFAULT_SWEEP_X_POSITIVE_X_OFFSET_M = 0.1
DEFAULT_SWEEP_X_DT_S = 1.0 / 30.0
DEFAULT_SWEEP_X_INITIAL_HOLD_FRAMES = 3
DEFAULT_SWEEP_X_MOVE_FRAMES = 6
DEFAULT_SWEEP_X_SLOW_OR_HOLD_FRAMES = 3
DEFAULT_SWEEP_X_RETURN_FRAMES = 6
DEFAULT_SWEEP_X_FINAL_HOLD_FRAMES = 3


def _append_phase_frames(
    *,
    frames: list[ProgrammedTargetFrame],
    phase: str,
    position_m: Vector3,
    endpoint_m: Vector3,
    velocity_mps: Vector3,
    dt_s: float,
    frame_count: int,
    frame_index_start: int,
) -> int:
    for offset in range(frame_count):
        frames.append(
            ProgrammedTargetFrame(
                t_s=float(frame_index_start + offset) * dt_s,
                target_position_m=position_m,
                desired_endpoint_m=endpoint_m,
                target_velocity_mps=velocity_mps,
                phase=phase,
            )
        )

    return frame_index_start + frame_count


def build_sweep_x_trajectory(
    *,
    initial_position_m: Vector3 = DEFAULT_SWEEP_X_INITIAL_POSITION_M,
    positive_x_offset_m: float = DEFAULT_SWEEP_X_POSITIVE_X_OFFSET_M,
    dt_s: float = DEFAULT_SWEEP_X_DT_S,
    initial_hold_frames: int = DEFAULT_SWEEP_X_INITIAL_HOLD_FRAMES,
    move_frames: int = DEFAULT_SWEEP_X_MOVE_FRAMES,
    slow_or_hold_frames: int = DEFAULT_SWEEP_X_SLOW_OR_HOLD_FRAMES,
    return_frames: int = DEFAULT_SWEEP_X_RETURN_FRAMES,
    final_hold_frames: int = DEFAULT_SWEEP_X_FINAL_HOLD_FRAMES,
) -> ProgrammedTargetTrajectory:
    """world +X方向のm単位往復trajectoryを指定 ``dt_s`` orderingで生成する。"""

    initial_position_m = _coerce_vector3("initial_position_m", initial_position_m)
    if dt_s <= 0.0:
        raise ValueError("dt_s must be positive")

    for name, frame_count in (
        ("initial_hold_frames", initial_hold_frames),
        ("move_frames", move_frames),
        ("slow_or_hold_frames", slow_or_hold_frames),
        ("return_frames", return_frames),
        ("final_hold_frames", final_hold_frames),
    ):
        if frame_count < 1:
            raise ValueError(f"{name} must be at least 1")

    positive_x_target_m = _add_vector3(initial_position_m, (positive_x_offset_m, 0.0, 0.0))
    move_velocity_mps = (positive_x_offset_m / (move_frames * dt_s), 0.0, 0.0)
    return_velocity_mps = (-positive_x_offset_m / (return_frames * dt_s), 0.0, 0.0)

    frames: list[ProgrammedTargetFrame] = []
    frame_index = 0

    frame_index = _append_phase_frames(
        frames=frames,
        phase="initial_hold",
        position_m=initial_position_m,
        endpoint_m=initial_position_m,
        velocity_mps=(0.0, 0.0, 0.0),
        dt_s=dt_s,
        frame_count=initial_hold_frames,
        frame_index_start=frame_index,
    )

    for offset in range(move_frames):
        fraction = float(offset + 1) / float(move_frames)
        frames.append(
            ProgrammedTargetFrame(
                t_s=float(frame_index) * dt_s,
                target_position_m=_interpolate_vector3(initial_position_m, positive_x_target_m, fraction),
                desired_endpoint_m=_interpolate_vector3(initial_position_m, positive_x_target_m, fraction),
                target_velocity_mps=move_velocity_mps,
                phase="move_positive_x",
            )
        )
        frame_index += 1

    frame_index = _append_phase_frames(
        frames=frames,
        phase="slow_or_hold_at_positive_x",
        position_m=positive_x_target_m,
        endpoint_m=positive_x_target_m,
        velocity_mps=(0.0, 0.0, 0.0),
        dt_s=dt_s,
        frame_count=slow_or_hold_frames,
        frame_index_start=frame_index,
    )

    for offset in range(return_frames):
        fraction = float(offset + 1) / float(return_frames)
        frames.append(
            ProgrammedTargetFrame(
                t_s=float(frame_index) * dt_s,
                target_position_m=_interpolate_vector3(positive_x_target_m, initial_position_m, fraction),
                desired_endpoint_m=_interpolate_vector3(positive_x_target_m, initial_position_m, fraction),
                target_velocity_mps=return_velocity_mps,
                phase="return_to_initial",
            )
        )
        frame_index += 1

    _append_phase_frames(
        frames=frames,
        phase="final_hold",
        position_m=initial_position_m,
        endpoint_m=initial_position_m,
        velocity_mps=(0.0, 0.0, 0.0),
        dt_s=dt_s,
        frame_count=final_hold_frames,
        frame_index_start=frame_index,
    )

    return ProgrammedTargetTrajectory(name="sweep_x", frames=tuple(frames))


def build_sweep_x_input_source(
    *,
    loop: bool = False,
    initial_position_m: Vector3 = DEFAULT_SWEEP_X_INITIAL_POSITION_M,
    positive_x_offset_m: float = DEFAULT_SWEEP_X_POSITIVE_X_OFFSET_M,
    dt_s: float = DEFAULT_SWEEP_X_DT_S,
    initial_hold_frames: int = DEFAULT_SWEEP_X_INITIAL_HOLD_FRAMES,
    move_frames: int = DEFAULT_SWEEP_X_MOVE_FRAMES,
    slow_or_hold_frames: int = DEFAULT_SWEEP_X_SLOW_OR_HOLD_FRAMES,
    return_frames: int = DEFAULT_SWEEP_X_RETURN_FRAMES,
    final_hold_frames: int = DEFAULT_SWEEP_X_FINAL_HOLD_FRAMES,
) -> ProgrammedTargetInputSource:
    """I/Oを開始せず、生成済みsweep trajectoryを所有するreaderを返す。"""

    return ProgrammedTargetInputSource(
        build_sweep_x_trajectory(
            initial_position_m=initial_position_m,
            positive_x_offset_m=positive_x_offset_m,
            dt_s=dt_s,
            initial_hold_frames=initial_hold_frames,
            move_frames=move_frames,
            slow_or_hold_frames=slow_or_hold_frames,
            return_frames=return_frames,
            final_hold_frames=final_hold_frames,
        ),
        loop=loop,
    )


__all__ = [
    "build_sweep_x_input_source",
    "build_sweep_x_trajectory",
    "ProgrammedTargetFrame",
    "ProgrammedTargetInputSource",
    "ProgrammedTargetTrajectory",
]
