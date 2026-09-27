from __future__ import annotations

from dataclasses import fields

from xpotato_sim.schemas import BodyTransform, MuJoCoState, SiteTransform


def test_mujoco_state_contract_fields_and_units() -> None:
    body = BodyTransform(
        name="base_link",
        position_m=(1.0, 2.0, 3.0),
        quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
    )
    site = SiteTransform(
        name="tip",
        position_m=(4.0, 5.0, 6.0),
        quaternion_wxyz=(0.5, 0.5, 0.5, 0.5),
    )
    state = MuJoCoState(
        frame_index=7,
        time_s=0.125,
        qpos=(0.1, 0.2, 0.3),
        qvel=(0.4, 0.5, 0.6),
        bodies=(body,),
        sites=(site,),
        target_position_m=(0.7, 0.8, 0.9),
        metadata={"source": "unit-test"},
    )

    assert [field.name for field in fields(MuJoCoState)] == [
        "frame_index",
        "time_s",
        "qpos",
        "qvel",
        "bodies",
        "sites",
        "target_position_m",
        "metadata",
    ]
    assert state.frame_index == 7
    assert state.time_s == 0.125
    assert state.qpos == (0.1, 0.2, 0.3)
    assert state.qvel == (0.4, 0.5, 0.6)
    assert state.bodies == (body,)
    assert state.sites == (site,)
    assert state.target_position_m == (0.7, 0.8, 0.9)
    assert len(state.bodies[0].position_m) == 3
    assert len(state.bodies[0].quaternion_wxyz) == 4
    assert len(state.sites[0].position_m) == 3
    assert len(state.sites[0].quaternion_wxyz) == 4
