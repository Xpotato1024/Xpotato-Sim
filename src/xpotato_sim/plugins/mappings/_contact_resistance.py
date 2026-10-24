"""world速度の接近成分だけを変える純粋なcommand Mapping。状態・取得・積分は持たない。"""
from math import fsum, hypot, isfinite

from xpotato_sim.schemas.coordinated import vector


def approach_velocity(velocity, normal, *, gain):
    """m/sと単位法線を受け、離脱・接線・ゼロを維持する。gainは無次元。"""
    velocity = vector(velocity, 3, "world velocity")
    normal = vector(normal, 3, "approach normal")
    if abs(hypot(*normal) - 1.) > 1e-8:
        raise ValueError("unit approach normal required")
    if type(gain) not in (int, float) or not isfinite(gain) or not 0 <= gain <= 1:
        raise ValueError("gain must be within zero and one")
    approach = max(0., fsum(v*n for v, n in zip(velocity, normal, strict=True)))
    result = tuple(v - (1. - gain)*approach*n for v, n in zip(velocity, normal, strict=True))
    if not all(isfinite(v) for v in result):
        raise ValueError("nonfinite mapped velocity")
    return result


def resistance_gain(load_n, *, half_gain_load_n, minimum_gain):
    """圧縮法線荷重Nに対する非増加係数。表示clamp値は入力しない。"""
    if any(type(v) not in (int, float) or not isfinite(v) for v in (load_n, half_gain_load_n, minimum_gain)):
        raise ValueError("finite resistance condition required")
    if load_n < 0 or half_gain_load_n <= 0 or not 0 <= minimum_gain <= 1:
        raise ValueError("invalid resistance condition")
    return max(minimum_gain, 1. / (1. + load_n / half_gain_load_n))
