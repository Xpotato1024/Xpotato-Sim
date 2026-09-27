"""``free_space_environment/v1`` fixed Environment declaration entry point。

importはdeclarationをcatalog symbolへroutingするだけでscene lifecycleを開始しない。
"""

from .implementation import FREE_SPACE_ENVIRONMENT_PLUGIN


ENVIRONMENT_PLUGIN = FREE_SPACE_ENVIRONMENT_PLUGIN

__all__ = ["ENVIRONMENT_PLUGIN"]
