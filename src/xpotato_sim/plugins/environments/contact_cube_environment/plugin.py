"""``contact_cube_environment/v1`` fixed Environment declaration entry point。

importはdeclarationをcatalog symbolへroutingする。scene loadはtyped requestを受けた
``compose_scene``の中だけで行い、import時にMuJoCo lifecycleやexternal I/Oを開始しない。
"""

from .implementation import CONTACT_CUBE_ENVIRONMENT_PLUGIN


ENVIRONMENT_PLUGIN = CONTACT_CUBE_ENVIRONMENT_PLUGIN

__all__ = ["ENVIRONMENT_PLUGIN"]
