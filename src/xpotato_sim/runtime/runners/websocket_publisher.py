from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from functools import partial

from xpotato_sim.runtime.experiment.contracts import PluginSelection, VersionedIdentity

from xpotato_sim.plugins.input_sources.catalog import get_input_source_registration
from xpotato_sim.plugins.input_sources.programmed_target import build_sweep_x_input_source
from xpotato_sim.runtime.composition.concrete_mujoco_pipeline import DEFAULT_CONCRETE_TARGET_POSITION_M, build_concrete_mujoco_pipeline
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.runtime.control.input_step_diagnostics import project_sweep_x_replay_state
from xpotato_sim.runtime.control.viewer_control_ingress import (
    build_viewer_input_source,
    ingest_viewer_control_message_json,
    ingest_viewer_control_message,
)
from xpotato_sim.runtime.execution.input_step_loop import (
    build_runtime_input_source_step_loop_plan,
    run_runtime_input_source_step_loop,
)
from xpotato_sim.runtime.execution.live_timing import (
    AbsoluteDeadlinePacer,
    LiveRuntimeTimingMetrics,
)
from xpotato_sim.runtime.runners.live_websocket_delivery import (
    LiveLatestStateWebSocketPublisher,
)
from xpotato_sim.schemas import RawInputFrame
from xpotato_sim.transport import WebSocketPublisherServer, WebSocketStatePublisher

DEFAULT_WEBSOCKET_PUBLISHER_HOST = "127.0.0.1"
DEFAULT_WEBSOCKET_PUBLISHER_PORT = 8766
DEFAULT_WEBSOCKET_PUBLISHER_STEPS = 1
DEFAULT_WEBSOCKET_PUBLISHER_DT_S = 1.0 / 60.0
DEFAULT_WEBSOCKET_PUBLISHER_INTERVAL_S = 0.0
DEFAULT_WEBSOCKET_PUBLISHER_GRACE_PERIOD_S = 0.05
SUPPORTED_WEBSOCKET_PUBLISHER_PRESETS = ("sweep_x",)


def _default_replay_frame() -> RawInputFrame:
    return RawInputFrame(
        source="replay",
        timestamp_s=0.0,
        metadata={
            "preset": "r6-c-p1-default",
            "target_position_m": DEFAULT_CONCRETE_TARGET_POSITION_M,
            "desired_endpoint_m": DEFAULT_CONCRETE_TARGET_POSITION_M,
        },
    )


def _sweep_x_replay_frames(steps: int) -> tuple[RawInputFrame, ...]:
    source = build_sweep_x_input_source(initial_position_m=DEFAULT_CONCRETE_TARGET_POSITION_M, loop=False)
    return tuple(source.read_frame() for _ in range(steps))


def _validate_host(host: str) -> None:
    if not host:
        raise ValueError("host must not be empty")


def _validate_port(port: int) -> None:
    if port < 1 or port > 65535:
        raise ValueError("port must be in the range 1..65535")


def _validate_steps(steps: int) -> None:
    if steps < 1:
        raise ValueError("steps must be a positive integer")


def _validate_dt_s(dt_s: float) -> None:
    if dt_s <= 0.0:
        raise ValueError("dt_s must be positive")


def _validate_interval_s(interval_s: float) -> None:
    if interval_s < 0.0:
        raise ValueError("interval_s must be non-negative")


def _validate_grace_period_s(grace_period_s: float) -> None:
    if grace_period_s < 0.0:
        raise ValueError("grace_period_s must be non-negative")


def _log(message: str) -> None:
    print(message, flush=True)


