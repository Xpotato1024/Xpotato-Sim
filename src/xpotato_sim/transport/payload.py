from __future__ import annotations

from typing import Any

from xpotato_sim.schemas import MuJoCoState

TRANSPORT_PAYLOAD_VERSION = 0
TransportPayload = dict[str, Any]


def _vector_to_list(vector: tuple[float, ...] | list[float]) -> list[float]:
    return list(vector)


def _transform_to_payload(transform: Any) -> dict[str, Any]:
    return {
        "name": transform.name,
        "position_m": _vector_to_list(transform.position_m),
        "quaternion_wxyz": _vector_to_list(transform.quaternion_wxyz),
    }


def mujoco_state_to_payload(state: MuJoCoState) -> TransportPayload:
    """Convert a MuJoCoState snapshot into a JSON-compatible transport payload.

    The returned payload uses only JSON-compatible containers and values
    composed of int, float, str, None, dict, and list. metadata is shallow-copied
    and is expected to already be JSON-compatible.
    """

    target_position_m: list[float] | None
    if state.target_position_m is None:
        target_position_m = None
    else:
        target_position_m = _vector_to_list(state.target_position_m)

    metadata = dict(state.metadata)
    endpoint_evaluation = metadata.pop("endpoint_evaluation", None)

    payload: TransportPayload = {
        "version": TRANSPORT_PAYLOAD_VERSION,
        "frame_index": state.frame_index,
        "time_s": state.time_s,
        "qpos": _vector_to_list(state.qpos),
        "qvel": _vector_to_list(state.qvel),
        "bodies": [_transform_to_payload(body) for body in state.bodies],
        "sites": [_transform_to_payload(site) for site in state.sites],
        "target_position_m": target_position_m,
        "metadata": metadata,
    }

    if endpoint_evaluation is not None:
        payload["endpoint_evaluation"] = endpoint_evaluation

    return payload
