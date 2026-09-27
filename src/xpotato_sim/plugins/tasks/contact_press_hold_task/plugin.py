"""``contact_press_hold_task/v1`` fixed Task declaration entry point。

このmoduleはimport時に宣言だけを公開し、lifecycleを開始しない。
"""

from .implementation import CONTACT_PRESS_HOLD_TASK_PLUGIN


TASK_PLUGIN = CONTACT_PRESS_HOLD_TASK_PLUGIN

__all__ = ["TASK_PLUGIN"]
