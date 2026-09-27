"""Plugin-owned fast_arm viewer declaration loaded from its serializable SoT."""

from __future__ import annotations

import json

from xpotato_sim.runtime.composition.viewer_robot_declaration import (
    ViewerRobotDeclaration,
    decode_viewer_robot_declaration,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.resources import (
    FAST_ARM_RESOURCE_BINDING_MANIFEST,
    FAST_ARM_VIEWER_DECLARATION_RESOURCE as _VIEWER_RESOURCE,
)
from xpotato_sim.runtime.composition.robot_resource import read_package_resource_bytes
from xpotato_sim.runtime.composition.viewer_package_resource_manifest import (
    validate_viewer_declaration_resource_bindings,
)


FAST_ARM_VIEWER_DECLARATION_RESOURCE = _VIEWER_RESOURCE.logical_identifier

FAST_ARM_VIEWER_DECLARATION: ViewerRobotDeclaration = (
    decode_viewer_robot_declaration(
        json.loads(read_package_resource_bytes(_VIEWER_RESOURCE).decode("utf-8"))
    )
)
validate_viewer_declaration_resource_bindings(
    FAST_ARM_RESOURCE_BINDING_MANIFEST,
    FAST_ARM_VIEWER_DECLARATION,
)


__all__ = [
    "FAST_ARM_VIEWER_DECLARATION",
    "FAST_ARM_VIEWER_DECLARATION_RESOURCE",
]
