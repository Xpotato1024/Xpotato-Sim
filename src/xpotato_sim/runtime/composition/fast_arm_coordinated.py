"""明示assembly・Gamepad Mapping・共同実行を一度だけ結線する。実I/Oは所有しない。"""
from __future__ import annotations
from collections.abc import Callable, Mapping
from types import MappingProxyType
from fast_arm_core.assembly import FastArmAssembly
from xpotato_sim.plugins.input_sources.viewer import ViewerInputSource
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping.implementation import (
    ViewerKeyboardGamepadMappingStrategy, normalize_viewer_control_mapping_parameters,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.coordinated import FastArmAssemblyMotionProvider
from xpotato_sim.runtime.execution.coordinated import CoordinatedRuntime, CoordinatedStepResult
from xpotato_sim.schemas import ViewerControlMessage
from xpotato_sim.schemas.coordinated import number


class FastArmCoordinatedGamepadRuntime:
    """ソフトウェア専用の明示composition。旧launch profile/v1を双腕へ読み替えない。"""
    def __init__(self, *, assembly: FastArmAssembly, mapping_parameters: Mapping[str, object],
                 side_to_arm: Mapping[str, str], epoch: str, dt_s: float,
                 max_input_age_s: float, clock: Callable[[], float]) -> None:
        if (not isinstance(side_to_arm, Mapping) or not set(side_to_arm) <= {"left", "right"}
                or len(side_to_arm) != len(assembly.arm_ids)
                or set(side_to_arm.values()) != set(assembly.arm_ids)):
            raise ValueError("explicit side binding must cover every assembly arm exactly once")
        if not callable(clock):
            raise TypeError("host monotonic clock required")
        self.parameters = normalize_viewer_control_mapping_parameters(mapping_parameters)
        if "gamepad_plane_control" not in self.parameters:
            raise ValueError("plane-control Mapping required")
        self.side_to_arm = MappingProxyType(dict(side_to_arm))
        self.clock = clock
        self.source = ViewerInputSource(clock=clock)
        self.mapping = ViewerKeyboardGamepadMappingStrategy(session=True)
        self.runtime = CoordinatedRuntime(FastArmAssemblyMotionProvider(assembly), epoch=epoch,
                                          dt_s=dt_s, max_input_age_s=max_input_age_s)

    def ingest(self, message: ViewerControlMessage) -> None:
        try:
            self.source.ingest_control_message(message)
        except Exception as exc:
            self.runtime.fail(f"source_ingress_failed:{type(exc).__name__}")
            raise

    def tick(self, *, epoch: str) -> CoordinatedStepResult:
        if self.runtime.state in ("stopped", "faulted"):
            return self.runtime.tick(None, epoch=epoch, now_s=0.)
        try:
            now = number(self.clock(), "host clock")
            frame = self.source.read_frame()
            received = self.source.last_received_at_s
            value = None if received is None else self.mapping.map_coordinated_input(
                frame, self.parameters, side_to_endpoint=self.side_to_arm, received_at_s=received)
        except Exception as exc:
            self.runtime.fail(f"input_mapping_failed:{type(exc).__name__}:{exc}")
            return self.runtime.tick(None, epoch=epoch, now_s=0.)
        return self.runtime.tick(value, epoch=epoch, now_s=now)

    def restart(self, *, epoch: str) -> None:
        self.runtime.restart(epoch=epoch, now_s=self.clock())
        self.source = ViewerInputSource(clock=self.clock)
        self.mapping = ViewerKeyboardGamepadMappingStrategy(session=True)

    def stop(self) -> None:
        self.runtime.stop()
