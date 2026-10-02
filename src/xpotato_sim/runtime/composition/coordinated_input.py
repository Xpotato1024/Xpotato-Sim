"""名前付き1個以上の手先を同じSource/Mapping/共同実行へ接続する。"""
from __future__ import annotations
from collections.abc import Callable, Mapping
from types import MappingProxyType
from xpotato_sim.plugins.input_sources.viewer import ViewerInputSource
from xpotato_sim.runtime.execution.coordinated import CoordinatedRuntime, CoordinatedStepResult
from xpotato_sim.schemas import ViewerControlMessage
from xpotato_sim.schemas.coordinated import number


class CoordinatedInputRuntime:
    """ソフトウェア専用の明示composition。旧launch profile/v1を双腕へ読み替えない。"""
    def __init__(self, *, provider, mapping_parameters: Mapping[str, object],
                 mapping_factory: Callable[[], object], side_to_arm: Mapping[str, str],
                 epoch: str, dt_s: float, max_input_age_s: float, clock: Callable[[], float]) -> None:
        if (not isinstance(side_to_arm, Mapping) or not set(side_to_arm) <= {"left", "right"}
                or len(side_to_arm) != len(provider.endpoint_ids)
                or set(side_to_arm.values()) != set(provider.endpoint_ids)):
            raise ValueError("explicit side binding must cover every assembly arm exactly once")
        if not callable(clock):
            raise TypeError("host monotonic clock required")
        self.parameters = dict(mapping_parameters)
        self._mapping_factory = mapping_factory
        controls = {"gamepad_trigger_control"} & set(self.parameters)
        if len(controls) != 1:
            raise ValueError("exactly one coordinated Gamepad control Mapping is required")
        self.side_to_arm = MappingProxyType(dict(side_to_arm))
        self.clock = clock
        self.source = ViewerInputSource(clock=clock)
        self.mapping = self._mapping_factory()
        self.runtime = CoordinatedRuntime(provider, epoch=epoch,
                                          dt_s=dt_s, max_input_age_s=max_input_age_s)
        self.last_frame = None
        self._consumed_value = None
        self.mapping.reset_coordinated_presentation(
            self.parameters, reason="awaiting_gamepad_input")

    def ingest(self, message: ViewerControlMessage) -> None:
        try:
            self.source.ingest_control_message(message)
            self._consumed_value = None
        except Exception as exc:
            self.runtime.fail(f"source_ingress_failed:{type(exc).__name__}")
            raise

    def tick(self, *, epoch: str) -> CoordinatedStepResult:
        if self.runtime.state in ("stopped", "faulted"):
            self.mapping.reset_coordinated_presentation(
                self.parameters, reason=self.runtime.reason or self.runtime.state)
            return self.runtime.tick(None, epoch=epoch, now_s=0.)
        try:
            now = number(self.clock(), "host clock")
            frame = self.source.read_frame()
            self.last_frame = frame
            received = self.source.last_received_at_s
            value = self._consumed_value
            if value is None and received is not None:
                value = self.mapping.map_coordinated_input(
                    frame, self.parameters, side_to_endpoint=self.side_to_arm, received_at_s=received)
        except Exception as exc:
            self.runtime.fail(f"input_mapping_failed:{type(exc).__name__}:{exc}")
            self.mapping.reset_coordinated_presentation(
                self.parameters, reason=self.runtime.reason or "input_mapping_failed")
            return self.runtime.tick(None, epoch=epoch, now_s=0.)
        result = self.runtime.tick(value, epoch=epoch, now_s=now)
        if result.state in ("stopped", "faulted"):
            self.mapping.reset_coordinated_presentation(
                self.parameters, reason=result.reason or result.state)
        return result

    def consume_received_input(self) -> None:
        """各sampleのMapping離散状態を順に更新し、古いmotionは積分しない。"""
        frame = self.source.read_frame()
        received = self.source.last_received_at_s
        value = self.mapping.map_coordinated_input(
            frame, self.parameters, side_to_endpoint=self.side_to_arm, received_at_s=received)
        self.last_frame = frame
        self.runtime.consume_received_input(value)
        self._consumed_value = value

    @property
    def latest_trigger_presentation(self) -> dict[str, object] | None:
        value = self.mapping.latest_trigger_presentation
        if value is not None:
            value["endpoint_bindings"] = dict(self.side_to_arm)
        return value

    def restart(self, *, epoch: str) -> None:
        self.runtime.restart(epoch=epoch, now_s=self.clock())
        self.source = ViewerInputSource(clock=self.clock)
        self.mapping = self._mapping_factory()
        self.last_frame = None
        self._consumed_value = None
        self.mapping.reset_coordinated_presentation(
            self.parameters, reason="awaiting_gamepad_input")

    def stop(self) -> None:
        self.runtime.stop()
