"""Input Source declarationをconstruction requestとexecution adapterへ結ぶ契約。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from xpotato_sim.runtime.execution.input_source_adapters import (
    RuntimeInputSourceExecutionAdapter,
)
from xpotato_sim.runtime.experiment.input_source import (
    InputSourcePlugin,
    InputSourceRuntimeDependencies,
)
from xpotato_sim.schemas import RawInputFrame


@dataclass(frozen=True, slots=True)
class InputSourcePluginRequest:
    """validation済みparameterとoffline frame/runtime dependencyのconstruction入力。

    この値の生成だけではserial、browser、filesystem等のacquisitionを開始しない。
    """
    parameters: Mapping[str, object]
    frames: tuple[RawInputFrame, ...]
    loop: bool
    initial_metadata: Mapping[str, object]
    runtime_dependencies: InputSourceRuntimeDependencies | None = None


RequestBuilder = Callable[..., InputSourcePluginRequest]


@dataclass(frozen=True, slots=True)
class InputSourcePluginRegistration:
    """logical plugin、CLI alias、request builder、typed adapterの固定宣言。"""
    plugin: InputSourcePlugin
    cli_aliases: tuple[str, ...]
    request_builder: RequestBuilder
    execution_adapter: RuntimeInputSourceExecutionAdapter

    def __post_init__(self) -> None:
        if not self.cli_aliases:
            raise ValueError("input source registration requires at least one CLI alias")
        if len(set(self.cli_aliases)) != len(self.cli_aliases):
            raise ValueError("input source registration aliases must be unique")
        if not isinstance(self.execution_adapter, RuntimeInputSourceExecutionAdapter):
            raise TypeError(
                "input source registration requires a typed execution adapter"
            )


__all__ = [
    "InputSourcePluginRegistration",
    "InputSourcePluginRequest",
    "RequestBuilder",
]
