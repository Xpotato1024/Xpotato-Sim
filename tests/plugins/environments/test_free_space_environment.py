from __future__ import annotations

import pytest

from xpotato_sim.plugins.environments.free_space_environment import (
    FREE_SPACE_ENVIRONMENT_PLUGIN,
)
from xpotato_sim.plugins.environments.free_space_environment.implementation import (
    FreeSpaceSceneCondition,
)
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity


def test_free_space_environment_is_explicit_and_side_effect_free() -> None:
    plugin = FREE_SPACE_ENVIRONMENT_PLUGIN

    assert plugin.identity == VersionedIdentity("free_space_environment", 1)
    assert plugin.compatible_backend_kinds == frozenset({"mujoco"})
    assert plugin.roles == ()
    assert plugin.produced_evidence == frozenset()
    condition = plugin.scene_provider.compose_scene({})
    assert condition == FreeSpaceSceneCondition(
        uses_robot_base_scene=True,
        task_objects=(),
        contact_required=False,
    )
    plugin.scene_provider.reset_scene(condition)


def test_free_space_environment_rejects_parameters_and_foreign_reset_state() -> None:
    provider = FREE_SPACE_ENVIRONMENT_PLUGIN.scene_provider
    with pytest.raises(ValueError, match="does not accept parameters"):
        provider.compose_scene({"fallback": True})
    with pytest.raises(TypeError, match="requires its scene condition"):
        provider.reset_scene(object())
