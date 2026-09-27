"""``contact_outcome/v1`` fixed Evaluation declaration entry point。

このmoduleはimport時に宣言だけを公開し、lifecycleを開始しない。
"""

from .implementation import CONTACT_OUTCOME_PLUGIN


EVALUATION_PLUGIN = CONTACT_OUTCOME_PLUGIN

__all__ = ["EVALUATION_PLUGIN"]
