"""``completion_time/v1`` fixed Evaluation declaration entry point。

importはdeclarationをcatalog symbolへroutingするだけでmetric導出を実行しない。
"""

from .implementation import COMPLETION_TIME_PLUGIN


EVALUATION_PLUGIN = COMPLETION_TIME_PLUGIN

__all__ = ["EVALUATION_PLUGIN"]
