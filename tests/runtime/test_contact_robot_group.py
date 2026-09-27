"""freejointが先行するsceneでscalar command groupの独立性を確認する。"""
from __future__ import annotations
import numpy as np
import pytest
from xpotato_sim.mujoco_backend.simulator import HeadlessMuJoCoSimulator
from xpotato_sim.schemas import JointPositionCommand

XML = b"""<mujoco><option gravity="0 0 0"/><worldbody>
<body name="free" pos="0 0 2"><freejoint name="object_free"/><geom type="sphere" size=".03"/></body>
<body name="r" pos="0 0 0"><joint name="slide_x" type="slide" axis="1 0 0"/><geom type="sphere" size=".02"/>
<body name="c" pos="0 0 .1"><joint name="hinge_y" axis="0 1 0"/><geom type="sphere" size=".01"/></body></body>
</worldbody></mujoco>"""

def model():
    return HeadlessMuJoCoSimulator.from_xml_resources(XML,assets={},logical_model_path='group-test.xml')

def test_command_group_preserves_uncommanded_freejoint_velocity():
    sim=model()
    q,d=sim.bind_joint_position_group(('hinge_y','slide_x'))
    assert q==(8,7) and d==(7,6)
    sim.data.qvel[0]=.25
    original=sim.data.qpos[:7].copy()
    command=JointPositionCommand(timestamp_s=0.0,joint_angles_rad=(.2,.3))
    sim.apply_joint_position_command(command)
    sim.step(.01)
    assert tuple(sim.data.qpos[i] for i in q)==(.2,.3)
    assert sim.data.qvel[0]==pytest.approx(.25)
    assert sim.data.qpos[0]==pytest.approx(original[0]+.0025)
    assert len(sim.snapshot().qpos)==9
    assert sim.last_joint_position_command is command

@pytest.mark.parametrize('names', ((),('missing',),('object_free',),('slide_x','slide_x')))
def test_invalid_named_group_does_not_mutate_model(names):
    sim=model(); initial=sim.data.qpos.copy()
    with pytest.raises(ValueError): sim.bind_joint_position_group(names)
    assert np.array_equal(sim.data.qpos,initial) and sim._command_group is None

def test_group_cannot_be_rebound_or_changed_after_request():
    sim=model(); sim.bind_joint_position_group(('slide_x','hinge_y'))
    with pytest.raises(ValueError): sim.bind_joint_position_group(('hinge_y','slide_x'))
    sim.apply_joint_position_command(JointPositionCommand(timestamp_s=0.,joint_angles_rad=(0.,0.)))
    with pytest.raises(ValueError): sim.bind_joint_position_group(('slide_x','hinge_y'))

def test_wrong_command_length_is_not_sliced():
    sim=model(); sim.bind_joint_position_group(('slide_x','hinge_y'))
    sim.apply_joint_position_command(JointPositionCommand(timestamp_s=0.,joint_angles_rad=(0.,)))
    with pytest.raises(ValueError,match='length'):sim.step(.01)
    assert sim.snapshot().frame_index==0
