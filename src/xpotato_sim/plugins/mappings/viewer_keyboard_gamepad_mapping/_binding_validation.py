"""同じMapping内のstick bindingに共通する構造検証。状態機械は所有しない。"""
from __future__ import annotations

from collections.abc import Sequence


def integer_pair(value: object, label: str) -> tuple[int, int]:
    """boolを含まない整数2要素を、順序を保ったtupleへ検証する。"""
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes))
            or len(value) != 2 or any(type(v) is not int for v in value)):
        raise ValueError(f"{label} requires two integers")
    return (value[0], value[1])
