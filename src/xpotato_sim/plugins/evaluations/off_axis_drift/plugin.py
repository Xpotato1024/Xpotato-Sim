"""``off_axis_drift/v1`` fixed Evaluation declaration entry point。

importはdeclarationをcatalog symbolへroutingするだけでmetric導出を実行しない。
"""

from .implementation import OFF_AXIS_DRIFT_PLUGIN


EVALUATION_PLUGIN = OFF_AXIS_DRIFT_PLUGIN

__all__ = ["EVALUATION_PLUGIN"]
