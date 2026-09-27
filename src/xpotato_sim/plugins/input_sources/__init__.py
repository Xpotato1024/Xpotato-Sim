"""First-party versioned input source plugins."""

from xpotato_sim.plugins.input_sources.catalog import (
    INPUT_SOURCE_CATALOG,
    get_input_source_registration,
    resolve_input_source_plugin,
)

__all__ = [
    "INPUT_SOURCE_CATALOG",
    "get_input_source_registration",
    "resolve_input_source_plugin",
]
