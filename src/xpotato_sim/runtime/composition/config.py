from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from xpotato_sim.runtime.experiment.contracts import PluginSelection


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Runtime configuration for runtime composition."""

    dt_s: float = 1.0 / 60.0
    robot_profile_id: str | None = None
    robot_logical_version: int = 1
    mujoco_model_path: Path | None = None
    joint_limit_config_path: Path | None = None

    def __post_init__(self) -> None:
        if self.robot_profile_id is not None and not self.robot_profile_id:
            raise ValueError("robot_profile_id must be a non-empty string when supplied")
        if type(self.robot_logical_version) is not int or self.robot_logical_version < 1:
            raise ValueError("robot_logical_version must be a positive integer")
        if self.mujoco_model_path is not None:
            object.__setattr__(self, "mujoco_model_path", Path(self.mujoco_model_path))
        if self.joint_limit_config_path is not None:
            object.__setattr__(self, "joint_limit_config_path", Path(self.joint_limit_config_path))

    @property
    def robot_selection(self) -> PluginSelection | None:
        if self.robot_profile_id is None:
            return None
        return PluginSelection(self.robot_profile_id, self.robot_logical_version)
