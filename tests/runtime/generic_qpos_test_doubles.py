from __future__ import annotations

from collections.abc import Sequence

from xpotato_sim.runtime.safety.qpos_feasibility import (
    QposFeasibilityDiagnostic,
    QposFeasibilityResult,
)
from xpotato_sim.schemas import JointCommand, MotionCommand


class RejectingGenericQposGuard:
    """Metadata-free rejecting guard used to test the generic control contract."""

    def evaluate(
        self,
        motion_command: MotionCommand,
        *,
        current_qpos_rad: Sequence[float],
    ) -> QposFeasibilityResult:
        forbidden_keys = {
            "qpos_feasibility_rejected",
            "qpos_rejection_reason",
            "qpos_limit_violations",
            "qpos_limit_status",
        }
        metadata = {
            key: value
            for key, value in motion_command.metadata.items()
            if key not in forbidden_keys
        }
        current_qpos = tuple(float(value) for value in current_qpos_rad)
        held_command = MotionCommand(
            timestamp_s=motion_command.timestamp_s,
            target=None,
            joint=JointCommand(joint_angles_rad=current_qpos),
            metadata=metadata,
        )
        return QposFeasibilityResult(
            motion_command=held_command,
            accepted=False,
            action="hold",
            candidate_qpos_rad=None
            if motion_command.joint is None
            else tuple(float(value) for value in motion_command.joint.joint_angles_rad),
            diagnostics=(
                QposFeasibilityDiagnostic(
                    code="generic_test_rejection",
                    attributes=(("action", "hold"),),
                ),
            ),
        )
