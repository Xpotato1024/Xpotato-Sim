"""Gamepadの軸選択・符号を入力取得やカメラから独立して検証する。"""
from __future__ import annotations
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


def _integer_triplet(value: object, label: str) -> tuple[int, int, int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise ValueError(f"{label} must contain exactly three integers")
    if any(type(item) is not int for item in value):
        raise ValueError(f"{label} must contain exactly three integers")
    return (value[0], value[1], value[2])


@dataclass(frozen=True, slots=True)
class GamepadAxisMap:
    axis_indices: tuple[int, int, int]
    axis_signs: tuple[int, int, int]

    def __post_init__(self) -> None:
        indices = _integer_triplet(self.axis_indices, "axis_indices")
        signs = _integer_triplet(self.axis_signs, "axis_signs")
        if any(index < 0 for index in indices) or len(set(indices)) != 3:
            raise ValueError("axis_indices must be distinct non-negative integers")
        if any(sign not in (-1, 1) for sign in signs):
            raise ValueError("axis_signs must contain only -1 or 1")
        object.__setattr__(self, "axis_indices", indices)
        object.__setattr__(self, "axis_signs", signs)


def coerce_gamepad_axis_map(value: object) -> GamepadAxisMap:
    if isinstance(value, GamepadAxisMap):
        return value
    if not isinstance(value, Mapping) or set(value) != {"axis_indices", "axis_signs"}:
        raise ValueError("gamepad_axis_map requires only axis_indices and axis_signs")
    return GamepadAxisMap(value["axis_indices"], value["axis_signs"])


def apply_gamepad_axis_map(axes: Sequence[float], config: GamepadAxisMap) -> tuple[float, float, float]:
    # 明示選択した軸の欠落は中立入力ではなく、非対応のsampleとして拒否する。
    if max(config.axis_indices) >= len(axes):
        raise ValueError("gamepad sample is missing an explicitly selected axis")
    return tuple(axes[index] * sign for index, sign in zip(config.axis_indices, config.axis_signs, strict=True))
