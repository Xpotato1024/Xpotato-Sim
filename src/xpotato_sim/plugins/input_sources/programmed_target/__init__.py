"""Programmed-target Input Source Plugin implementation."""

from collections.abc import Mapping

from xpotato_sim.plugins.input_sources.programmed_target.source import (
    ProgrammedTargetFrame,
    ProgrammedTargetInputSource,
    ProgrammedTargetTrajectory,
    build_sweep_x_input_source,
    build_sweep_x_trajectory,
)
from xpotato_sim.schemas import RawInputFrame


def _validate_parameters(parameters: Mapping[str, object]) -> tuple[int, bool]:
    steps = parameters["steps"]
    if type(steps) is not int or steps < 1:
        raise ValueError("steps must be a positive integer")

    preset = parameters.get("preset", "sweep_x")
    if preset != "sweep_x":
        raise ValueError("unsupported programmed_target preset")

    loop = parameters.get("loop", False)
    if type(loop) is not bool:
        raise ValueError("loop must be a boolean")

    return steps, loop


def build_frames(parameters: Mapping[str, object]) -> tuple[RawInputFrame, ...]:
    steps, loop = _validate_parameters(parameters)
    source = build_sweep_x_input_source(
        initial_position_m=parameters["initial_position_m"],
        loop=loop,
    )
    return tuple(source.read_frame() for _ in range(steps))


def build_reader(parameters: Mapping[str, object]) -> ProgrammedTargetInputSource:
    _, loop = _validate_parameters(parameters)
    source = build_sweep_x_input_source(
        initial_position_m=parameters["initial_position_m"],
        loop=loop,
    )
    return source


__all__ = [
    "ProgrammedTargetFrame",
    "ProgrammedTargetInputSource",
    "ProgrammedTargetTrajectory",
    "build_frames",
    "build_reader",
    "build_sweep_x_input_source",
    "build_sweep_x_trajectory",
]
