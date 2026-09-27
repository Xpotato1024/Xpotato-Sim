"""Backend viewer bridge compatibility Input Source Plugin."""

from collections.abc import Mapping
from time import monotonic

from xpotato_sim.plugins.input_sources.viewer.source import (
    DEFAULT_VIEWER_INPUT_COMMAND_TIMEOUT_MS,
    DEFAULT_VIEWER_SAFE_ENDPOINT_M,
    ViewerInputSource,
)
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
    InputSourceRuntimeDependencies,
)
from xpotato_sim.schemas import RawInputFrame


def viewer_health(delegate: ViewerInputSource) -> InputSourceHealth:
    status, reason, age_ms, metadata = delegate.health_snapshot()
    return InputSourceHealth(
        InputSourceHealthStatus(status),
        reason=reason,
        age_ms=age_ms,
        metadata=metadata,
    )


class ViewerManagedInputSourceReader:
    """externally-pushed viewer bridgeのnoop start/close lifecycle owner。

    browser/WebSocket acquisitionはviewer側が所有するため、このreaderはI/Oを開始・停止
    しない。read/healthはdelegateのstale/invalid状態を直列に投影し、thread-safeではない。
    """

    def __init__(self, delegate: ViewerInputSource) -> None:
        self._delegate = delegate

    def start(self) -> None:
        """Viewer acquisition is externally pushed; startup performs no I/O."""

    def close(self) -> None:
        """Closing the backend reader performs no browser or network I/O."""

    def read_frame(self) -> RawInputFrame:
        return self._delegate.read_frame()

    def current_health(self) -> InputSourceHealth:
        return viewer_health(self._delegate)

    @property
    def viewer_bridge_capability(self) -> ViewerInputSource:
        return self._delegate


def build_frames(parameters: Mapping[str, object]) -> tuple[RawInputFrame, ...]:
    return (RawInputFrame(source="viewer", timestamp_s=0.0, metadata=dict(parameters["metadata"])),)


def build_reader(parameters: Mapping[str, object], *, runtime_dependencies: InputSourceRuntimeDependencies | None = None) -> ViewerManagedInputSourceReader:
    """初期safe endpointとclockを固定した未接続backend readerを返す。"""

    endpoint = parameters.get("initial_endpoint_m", DEFAULT_VIEWER_SAFE_ENDPOINT_M)
    clock = monotonic if runtime_dependencies is None or runtime_dependencies.clock is None else runtime_dependencies.clock
    delegate = ViewerInputSource(initial_endpoint_m=endpoint, clock=clock)
    return ViewerManagedInputSourceReader(delegate)


__all__ = [
    "DEFAULT_VIEWER_INPUT_COMMAND_TIMEOUT_MS",
    "DEFAULT_VIEWER_SAFE_ENDPOINT_M",
    "ViewerInputSource",
    "ViewerManagedInputSourceReader",
    "viewer_health",
    "build_frames",
    "build_reader",
]