async def _run_input_source_websocket_publisher_async(
    *,
    host: str,
    port: int,
    steps: int,
    dt_s: float,
    interval_s: float,
    grace_period_s: float,
    preset: str | None,
    input_source: str,
    robot_profile_id: str,
    robot_logical_version: int = 1,
    control_mapping_selection: PluginSelection | None = None,
    control_mapping_parameters: Mapping[str, object] | None = None,
    command_semantics_route_selection: VersionedIdentity | None = None,
    viewer_provider_id: str | None = None,
    on_ready: Callable[[], None] | None = None,
) -> None:
    runtime_config = RuntimeConfig(
        dt_s=dt_s,
        robot_profile_id=robot_profile_id,
        robot_logical_version=robot_logical_version,
    )
    registration = get_input_source_registration(input_source)
    viewer_input_source = None
    on_message = None
    if registration.execution_adapter.uses_viewer_endpoint_compatibility:
        viewer_input_source = build_viewer_input_source()

        def handle_viewer_message(message: str) -> None:
            assert viewer_input_source is not None
            if viewer_provider_id is None:
                ingest_viewer_control_message_json(viewer_input_source, message)
            else:
                ingest_viewer_control_message(viewer_input_source, message, expected_provider_id=viewer_provider_id)

        on_message = handle_viewer_message

    server_kwargs = {"host": host, "port": port}
    if on_message is not None:
        server_kwargs["on_message"] = on_message
    async with WebSocketPublisherServer(**server_kwargs) as server:
        _log(f"serving on ws://{server.host}:{server.bound_port}")
        if on_ready is not None:
            on_ready()
        _log(f"Waiting for viewer during grace period ({grace_period_s:.2f}s)")

        has_client = await server.wait_for_client(timeout_s=grace_period_s)
        if not has_client:
            _log("No viewer connected during grace period; no payloads published.")
            _log("Completed without publishing because no viewer connected.")
            return

        _log("Viewer connected; publishing started.")
        selection = select_runtime_input_source(
            input_source,
            steps=steps,
            preset=preset,
            control_mapping_selection=control_mapping_selection,
            control_mapping_parameters=control_mapping_parameters,
            command_semantics_route_selection=command_semantics_route_selection,
        )

        if viewer_input_source is not None:
            timing_metrics = LiveRuntimeTimingMetrics()
            pacer = (
                AbsoluteDeadlinePacer(interval_s, metrics=timing_metrics)
                if interval_s > 0.0
                else None
            )
            async with LiveLatestStateWebSocketPublisher(server) as publisher:
                plan = build_runtime_input_source_step_loop_plan(
                    selection,
                    config=runtime_config,
                    publisher=publisher,
                    viewer_input_source=viewer_input_source,
                )
                await run_runtime_input_source_step_loop(
                    plan,
                    steps=steps,
                    dt_s=dt_s,
                    interval_s=interval_s,
                    pacer=pacer,
                    timing_metrics=timing_metrics,
                    collect_records=False,
                )
                await publisher.drain()
                delivery_summary = publisher.summary().to_dict()
            _log(
                "live runtime timing summary: "
                + json.dumps(
                    {
                        **timing_metrics.summary(dt_s=dt_s).to_dict(),
                        **delivery_summary,
                    },
                    sort_keys=True,
                )
            )
        else:
            plan = build_runtime_input_source_step_loop_plan(
                selection,
                config=runtime_config,
                publisher=WebSocketStatePublisher(server),
            )
            await run_runtime_input_source_step_loop(
                plan,
                steps=steps,
                dt_s=dt_s,
                interval_s=interval_s,
            )

        _log(f"Completed after publishing {steps} frame(s).")


def run_input_source_websocket_publisher(
    *,
    input_source: str,
    host: str = DEFAULT_WEBSOCKET_PUBLISHER_HOST,
    port: int = DEFAULT_WEBSOCKET_PUBLISHER_PORT,
    steps: int = DEFAULT_WEBSOCKET_PUBLISHER_STEPS,
    dt_s: float = DEFAULT_WEBSOCKET_PUBLISHER_DT_S,
    interval_s: float = DEFAULT_WEBSOCKET_PUBLISHER_INTERVAL_S,
    grace_period_s: float = DEFAULT_WEBSOCKET_PUBLISHER_GRACE_PERIOD_S,
    preset: str | None = None,
    robot_profile_id: str = "fast_arm",
    robot_logical_version: int = 1,
    control_mapping_selection: PluginSelection | None = None,
    control_mapping_parameters: Mapping[str, object] | None = None,
    command_semantics_route_selection: VersionedIdentity | None = None,
    viewer_provider_id: str | None = None,
    on_ready: Callable[[], None] | None = None,
) -> None:
    _validate_host(host)
    _validate_port(port)
    _validate_steps(steps)
    _validate_dt_s(dt_s)
    _validate_interval_s(interval_s)
    _validate_grace_period_s(grace_period_s)
    get_input_source_registration(input_source)
    if viewer_provider_id not in (None, "keyboard/v1", "gamepad/v1"):
        raise ValueError("unknown viewer input provider")
    if on_ready is not None and not callable(on_ready):
        raise TypeError("on_ready must be callable")
    asyncio.run(
        _run_input_source_websocket_publisher_async(
            host=host,
            port=port,
            steps=steps,
            dt_s=dt_s,
            interval_s=interval_s,
            grace_period_s=grace_period_s,
            preset=preset,
            input_source=input_source,
            robot_profile_id=robot_profile_id,
            robot_logical_version=robot_logical_version,
            control_mapping_selection=control_mapping_selection,
            control_mapping_parameters=control_mapping_parameters,
            command_semantics_route_selection=command_semantics_route_selection,
            viewer_provider_id=viewer_provider_id,
            on_ready=on_ready,
        )
    )


