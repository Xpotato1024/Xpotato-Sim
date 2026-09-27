"""Checkerが実際に評価したjoint configuration / sample列の値identity。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvaluatedJointRoute:
    """Runtime設定のendpointとRobot-owned joint順序の明示対応。"""

    endpoint_id: str
    joint_names: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint_id, str) or not self.endpoint_id or self.endpoint_id != self.endpoint_id.strip():
            raise ValueError("joint route requires a concrete endpoint identity")
        if type(self.joint_names) is not tuple or not self.joint_names:
            raise ValueError("joint route requires ordered joint names")
        if any(not isinstance(name, str) or not name or name != name.strip() for name in self.joint_names):
            raise ValueError("joint route requires concrete joint identities")
        if len(set(self.joint_names)) != len(self.joint_names):
            raise ValueError("joint route names must be unique")


@dataclass(frozen=True, slots=True)
class EvaluatedCandidate:
    """Caller IDではなく、ownerが観測・評価入力から取り出すimmutableな値。"""

    joint_names: tuple[str, ...]
    configurations: tuple[tuple[tuple[float, ...], tuple[float, ...] | None], ...]
    timestamps_s: tuple[float, ...] = ()
    joint_route: EvaluatedJointRoute | None = None


__all__ = ["EvaluatedCandidate", "EvaluatedJointRoute"]
