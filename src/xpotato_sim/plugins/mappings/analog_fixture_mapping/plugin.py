"""``analog_fixture_mapping/v1`` のfixed Mapping declaration entry point。

importは既存declarationをcatalog symbolへroutingするだけでalgorithmを実行しない。
"""

from .implementation import ANALOG_FIXTURE_CONTROL_MAPPING_PLUGIN


CONTROL_MAPPING_PLUGIN = ANALOG_FIXTURE_CONTROL_MAPPING_PLUGIN

__all__ = ["CONTROL_MAPPING_PLUGIN"]
