"""``replay_mapping/v1`` のfixed Mapping declaration entry point。

importはreplay command semanticを既存implementationへroutingするだけで、execution
strategyを開始しない。
"""

from .implementation import REPLAY_CONTROL_MAPPING_PLUGIN


CONTROL_MAPPING_PLUGIN = REPLAY_CONTROL_MAPPING_PLUGIN

__all__ = ["CONTROL_MAPPING_PLUGIN"]
