"""Serializable viewer declaration for the test-only fixture robot."""

from __future__ import annotations

import json
from pathlib import Path

from xpotato_sim.runtime.composition.viewer_robot_declaration import decode_viewer_robot_declaration


_RESOURCE_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_VIEWER_DECLARATION = decode_viewer_robot_declaration(
    json.loads(
        (
            _RESOURCE_ROOT
            / "assets"
            / "mujoco"
            / "fixture_bot"
            / "viewer-profile.json"
        ).read_text(encoding="utf-8")
    )
)


__all__ = ["FIXTURE_VIEWER_DECLARATION"]
