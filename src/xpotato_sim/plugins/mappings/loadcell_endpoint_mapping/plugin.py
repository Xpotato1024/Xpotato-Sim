"""``loadcell_endpoint_mapping/v1`` のfixed Mapping declaration entry point。

loadcell-to-endpoint contractを既存implementationへroutingし、import時にsourceやRobotの
lifecycleを開始しない。
"""

from .implementation import LOADCELL_ENDPOINT_MAPPING_PLUGIN


CONTROL_MAPPING_PLUGIN = LOADCELL_ENDPOINT_MAPPING_PLUGIN

__all__ = ["CONTROL_MAPPING_PLUGIN"]
