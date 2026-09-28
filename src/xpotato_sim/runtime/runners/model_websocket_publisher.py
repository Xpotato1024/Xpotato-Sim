"""モデル選択に基づく名前付き手先runtimeを既存Viewer WebSocketへ投影する。"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from time import monotonic

from xpotato_sim.runtime.execution.live_timing import AbsoluteDeadlinePacer
from xpotato_sim.runtime.runners.live_websocket_delivery import LiveLatestStateWebSocketPublisher
from xpotato_sim.runtime.composition.launch_profile import LaunchProfile
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.runtime.composition.coordinated_input import CoordinatedInputRuntime
from xpotato_sim.schemas import parse_viewer_control_message_json
from xpotato_sim.transport import WebSocketPublisherServer


def _runtime_metadata(
    runtime: CoordinatedInputRuntime,
    declaration_metadata: Mapping[str, object],
    *,
    state: str,
    reason: str | None,
    epoch: str,
    tick: int,
) -> dict[str, object]:
    metadata = {} if runtime.last_frame is None else dict(runtime.last_frame.metadata)
    metadata.update(dict(declaration_metadata))
    metadata.update({
        "physical_output": "disabled",
        "motion_status": state,
        "motion_rejection_reason": reason,
        "coordinated_runtime_v1": {
            "schema_version": "coordinated-runtime-presentation/v1",
            "state": state,
            "reason": reason,
            "epoch": epoch,
            "tick": tick,
            "arm_ids": list(runtime.runtime.provider.endpoint_ids),
        },
    })
    if state in {"faulted", "stopped"}:
        metadata.update(source_active=False, stale_reason=reason or state)
    presentation = runtime.latest_plane_presentation
    if presentation is not None:
        metadata["gamepad_plane_control_v1"] = presentation
    return metadata


def _ingest_message(
    runtime: CoordinatedInputRuntime,
    message: str,
) -> None:
    if runtime.runtime.state in {"faulted", "stopped"}:
        return
    try:
        parsed = parse_viewer_control_message_json(message)
        if (
            parsed.source_kind != "gamepad"
            or parsed.provider_id != "gamepad/v1"
            or parsed.provider_schema != "viewer_gamepad_sample/v1"
        ):
            raise ValueError("coordinated viewer requires explicit gamepad/v1 input")
        runtime.ingest(parsed)
    except Exception as exc:
        runtime.runtime.fail(f"source_ingress_failed:{type(exc).__name__}:{exc}")
        raise


async def _run_model_websocket_publisher_async(
    profile: LaunchProfile,
    *,
    clock: Callable[[], float],
    on_ready: Callable[[], None] | None,
) -> None:
    instance = profile.build_model()
    bundle = instance.viewer
    selected = select_runtime_input_source(profile.input_source.plugin_id, steps=1,
        control_mapping_selection=profile.mapping, control_mapping_parameters=profile.mapping_parameters)
    mapping = selected.control_mapping
    if mapping is None or mapping.session_strategy_factory is None:
        raise ValueError("session Mapping required")
    runtime = CoordinatedInputRuntime(
        provider=instance.provider, mapping_factory=mapping.session_strategy_factory,
        mapping_parameters=profile.mapping_parameters,
        side_to_arm=profile.side_to_endpoint,
        epoch=profile.epoch,
        dt_s=profile.dt_s,
        max_input_age_s=profile.max_input_age_s,
        clock=clock,
    )

    def on_message(message: str) -> None:
        _ingest_message(runtime, message)

    try:
        async with WebSocketPublisherServer(
            host=profile.host, port=profile.backend_port, on_message=on_message,
        ) as server:
            print(f"serving on ws://{server.host}:{server.bound_port}", flush=True)
            if on_ready is not None:
                on_ready()
            if not await server.wait_for_client(timeout_s=profile.grace_period_s):
                print("No viewer connected during grace period; no payloads published.", flush=True)
                return

            pacer = AbsoluteDeadlinePacer(profile.interval_s)
            pacer.start()
            async with LiveLatestStateWebSocketPublisher(server) as publisher:
                for frame_index in range(profile.steps):
                    if (runtime.source.last_received_at_s is None
                            and runtime.runtime.state == "waiting_neutral"):
                        runtime_state, runtime_reason, runtime_tick = (
                            "waiting_neutral", "awaiting_gamepad_input", 0)
                    else:
                        result = runtime.tick(epoch=profile.epoch)
                        runtime_state, runtime_reason, runtime_tick = (
                            result.state, result.reason, result.tick)
                    metadata = _runtime_metadata(
                        runtime, bundle.metadata, state=runtime_state,
                        reason=runtime_reason, epoch=profile.epoch, tick=runtime_tick,
                    )
                    snapshot = runtime.runtime.provider.snapshot()
                    if (snapshot.model_sha256 != bundle.metadata["model_sha256"]
                            or snapshot.joint_names != bundle.declaration.joint_names
                            or len(snapshot.joint_positions_rad) != bundle.declaration.qpos_dimension):
                        raise ValueError("backend/viewer assembly declaration mismatch")
                    state = runtime.runtime.provider.transport_state(
                        frame_index=frame_index, metadata=metadata,
                    )
                    if (state.qpos != snapshot.joint_positions_rad
                            or state.time_s != snapshot.simulation_time_s):
                        raise ValueError("backend/viewer snapshot mismatch")
                    await publisher.publish(state)
                    # fault後も姿勢と理由を表示する。入力復帰による自動再開はしない。
                    if frame_index + 1 < profile.steps:
                        await asyncio.sleep(0)
                        await pacer.pace()
                await publisher.drain()
    finally:
        runtime.stop()


def run_model_websocket_publisher(
    profile: LaunchProfile,
    *,
    clock: Callable[[], float] = monotonic,
    on_ready: Callable[[], None] | None = None,
) -> None:
    if type(profile) is not LaunchProfile or profile.model is None:
        raise TypeError("LaunchProfile is required")
    if not callable(clock):
        raise TypeError("monotonic clock is required")
    if on_ready is not None and not callable(on_ready):
        raise TypeError("on_ready must be callable")
    asyncio.run(_run_model_websocket_publisher_async(
        profile, clock=clock, on_ready=on_ready,
    ))


__all__ = ["run_model_websocket_publisher"]
