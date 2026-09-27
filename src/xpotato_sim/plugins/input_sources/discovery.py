"""first-party Input Sourceのfixed ``plugin.py`` だけを読むbounded discovery。

module importはdeclaration取得に限定され、serial/browser/file readerを開始しない。
duplicate identityや不正registrationはproduction catalog作成前に拒否する。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from xpotato_sim.plugins.bounded_discovery import (
    BoundedPluginImportError,
    direct_child_package_names,
    import_fixed_entry_module,
)
from xpotato_sim.plugins.input_sources.registration import (
    InputSourcePluginRegistration,
)


INPUT_SOURCE_PLUGIN_ENTRY_MODULE = "plugin"
INPUT_SOURCE_PLUGIN_ENTRY_SYMBOL = "INPUT_SOURCE_PLUGIN"


class InputSourcePluginDiscoveryError(RuntimeError):
    """Fail-closed Input Source Plugin discovery error."""


@dataclass(frozen=True, slots=True)
class InputSourceDiscoveryRoot:
    """Input Source探索を許可するpackage namespace。"""
    namespace: ModuleType

    def __post_init__(self) -> None:
        if not hasattr(self.namespace, "__path__"):
            raise ValueError("input source discovery namespace must be a package")


def _load_registration(
    root: InputSourceDiscoveryRoot,
    package_name: str,
) -> InputSourcePluginRegistration:
    try:
        module = import_fixed_entry_module(
            root.namespace,
            package_name,
            entry_module=INPUT_SOURCE_PLUGIN_ENTRY_MODULE,
            kind="Input Source Plugin",
        )
    except BoundedPluginImportError as exc:
        raise InputSourcePluginDiscoveryError(str(exc)) from exc
    module_name = module.__name__
    if not hasattr(module, INPUT_SOURCE_PLUGIN_ENTRY_SYMBOL):
        raise InputSourcePluginDiscoveryError(
            "Input Source Plugin export is missing: "
            f"{module_name}.{INPUT_SOURCE_PLUGIN_ENTRY_SYMBOL}"
        )
    registration = getattr(module, INPUT_SOURCE_PLUGIN_ENTRY_SYMBOL)
    if not isinstance(registration, InputSourcePluginRegistration):
        raise InputSourcePluginDiscoveryError(
            f"invalid Input Source Plugin registration type for {module_name}"
        )
    return registration


def discover_input_source_plugins(
    root: InputSourceDiscoveryRoot,
) -> tuple[InputSourcePluginRegistration, ...]:
    """Discover direct packages only and fail on every broken candidate."""

    package_names = direct_child_package_names(root.namespace)
    registrations = tuple(
        _load_registration(root, package_name) for package_name in package_names
    )
    identities: set[str] = set()
    for registration in registrations:
        plugin_id = registration.plugin.identity.name
        if plugin_id in identities:
            raise InputSourcePluginDiscoveryError(
                f"duplicate input source plugin registration: {plugin_id!r}"
            )
        identities.add(plugin_id)
    for package_name, registration in zip(
        package_names, registrations, strict=True
    ):
        declared_id = registration.plugin.identity.name
        if declared_id != package_name:
            raise InputSourcePluginDiscoveryError(
                "Input Source Plugin package/declaration identity mismatch: "
                f"package={package_name!r}, declared={declared_id!r}"
            )
    return registrations


def discover_production_input_source_plugins(
) -> tuple[InputSourcePluginRegistration, ...]:
    """Discover only the fixed first-party production namespace."""

    from xpotato_sim.plugins import input_sources

    return discover_input_source_plugins(
        InputSourceDiscoveryRoot(namespace=input_sources)
    )


__all__ = [
    "INPUT_SOURCE_PLUGIN_ENTRY_MODULE",
    "INPUT_SOURCE_PLUGIN_ENTRY_SYMBOL",
    "InputSourceDiscoveryRoot",
    "InputSourcePluginDiscoveryError",
    "discover_input_source_plugins",
    "discover_production_input_source_plugins",
]
