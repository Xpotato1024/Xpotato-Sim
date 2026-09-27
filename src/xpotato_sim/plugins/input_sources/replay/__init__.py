"""Replay Input Source Plugin implementation."""

from collections.abc import Mapping

from xpotato_sim.plugins.input_sources.replay.source import ReplayInputSource
from xpotato_sim.runtime.experiment.input_source import InputSourceRuntimeDependencies
from xpotato_sim.schemas import RawInputFrame


def build_frames(parameters: Mapping[str, object]) -> tuple[RawInputFrame, ...]:
    frames = parameters.get("frames")
    if frames is None:
        return (RawInputFrame(source="replay", timestamp_s=0.0, metadata=dict(parameters["metadata"])),)
    return tuple(frames)


def build_reader(parameters: Mapping[str, object], *, runtime_dependencies: InputSourceRuntimeDependencies | None = None) -> ReplayInputSource:
    frames = (
        runtime_dependencies.replay_frames
        if runtime_dependencies is not None and runtime_dependencies.replay_frames is not None
        else build_frames(parameters)
    )
    return ReplayInputSource(frames, loop=bool(parameters.get("loop", True)))


__all__ = ["ReplayInputSource", "build_frames", "build_reader"]
