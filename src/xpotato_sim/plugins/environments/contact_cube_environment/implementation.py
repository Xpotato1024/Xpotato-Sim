"""R7-H contact cubeのbackend-owned Environment Plugin implementation。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from xpotato_sim.runtime.contact.manifest import (
    CONTACT_ENVIRONMENT_ROLE,
    CONTACT_OBJECT_IDENTITY,
)
from xpotato_sim.runtime.contact.scene import (
    ContactSceneBuildRequest,
    ContactSceneComposer,
    ContactSceneInstance,
)
from xpotato_sim.runtime.experiment.contracts import (
    EnvironmentPlugin,
    EnvironmentRole,
    ParameterContract,
    ParameterField,
    VersionedIdentity,
)


CONTACT_CUBE_ENVIRONMENT_IDENTITY = VersionedIdentity(
    "contact_cube_environment", 1
)


@dataclass(frozen=True, slots=True)
class ContactCubeSceneProvider:
    """typed requestをbackend scene compositionへ渡すprovider。"""

    def compose_scene(self, parameters: Mapping[str, object]) -> ContactSceneInstance:
        if set(parameters) != {"request"}:
            unknown = tuple(sorted(set(parameters) - {"request"}))
            if unknown:
                raise ValueError(f"unknown contact scene parameters: {unknown}")
            raise ValueError("contact scene requires a typed request parameter")
        request = parameters["request"]
        if not isinstance(request, ContactSceneBuildRequest):
            raise TypeError(
                "contact scene request must use ContactSceneBuildRequest"
            )
        if request.manifest.environment.plugin_id != CONTACT_CUBE_ENVIRONMENT_IDENTITY.name:
            raise ValueError(
                "contact cube environment requires its own manifest environment identity"
            )
        if request.manifest.object.identity != CONTACT_OBJECT_IDENTITY:
            raise ValueError(
                "contact cube environment requires the versioned contact cube object"
            )
        return ContactSceneComposer(request).build()

    def reset_scene(self, scene: object) -> None:
        if not isinstance(scene, ContactSceneInstance):
            raise TypeError(
                "contact cube environment reset requires ContactSceneInstance"
            )
        scene.reset()


CONTACT_CUBE_ENVIRONMENT_PLUGIN = EnvironmentPlugin(
    identity=CONTACT_CUBE_ENVIRONMENT_IDENTITY,
    scene_provider=ContactCubeSceneProvider(),
    roles=(
        EnvironmentRole(
            role=CONTACT_ENVIRONMENT_ROLE,
            object_kind="target_object",
            frame="mujoco_world",
            unit="meter",
        ),
    ),
    parameter_contract=ParameterContract(
        (
            ParameterField(
                "request",
                ContactSceneBuildRequest,
                condition_specific=True,
            ),
        )
    ),
    produced_evidence=frozenset(),
    compatible_backend_kinds=frozenset({"mujoco"}),
)


__all__ = [
    "CONTACT_CUBE_ENVIRONMENT_IDENTITY",
    "CONTACT_CUBE_ENVIRONMENT_PLUGIN",
    "ContactCubeSceneProvider",
]
