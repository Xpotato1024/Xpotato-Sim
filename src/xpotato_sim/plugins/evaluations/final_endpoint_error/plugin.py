"""``final_endpoint_error/v1`` fixed Evaluation declaration entry point。

importはdeclarationをcatalog symbolへroutingするだけでmetric導出を実行しない。
"""

from .implementation import FINAL_ENDPOINT_ERROR_PLUGIN


EVALUATION_PLUGIN = FINAL_ENDPOINT_ERROR_PLUGIN

__all__ = ["EVALUATION_PLUGIN"]
