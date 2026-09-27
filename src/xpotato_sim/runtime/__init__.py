"""Minimal runtime public facade with catalog-free package initialization."""

from __future__ import annotations

from importlib import import_module


_PUBLIC_EXPORTS = {
    "RuntimeConfig": ("xpotato_sim.runtime.composition.config", "RuntimeConfig"),
    "registered_robot_bundle_ids": (
        "xpotato_sim.plugins.robots.catalog",
        "registered_robot_bundle_ids",
    ),
    "registered_robot_runtime_plugin_ids": (
        "xpotato_sim.plugins.robots.catalog",
        "registered_robot_runtime_plugin_ids",
    ),
    "resolve_robot_bundle": ("xpotato_sim.plugins.robots.catalog", "resolve_robot_bundle"),
    "resolve_robot_runtime": ("xpotato_sim.plugins.robots.catalog", "resolve_robot_runtime"),
    "resolve_robot_runtime_plugin": (
        "xpotato_sim.plugins.robots.catalog",
        "resolve_robot_runtime_plugin",
    ),
}


def __getattr__(name: str) -> object:
    """Resolve the fixed public surface without loading the concrete catalog eagerly."""

    owner = _PUBLIC_EXPORTS.get(name)
    if owner is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = owner
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


__all__ = list(_PUBLIC_EXPORTS)
