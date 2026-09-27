"""``success_within_timeout/v1`` fixed Evaluation declaration entry point。

importはdeclarationをcatalog symbolへroutingするだけでmetric導出を実行しない。
"""

from .implementation import SUCCESS_WITHIN_TIMEOUT_PLUGIN


EVALUATION_PLUGIN = SUCCESS_WITHIN_TIMEOUT_PLUGIN

__all__ = ["EVALUATION_PLUGIN"]
