"""既存diagnostic importをadapter-owned実装へ転送するpublic compatibility facade。

F403はadapterの明示 ``__all__`` を維持するために必要で、consumer移行とpublic contract
変更の承認が完了するまで削除しない。
"""

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.neutral_initial_pose import *  # noqa: F403
from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.neutral_initial_pose import __all__
