"""bounded discoveryから構築するproduction Input Source catalog。

aliasとlogical identityを同一registrationへroutingし、unknown/duplicate/version mismatchを
fail closedにする。source construction/start/read/closeはcatalogの責務ではない。
"""

from __future__ import annotations

from collections.abc import Iterable

from xpotato_sim.plugins.input_sources.registration import InputSourcePluginRegistration
from xpotato_sim.runtime.experiment.contracts import PluginSelection
from xpotato_sim.runtime.experiment.input_source import InputSourcePlugin
from xpotato_sim.runtime.experiment.registry import VersionedPluginRegistry


class InputSourceCatalog:
    """Input Source registrationをaliasとcanonical IDで一意に解決する。"""
    def __init__(self, registrations: Iterable[InputSourcePluginRegistration]) -> None:
        self._registrations = tuple(
            sorted(registrations, key=lambda item: item.plugin.identity.canonical_id)
        )
        self._by_alias: dict[str, InputSourcePluginRegistration] = {}
        for registration in self._registrations:
            for alias in registration.cli_aliases:
                if alias in self._by_alias:
                    raise ValueError(f"duplicate input source CLI alias: {alias!r}")
                self._by_alias[alias] = registration
        self._by_alias = dict(sorted(self._by_alias.items()))
        self._registry = VersionedPluginRegistry(
            (registration.plugin for registration in self._registrations),
            kind="input source plugin",
        )

    @property
    def ids(self) -> tuple[str, ...]:
        return self._registry.ids

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(self._by_alias)

    @property
    def registrations(self) -> tuple[InputSourcePluginRegistration, ...]:
        return self._registrations

    @property
    def registry(self) -> VersionedPluginRegistry[InputSourcePlugin]:
        return self._registry

    def resolve(self, alias: str) -> InputSourcePluginRegistration:
        try:
            return self._by_alias[alias]
        except KeyError as exc:
            raise ValueError(f"unsupported input source: {alias!r}") from exc

    def resolve_plugin(self, selection: PluginSelection) -> InputSourcePlugin:
        return self._registry.resolve(selection)

    def resolve_selection(self, alias: str) -> PluginSelection:
        registration = self.resolve(alias)
        return PluginSelection(
            registration.plugin.identity.name,
            registration.plugin.identity.version,
        )


from xpotato_sim.plugins.input_sources.discovery import (
    InputSourcePluginDiscoveryError,
    discover_production_input_source_plugins,
)


try:
    INPUT_SOURCE_CATALOG = InputSourceCatalog(
        discover_production_input_source_plugins()
    )
except ValueError as exc:
    raise InputSourcePluginDiscoveryError(str(exc)) from exc
def get_input_source_registration(alias: str) -> InputSourcePluginRegistration:
    """CLI aliasからregistrationを解決し、未知aliasを拒否する。"""
    return INPUT_SOURCE_CATALOG.resolve(alias)


def resolve_input_source_plugin(selection: PluginSelection) -> InputSourcePlugin:
    """exact plugin identity/versionを解決し、source lifecycleは開始しない。"""
    return INPUT_SOURCE_CATALOG.resolve_plugin(selection)


__all__ = [
    "INPUT_SOURCE_CATALOG",
    "InputSourceCatalog",
    "get_input_source_registration",
    "resolve_input_source_plugin",
]
