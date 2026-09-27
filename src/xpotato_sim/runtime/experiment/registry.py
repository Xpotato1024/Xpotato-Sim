"""Deterministic known-ID registries for versioned experiment plugins."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Generic, Protocol, TypeVar

from xpotato_sim.runtime.experiment.contracts import PluginSelection, VersionedIdentity


class _VersionedPlugin(Protocol):
    identity: VersionedIdentity


T = TypeVar("T", bound=_VersionedPlugin)


class VersionedPluginRegistry(Generic[T]):
    def __init__(self, entries: Iterable[T], *, kind: str) -> None:
        values: dict[str, T] = {}
        for entry in entries:
            plugin_id = entry.identity.name
            if plugin_id in values:
                raise ValueError(f"duplicate {kind} registration: {plugin_id!r}")
            values[plugin_id] = entry
        self._kind = kind
        self._values: Mapping[str, T] = MappingProxyType(
            {plugin_id: values[plugin_id] for plugin_id in sorted(values)}
        )

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._values)

    @property
    def entries(self) -> tuple[T, ...]:
        return tuple(self._values.values())

    def resolve(self, selection: PluginSelection) -> T:
        try:
            plugin = self._values[selection.plugin_id]
        except KeyError as exc:
            raise ValueError(
                f"unknown {self._kind} ID {selection.plugin_id!r}; available: {self.ids}"
            ) from exc
        if plugin.identity.version != selection.contract_version:
            raise ValueError(
                f"{self._kind} contract version mismatch for {selection.plugin_id!r}: "
                f"requested v{selection.contract_version}, registered v{plugin.identity.version}"
            )
        return plugin


__all__ = ["VersionedPluginRegistry"]
