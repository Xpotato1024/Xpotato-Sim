"""Robot-independent MuJoCo model reference contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResolvedModelReference:
    role: str
    kind: str
    name: str

    def __post_init__(self) -> None:
        if not self.role or self.role != self.role.strip():
            raise ValueError("MuJoCo model reference role must not be empty")
        if self.kind not in {"site", "body"}:
            raise ValueError(
                f"unsupported MuJoCo model reference kind: {self.kind!r}"
            )
        if not self.name or self.name != self.name.strip():
            raise ValueError("MuJoCo model reference name must not be empty")


__all__ = ["ResolvedModelReference"]
