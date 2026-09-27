from __future__ import annotations

from xpotato_sim.schemas import (
    BodyTransform,
    InputIntent,
    JointCommand,
    MotionCommand,
    MuJoCoState,
    RawInputFrame,
    RenderState,
    SiteTransform,
    TargetCommand,
)


def test_schema_defaults_and_independent_metadata() -> None:
    raw = RawInputFrame(source="keyboard", timestamp_s=1.5)
    intent = InputIntent(source="keyboard", timestamp_s=1.5)
    target = TargetCommand()
    joint = JointCommand()
    motion = MotionCommand(timestamp_s=2.0)
    body = BodyTransform(
        name="arm",
        position_m=(0.0, 0.0, 0.0),
        quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
    )
    site = SiteTransform(
        name="tip",
        position_m=(0.0, 0.0, 0.0),
        quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
    )
    state = MuJoCoState(frame_index=0, time_s=0.0)
    render = RenderState(frame_index=0, time_s=0.0)

    assert raw.source == "keyboard"
    assert intent.source == "keyboard"
    assert intent.values == ()
    assert target.delta_m == (0.0, 0.0, 0.0)
    assert joint.joint_angles_rad == ()
    assert motion.target is None
    assert body.name == "arm"
    assert site.name == "tip"
    assert state.frame_index == 0
    assert render.time_s == 0.0

    raw.metadata["raw"] = True
    intent.metadata["intent"] = True
    motion.metadata["motion"] = True
    state.metadata["state"] = True
    render.metadata["render"] = True

    assert "raw" not in RawInputFrame(source="keyboard", timestamp_s=1.5).metadata
    assert "intent" not in InputIntent(source="keyboard", timestamp_s=1.5).metadata
    assert "motion" not in MotionCommand(timestamp_s=2.0).metadata
    assert "state" not in MuJoCoState(frame_index=0, time_s=0.0).metadata
    assert "render" not in RenderState(frame_index=0, time_s=0.0).metadata
