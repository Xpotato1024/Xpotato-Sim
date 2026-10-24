"""Gamepadの既存wireを共通session境界へ渡す。取得・Mappingは複製しない。"""
from xpotato_sim.plugins.input_sources.viewer.source import ViewerInputSource
from xpotato_sim.runtime.experiment.session_input import SessionInputMessage
from xpotato_sim.schemas import ViewerControlMessage, parse_viewer_control_message_json


class GamepadSessionSource(ViewerInputSource):
    initial_wait_reason: str = "awaiting_gamepad_input"

    def __init__(self, *, parameters, clock):
        if parameters:
            raise ValueError("Gamepad session does not accept device parameters")
        super().__init__(clock=clock)

    @staticmethod
    def validated_message(parsed):
        if (type(parsed) is not ViewerControlMessage or parsed.source_kind != "gamepad"
                or parsed.provider_id != "gamepad/v1"
                or parsed.provider_schema != "viewer_gamepad_sample/v1"):
            raise ValueError("coordinated viewer requires explicit gamepad/v1 input")
        return SessionInputMessage("gamepad", parsed.metadata.get("viewer_provider_session_id"),
            parsed.sequence, parsed.timestamp_s, parsed,
            available=parsed.gamepad is not None and not parsed.gamepad.stale and parsed.gamepad.connected)

    def parse_message(self, message):
        return self.validated_message(parse_viewer_control_message_json(message))

    def ingest_session_message(self, message):
        if type(message) is ViewerControlMessage:
            # 保存済みassembly診断のtyped入口は既存sourceの互換検証へ委譲する。
            # finite trialのwire入口はparse_messageで明示provider/sessionを要求する。
            self.ingest_control_message(message)
            return
        if type(message) is not SessionInputMessage or type(message.payload) is not ViewerControlMessage:
            raise TypeError("Gamepad session requires its validated wire sample")
        self.ingest_control_message(message.payload)
