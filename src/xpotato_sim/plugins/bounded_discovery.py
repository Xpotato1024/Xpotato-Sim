"""Small primitives for bounded first-party package discovery."""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType


class BoundedPluginImportError(RuntimeError):
    """Normalized import failure for a fixed package entry point."""


def direct_child_package_names(namespace: ModuleType) -> tuple[str, ...]:
    """Return sorted, public, direct child packages of one fixed namespace."""

    if not hasattr(namespace, "__path__"):
        raise ValueError("plugin discovery namespace must be a package")
    return tuple(
        sorted(
            item.name
            for item in pkgutil.iter_modules(namespace.__path__)
            if item.ispkg and not item.name.startswith("_")
        )
    )


def import_fixed_entry_module(
    namespace: ModuleType,
    package_name: str,
    *,
    entry_module: str,
    kind: str,
) -> ModuleType:
    """Import only ``<namespace>.<direct-child>.<entry-module>``."""

    module_name = f"{namespace.__name__}.{package_name}.{entry_module}"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            raise BoundedPluginImportError(
                f"{kind} entry point is missing: {module_name}"
            ) from exc
        raise BoundedPluginImportError(
            f"{kind} import failed for {module_name}: {exc}"
        ) from exc
    except Exception as exc:
        raise BoundedPluginImportError(
            f"{kind} import failed for {module_name}: {exc}"
        ) from exc


__all__ = [
    "BoundedPluginImportError",
    "direct_child_package_names",
    "import_fixed_entry_module",
]
