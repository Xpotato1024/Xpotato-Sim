"""既存synthetic fixtureを使う有界のdecoder性質検査。物理実験ではない。"""
from copy import deepcopy
import json
from pathlib import Path

from hypothesis import given, settings, strategies as st
import pytest

from xpotato_sim.runtime.contact.log import ContactTaskLogError, decode_contact_task_log
from xpotato_sim.runtime.contact.manifest import (
    ContactManifestDecodeError, decode_contact_manifest, encode_contact_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "apps/mujoco-viewer/tests/fixtures/contact-cube-v1-demo.jsonl"
GENERATED = settings(max_examples=50, derandomize=True, database=None, deadline=None)


def seed_log():
    return decode_contact_task_log(FIXTURE.read_bytes())


@GENERATED
@given(mass=st.floats(min_value=1e-9, max_value=1e6, allow_nan=False, allow_infinity=False),
       rgba=st.tuples(*(st.floats(min_value=0., max_value=1., allow_nan=False, allow_infinity=False) for _ in range(4))))
def test_manifest_canonical_roundtrip_and_mapping_order(mass, rgba):
    document = deepcopy(seed_log().header.context.manifest.to_document())
    document["scene"]["object"]["mass_kg"] = mass
    document["scene"]["object"]["material"]["rgba"] = list(rgba)
    decoded = decode_contact_manifest(document)
    assert decoded.object.mass_kg == mass
    assert decoded.object.material.rgba == rgba
    encoded = encode_contact_manifest(decoded)
    assert encode_contact_manifest(decode_contact_manifest(encoded)) == encoded
    reordered = dict(reversed(tuple(document.items())))
    assert encode_contact_manifest(decode_contact_manifest(reordered)) == encoded


@GENERATED
@given(mass=st.one_of(st.booleans(), st.floats(max_value=0., allow_nan=False, allow_infinity=False),
                      st.sampled_from([float("nan"), float("inf"), -float("inf")])))
def test_manifest_invalid_mass_is_never_normalized_into_valid_input(mass):
    document = deepcopy(seed_log().header.context.manifest.to_document())
    document["scene"]["object"]["mass_kg"] = mass
    with pytest.raises(ContactManifestDecodeError):
        decode_contact_manifest(document)


@GENERATED
@given(data=st.data())
def test_log_canonical_bytes_roundtrip_and_reject_noncanonical_order(data):
    encoded = seed_log().to_jsonl()
    documents = [json.loads(line) for line in encoded.splitlines()]
    for document in documents:
        order = data.draw(st.permutations(tuple(document)))
        reordered = {key: document[key] for key in order}
        document.clear()
        document.update(reordered)
    raw = ("\n".join(json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")) for document in documents) + "\n").encode("utf-8")
    if raw == encoded:
        assert decode_contact_task_log(raw).to_jsonl() == encoded
    else:
        with pytest.raises(ContactTaskLogError, match="not canonical JSON"):
            decode_contact_task_log(raw)
    canonical = ("\n".join(json.dumps(document, ensure_ascii=False, allow_nan=False,
                 sort_keys=True, separators=(",", ":")) for document in documents) + "\n").encode("utf-8")
    assert canonical == encoded
    assert decode_contact_task_log(canonical).to_jsonl() == encoded
