"""双腕Viewer profileを既存application選択へ明示的に追加する。"""
from __future__ import annotations

import json
from math import cos, pi, sin

import pytest

from xpotato_sim.runtime.composition.coordinated_viewer_profile import (
    CoordinatedViewerProfile,
    decode_coordinated_viewer_profile,
    list_coordinated_viewer_profiles,
    load_coordinated_viewer_profile,
    override_coordinated_viewer_profile,
)
from xpotato_sim.runtime.composition.launch_profile import LaunchProfile
from xpotato_sim.runtime.runners.application import (
    application_url,
    list_application_profiles,
    load_application_profile,
)


def test_shipped_profile_selects_two_arms_only_when_explicit():
    assert "fast-arm-bimanual-gamepad" in list_coordinated_viewer_profiles()
    profile = load_coordinated_viewer_profile("fast-arm-bimanual-gamepad")
    assert isinstance(profile, CoordinatedViewerProfile)
    assert profile.assembly.arm_ids == ("left", "right")
    assert dict(profile.side_to_arm) == {"left": "left", "right": "right"}
    w, x = cos(pi / 12), sin(pi / 12)
    assert profile.assembly.instances[0].quaternion_wxyz == pytest.approx((w, x, 0, 0))
    assert profile.assembly.instances[1].quaternion_wxyz == pytest.approx((w, -x, 0, 0))


def test_application_profile_registry_preserves_single_arm_profiles():
    names = list_application_profiles()
    assert "sim-gamepad" in names
    assert "fast-arm-bimanual-gamepad" in names
    assert isinstance(load_application_profile("sim-gamepad"), LaunchProfile)
    coordinated = load_application_profile("fast-arm-bimanual-gamepad")
    assert isinstance(coordinated, CoordinatedViewerProfile)
    assert "inputProvider=gamepad%2Fv1" in application_url(coordinated)
    assert "inputStartup=scene" in application_url(coordinated)
    assert "inputStartup" not in application_url(load_application_profile("sim-gamepad"))


def test_override_changes_only_web_surface():
    profile = load_coordinated_viewer_profile("fast-arm-bimanual-gamepad")
    changed = override_coordinated_viewer_profile(
        profile, web_port=5198, backend_port=8798, open_browser=False
    )
    assert changed.web_port == 5198
    assert changed.backend_port == 8798
    assert changed.open_browser is False
    assert changed.assembly == profile.assembly
    assert changed.mapping_parameters == profile.mapping_parameters


def test_profile_decoder_rejects_incomplete_binding_unknown_field_and_nonloopback():
    profile = load_coordinated_viewer_profile("fast-arm-bimanual-gamepad")
    raw = json.loads(profile.document_json)

    incomplete = json.loads(profile.document_json)
    incomplete["side_to_arm"] = {"left": "left", "right": "left"}
    with pytest.raises(ValueError, match="cover both"):
        decode_coordinated_viewer_profile(
            json.dumps(incomplete).encode(), source_path=profile.source_path
        )

    unknown = json.loads(profile.document_json)
    unknown["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        decode_coordinated_viewer_profile(
            json.dumps(unknown).encode(), source_path=profile.source_path
        )

    raw["web"]["host"] = "192.0.2.1"
    with pytest.raises(ValueError, match="loopback"):
        decode_coordinated_viewer_profile(
            json.dumps(raw).encode(), source_path=profile.source_path
        )
