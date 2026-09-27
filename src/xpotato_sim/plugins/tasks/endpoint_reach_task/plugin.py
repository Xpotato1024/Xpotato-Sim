"""``endpoint_reach_task/v1`` fixed Task declaration entry point。

importはdeclarationをcatalog symbolへroutingするだけでtask lifecycleを開始しない。
"""

from .implementation import ENDPOINT_REACH_TASK_PLUGIN


TASK_PLUGIN = ENDPOINT_REACH_TASK_PLUGIN

__all__ = ["TASK_PLUGIN"]
