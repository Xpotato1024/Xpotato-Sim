"""接触sceneと同じmodel/dataを使い、Robotの名前付きjoint viewだけを提供する。"""
from __future__ import annotations

from dataclasses import replace
import xml.etree.ElementTree as ET

from xpotato_sim.runtime.contact.scene import ContactSceneBuildRequest, ContactSceneInstance
from xpotato_sim.runtime.composition.robot_profile import RobotProfile
from xpotato_sim.schemas import JointPositionCommand, MotionCommand, MuJoCoState

PROXY_NAME = "signal_e2e_tool_proxy"
PROXY_RADIUS_M = 0.01


def add_signal_tool_proxy(request: ContactSceneBuildRequest, profile: RobotProfile) -> ContactSceneBuildRequest:
    """Robot assetのprivate copyだけへ名前付き球を追加する。実機形状ではない。"""
    name = profile.endpoint.site_name
    assets = dict(request.assets)
    matches = []
    for path, content in assets.items():
        if not path.endswith(".xml"):
            continue
        tree = ET.fromstring(content)
        if any(g.get("name") == PROXY_NAME for g in tree.iter("geom")):
            raise ValueError("signal tool proxy already exists")
        for body in tree.iter("body"):
            for site in body.findall("site"):
                if site.get("name") == name:
                    matches.append((path, tree, body, site))
    if len(matches) != 1:
        raise ValueError("signal contact fixture requires one declared endpoint site")
    path, tree, body, site = matches[0]
    ET.SubElement(body, "geom", {"name": PROXY_NAME, "type": "sphere",
        "pos": site.get("pos", "0 0 0"), "size": str(PROXY_RADIUS_M), "mass": "0.001",
        "contype": "1", "conaffinity": "1"})
    assets[path] = ET.tostring(tree, encoding="utf-8")
    return replace(request, assets=assets)


class ContactRobotView:
    """独立modelを持たず、pipelineにはRobotのqpos/qvel、logには全sceneを返す。"""

    def __init__(self, instance: ContactSceneInstance, profile: RobotProfile):
        if not isinstance(instance, ContactSceneInstance) or not isinstance(profile, RobotProfile):
            raise TypeError("contact Robot view requires typed scene and profile")
        self.instance = instance
        self.backend = instance.simulator
        self.qpos_addresses, self.dof_addresses = self.backend.bind_joint_position_group(profile.canonical_joint_names)
        if len(self.qpos_addresses) != profile.qpos_dimension or len(self.dof_addresses) != profile.qvel_dimension:
            raise ValueError("contact Robot view dimensions differ from profile")

    @property
    def model(self):
        return self.backend.model

    @property
    def data(self):
        return self.backend.data

    @property
    def last_joint_position_command(self):
        return self.backend.last_joint_position_command

    def apply_joint_position_command(self, command: JointPositionCommand) -> None:
        self.backend.apply_joint_position_command(command)

    def record_motion_command_envelope(self, command: MotionCommand) -> None:
        self.backend.record_motion_command_envelope(command)

    def step(self, dt_s: float) -> None:
        self.backend.step(dt_s)

    def snapshot(self) -> MuJoCoState:
        state = self.backend.snapshot()
        return replace(state, qpos=tuple(state.qpos[i] for i in self.qpos_addresses),
                       qvel=tuple(state.qvel[i] for i in self.dof_addresses))
