"""Output回帰用のsynthetic MuJoCo観測。実機evidenceではない。"""

from dataclasses import replace

import mujoco

from xpotato_sim.runtime.output.safety_gate import compose_physical_output_safety_input
from xpotato_sim.runtime.safety.collision_policy import (
    CollisionPolicy,
    build_mujoco_geometry_inventory,
    evaluate_collision_configuration,
    evaluate_mujoco_collision_configuration,
)
from xpotato_sim.runtime.safety.trajectory_feasibility import JacobianDiagnostic
from xpotato_sim.runtime.safety.evaluated_candidate import EvaluatedJointRoute
from xpotato_sim.schemas import JointPositionCommand
from tests.runtime.test_physical_safety_core import JOINTS, _dynamic_policy, _limits
from tests.schemas.test_physical_output_contract import _endpoint_request


def joint_request(**changes):
    request = replace(
        _endpoint_request(), target_robot_id="fixture-robot",
        command_semantics="joint_position_command/v1",
        command=JointPositionCommand(1.0, (0.0, 0.0)),
    )
    return replace(request, **changes) if changes else request


def observed_safety_input(request, *, joint_names=JOINTS, limits=None, model_id="output-fixture-model"):
    joints = "".join(
        f'<body name="robot_{index}"><joint name="{name}" type="hinge" axis="0 0 1"/>'
        '<inertial pos="0 0 0" mass="1" diaginertia="1 1 1"/>'
        for index, name in enumerate(joint_names)
    )
    # 距離0.05 mのpositive-margin contactを既存contact producerへ渡す。
    # clearance formulaやmissing-pairの意味はテスト側で置き換えない。
    model = mujoco.MjModel.from_xml_string(
        '<mujoco><worldbody>' + joints
        + '<geom name="robot_geom" type="sphere" size="0.1" margin="0.1"/>'
        + '</body>' * len(joint_names)
        + '<body name="environment" pos="0.25 0 0">'
        '<geom name="environment_geom" type="sphere" size="0.1" margin="0.1"/>'
        '</body></worldbody></mujoco>'
    )
    data = mujoco.MjData(model)
    data.qpos[:] = request.command.joint_angles_rad
    inventory = build_mujoco_geometry_inventory(
        model, robot_body_names=tuple(f"robot_{i}" for i in range(len(joint_names))), environment_body_names=("environment",),
    )
    policy = CollisionPolicy("output-fixture-collision", 0.01, 0.01)
    context = evaluate_collision_configuration(
        inventory, (), policy, robot_id=request.target_robot_id,
        model_id=model_id, policy_revision="rev-1", inventory_revision="rev-1",
    ).context
    collision = evaluate_mujoco_collision_configuration(
        model, data, inventory, policy, context, joint_route=EvaluatedJointRoute(request.endpoint_id, joint_names),
    )
    jacobian = JacobianDiagnostic(
        source_id="fixture-jacobian", row_count=3, column_count=3,
        numeric_rank=3, effective_rank=3, minimum_singular_value=0.5,
        condition_number=2.0, evidence_reference="fixture-jacobian-evidence",
    )
    return compose_physical_output_safety_input(
        request, limits if limits is not None else _limits(expected_joint_names=joint_names, robot_id=request.target_robot_id),
        collision, _dynamic_policy(joint_names), jacobian,
    )
