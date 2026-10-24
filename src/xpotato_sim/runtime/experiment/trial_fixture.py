"""有限入力記録のstrict読取。取得元と再生時刻を明記し、新規sampleを捏造しない。"""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from xpotato_sim.runtime.scene.objects import canonical, fields, identifier, number, strict_json
from xpotato_sim.runtime.composition.input_sessions import resolve_source_session
from xpotato_sim.runtime.experiment.contracts import PluginSelection
import json

MAX_FIXTURE_BYTES = 1048576


@dataclass(frozen=True, slots=True)
class TrialInputFixture:
    """sampleを後で書換えない有界の記録。実機取得の証明とは区別する。"""
    fixture_id: str
    digest: str
    byte_count: int
    provenance: str
    samples: tuple[tuple[float, str], ...]
    schema: str
    input_json: str

    def identity(self):
        return {"schema_version": self.schema, "fixture_id": self.fixture_id,
            "sha256": self.digest, "bytes": self.byte_count, "sample_count": len(self.samples),
            "duration_s": self.samples[-1][0], "provenance": self.provenance,
            **({"input": json.loads(self.input_json)} if self.schema == "trial-input-fixture/v1" else {})}

    def assert_profile(self, profile):
        expected = {"plugin": {"name": profile.input_source.plugin_id, "version": profile.input_source.contract_version},
            "provider": profile.provider_id, "parameters": profile.input_parameters}
        if canonical(expected) != self.input_json.encode("utf-8"):
            raise ValueError("fixture input binding differs from the frozen trial profile")


def load_trial_fixture(path: Path) -> TrialInputFixture:
    """明示fileだけを読み、未知field・重複・時刻逆転・無限列を拒否する。"""
    with Path(path).open("rb") as stream:
        document = stream.read(MAX_FIXTURE_BYTES + 1)
    if len(document) > MAX_FIXTURE_BYTES:
        raise ValueError("fixture exceeds 1 MiB")
    raw = strict_json(document, max_bytes=MAX_FIXTURE_BYTES)
    if type(raw) is not dict or raw.get("schema_version") not in {"trial-gamepad-fixture/v1", "trial-input-fixture/v1"}:
        raise ValueError("unsupported trial fixture schema")
    generic = raw["schema_version"] == "trial-input-fixture/v1"
    fields(raw, {"schema_version", "fixture_id", "provenance", "samples"} | ({"input"} if generic else set()), "trial fixture")
    source = raw["input"] if generic else {"plugin": {"name": "viewer", "version": 1}, "provider": "gamepad/v1", "parameters": {}}
    fields(source, {"plugin", "provider", "parameters"}, "fixture input")
    fields(source["plugin"], {"name", "version"}, "fixture input plugin")
    selection = PluginSelection(source["plugin"]["name"], source["plugin"]["version"])
    binding = resolve_source_session(selection, source["provider"])
    reader = binding.create(source["parameters"], clock=lambda: 0.)
    fixture_id = identifier(raw["fixture_id"])
    if type(raw["provenance"]) is not str or not 1 <= len(raw["provenance"]) <= 1024:
        raise ValueError("explicit bounded fixture provenance required")
    if type(raw["samples"]) is not list or not 1 <= len(raw["samples"]) <= 10000:
        raise ValueError("fixture requires 1..10000 explicit samples")
    samples, previous, last_offset = [], {}, None
    for item in raw["samples"]:
        fields(item, {"offset_s", "message"}, "fixture sample")
        offset = number(item["offset_s"], nonnegative=True)
        if offset > 3600:
            raise ValueError("fixture duration exceeds 3600 seconds")
        message = canonical(item["message"]).decode("utf-8")
        if len(message.encode("utf-8")) > 65536:
            raise ValueError("fixture message exceeds 64 KiB")
        parsed = reader.parse_message(message)
        if not parsed.available:
            raise ValueError("fixture contains unavailable input")
        if last_offset is not None and (offset < last_offset or not generic and offset == last_offset):
            raise ValueError("fixture replay offsets must be ordered")
        last = previous.get(parsed.stream_id)
        if last is not None and (offset <= last[0] or parsed.session_id != last[1]
                or parsed.sequence <= last[2] or parsed.timestamp_s < last[3]):
            raise ValueError("fixture samples must be strictly ordered in one source session per stream")
        if len(previous) >= 2 and parsed.stream_id not in previous:
            raise ValueError("fixture stream bound exceeded")
        previous[parsed.stream_id] = (offset, parsed.session_id, parsed.sequence, parsed.timestamp_s)
        last_offset = offset
        samples.append((offset, message))
    return TrialInputFixture(fixture_id, sha256(document).hexdigest(), len(document),
        raw["provenance"], tuple(samples), raw["schema_version"], canonical(source).decode("utf-8"))
