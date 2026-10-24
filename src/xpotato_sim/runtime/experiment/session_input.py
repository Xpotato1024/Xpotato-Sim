"""入力装置ごとのwire検証と、共通有限試行の受領・順序検査の境界。"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from xpotato_sim.schemas import RawInputFrame
from xpotato_sim.schemas.coordinated import identifier, number


@dataclass(frozen=True, slots=True)
class SessionInputMessage:
    """元streamのidentity。ageは取得後の既知の遅れを含み、制御tickで更新しない。"""
    stream_id: str
    session_id: str
    sequence: int
    timestamp_s: float
    payload: object
    source_age_s: float = 0.0
    available: bool = True
    producer_receipt_s: float | None = None

    def __post_init__(self):
        identifier(self.stream_id, "input stream")
        identifier(self.session_id, "source session")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("nonnegative source sequence required")
        number(self.timestamp_s, "source timestamp")
        number(self.source_age_s, "source age")
        if type(self.available) is not bool:
            raise ValueError("explicit source availability required")
        if self.producer_receipt_s is not None:
            number(self.producer_receipt_s, "producer receipt")


class SessionInputSource(Protocol):
    """factoryは取得を開始しない。runnerがsource receiptとlifecycleを単一所有する。"""
    last_received_at_s: float | None
    initial_wait_reason: str

    def parse_message(self, message: str) -> SessionInputMessage: ...
    def ingest_session_message(self, message: SessionInputMessage) -> None: ...
    def read_frame(self) -> RawInputFrame: ...


@dataclass(frozen=True, slots=True)
class InputSourceSessionBinding:
    """装置名によるruntime分岐を避ける、登録済みの有限試行用factory。"""
    provider_id: str
    message_schema: str
    factory: Callable[..., SessionInputSource]
    application_transport: str | None = None

    def __post_init__(self):
        identifier(self.provider_id, "session provider")
        identifier(self.message_schema, "session message schema")
        if not callable(self.factory):
            raise TypeError("session source factory required")
        if self.application_transport is not None:
            identifier(self.application_transport, "application transport")

    def create(self, parameters: Mapping[str, object], *, clock: Callable[[], float]):
        return self.factory(parameters=parameters, clock=clock)
