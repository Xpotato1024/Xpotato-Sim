"""既存 ``fast_arm.bundle`` importをadapter ownerへ転送するpublic compatibility facade。

F403はadapterの明示 ``__all__`` を同じsurfaceで公開するために必要である。既存consumerを
canonical adapter pathへ移行し、public contract変更を承認した場合だけ削除できる。
"""

from xpotato_sim.plugins.robots.fast_arm.adapter.bundle import *  # noqa: F403
from xpotato_sim.plugins.robots.fast_arm.adapter.bundle import __all__
