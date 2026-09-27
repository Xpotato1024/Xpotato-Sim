"""既存 ``fast_arm.model_contract`` importをadapter ownerへ転送するpublic facade。

F403はadapterの明示 ``__all__`` を維持するために必要で、consumer移行とpublic contract
変更の承認が完了するまで削除しない。
"""

from xpotato_sim.plugins.robots.fast_arm.adapter.model_contract import *  # noqa: F403
from xpotato_sim.plugins.robots.fast_arm.adapter.model_contract import __all__