async def _run_replay_mujoco_websocket_publisher_async(
    *,
    host: str,
    port: int,
    steps: int,
    dt_s: float,
    interval_s: float,
    grace_period_s: float,
    preset: str | None,
    robot_profile_id: str,
) -> None:
    runtime_config = RuntimeConfig(dt_s=dt_s, robot_profile_id=robot_profile_id)

    async with WebSocketPublisherServer(host=host, port=port) as server:
        _log(f"serving on ws://{server.host}:{server.bound_port}")
        _log(f"Waiting for viewer during grace period ({grace_period_s:.2f}s)")

        has_client = await server.wait_for_client(timeout_s=grace_period_s)
        if not has_client:
            _log("No viewer connected during grace period; no payloads published.")
            _log("Completed without publishing because no viewer connected.")
            return

        _log("Viewer connected; publishing started.")

        pipeline = build_concrete_mujoco_pipeline(
            frames=_sweep_x_replay_frames(steps) if preset == "sweep_x" else (_default_replay_frame(),),
            config=runtime_config,
            loop=False if preset == "sweep_x" else True,
            publisher=WebSocketStatePublisher(server),
        )

        projection = (
            partial(
                project_sweep_x_replay_state,
                authoritative_profile_metadata=pipeline.robot_profile_metadata,
            )
            if preset == "sweep_x"
            else None
        )
        for index in range(steps):
            if projection is None:
                await pipeline.run_once(dt_s=dt_s)
            else:
                await pipeline.run_once(dt_s=dt_s, state_projection=projection)
            if interval_s > 0.0 and index + 1 < steps:
                await asyncio.sleep(interval_s)

        _log(f"Completed after publishing {steps} frame(s).")


def run_replay_mujoco_websocket_publisher(
    *,
    host: str = DEFAULT_WEBSOCKET_PUBLISHER_HOST,
    port: int = DEFAULT_WEBSOCKET_PUBLISHER_PORT,
    steps: int = DEFAULT_WEBSOCKET_PUBLISHER_STEPS,
    dt_s: float = DEFAULT_WEBSOCKET_PUBLISHER_DT_S,
    interval_s: float = DEFAULT_WEBSOCKET_PUBLISHER_INTERVAL_S,
    grace_period_s: float = DEFAULT_WEBSOCKET_PUBLISHER_GRACE_PERIOD_S,
    preset: str | None = None,
    robot_profile_id: str = "fast_arm",
) -> None:
    _validate_host(host)
    _validate_port(port)
    _validate_steps(steps)
    _validate_dt_s(dt_s)
    _validate_interval_s(interval_s)
    _validate_grace_period_s(grace_period_s)
    if preset is not None and preset not in SUPPORTED_WEBSOCKET_PUBLISHER_PRESETS:
        raise ValueError("unsupported websocket publisher preset")

    asyncio.run(
        _run_replay_mujoco_websocket_publisher_async(
            host=host,
            port=port,
            steps=steps,
            dt_s=dt_s,
            interval_s=interval_s,
            grace_period_s=grace_period_s,
            preset=preset,
            robot_profile_id=robot_profile_id,
        )
    )


__all__ = [
    "SUPPORTED_WEBSOCKET_PUBLISHER_PRESETS",
    "run_input_source_websocket_publisher",
    "run_replay_mujoco_websocket_publisher",
]
