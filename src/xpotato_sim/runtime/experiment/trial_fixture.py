"""有限Gamepad記録のstrict読取。再生時刻とsource timestampを区別する。"""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from xpotato_sim.runtime.scene.objects import canonical, fields, identifier, number, strict_json
from xpotato_sim.schemas import parse_viewer_control_message_json

MAX_FIXTURE_BYTES = 1048576


@dataclass(frozen=True, slots=True)
class GamepadTrialFixture:
    """sampleを後で書換えない有界の記録。実機取得の証明とは区別する。"""
    fixture_id: str
    digest: str
    byte_count: int
    provenance: str
    samples: tuple[tuple[float, str], ...]

    def identity(self):
        return {"schema_version": "trial-gamepad-fixture/v1", "fixture_id": self.fixture_id,
            "sha256": self.digest, "bytes": self.byte_count, "sample_count": len(self.samples),
            "duration_s": self.samples[-1][0], "provenance": self.provenance}


def load_trial_fixture(path: Path) -> GamepadTrialFixture:
    """明示fileだけを読み、未知field・重複・時刻逆転・無限列を拒否する。"""
    with Path(path).open("rb") as stream:
        document = stream.read(MAX_FIXTURE_BYTES + 1)
    if len(document) > MAX_FIXTURE_BYTES:
        raise ValueError("fixture exceeds 1 MiB")
    raw = strict_json(document)
    fields(raw, {"schema_version", "fixture_id", "provenance", "samples"}, "trial fixture")
    if raw["schema_version"] != "trial-gamepad-fixture/v1":
        raise ValueError("unsupported trial fixture schema")
    fixture_id = identifier(raw["fixture_id"])
    if type(raw["provenance"]) is not str or not 1 <= len(raw["provenance"]) <= 1024:
        raise ValueError("explicit bounded fixture provenance required")
    if type(raw["samples"]) is not list or not 1 <= len(raw["samples"]) <= 10000:
        raise ValueError("fixture requires 1..10000 explicit samples")
    samples, previous = [], None
    for item in raw["samples"]:
        fields(item, {"offset_s", "message"}, "fixture sample")
        offset = number(item["offset_s"], nonnegative=True)
        if offset > 3600:
            raise ValueError("fixture duration exceeds 3600 seconds")
        message = canonical(item["message"]).decode("utf-8")
        if len(message.encode("utf-8")) > 65536:
            raise ValueError("fixture message exceeds 64 KiB")
        parsed = parse_viewer_control_message_json(message)
        session = parsed.metadata.get("viewer_provider_session_id")
        if (parsed.source_kind != "gamepad" or parsed.provider_id != "gamepad/v1"
                or parsed.provider_schema != "viewer_gamepad_sample/v1" or parsed.sequence is None
                or type(session) is not str or not session):
            raise ValueError("explicit Gamepad provider/session/sequence required")
        if previous is not None:
            if (offset <= previous[0] or session != previous[1] or parsed.sequence <= previous[2]
                    or parsed.timestamp_s < previous[3]):
                raise ValueError("fixture samples must be strictly ordered in one source session")
        previous = (offset, session, parsed.sequence, parsed.timestamp_s)
        samples.append((offset, message))
    return GamepadTrialFixture(fixture_id, sha256(document).hexdigest(), len(document),
        raw["provenance"], tuple(samples))
