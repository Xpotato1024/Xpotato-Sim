"""normalized inputからworld-frame endpoint targetを生成するstateful control policy。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite, sqrt

from xpotato_sim.schemas import Vector3


@dataclass(frozen=True, slots=True)
class EndpointTargetGeneratorConfig:
    """target integrationのm/s gain、deadzone、m/step上限、world workspace境界。"""

    gain_m_per_s: float
    deadzone: float
    max_step_m: float
    workspace_min_m: Vector3
    workspace_max_m: Vector3
    smoothing_alpha: float = 1.0


@dataclass(frozen=True, slots=True)
class EndpointTargetGeneratorState:
    """step間で継続するdesired endpointとrejection後hold state。"""

    previous_desired_endpoint_m: Vector3 | None
    last_valid_target_position_m: Vector3 | None
    previous_rejected: bool = False


@dataclass(frozen=True, slots=True)
class EndpointTargetGeneratorInput:
    """1 stepのworld tip(m)、normalized vector、正の ``dt_s`` 入力。"""

    current_tip_position_m: Vector3
    input_vector: Vector3
    dt_s: float
    control_frame: str = "world"


@dataclass(frozen=True, slots=True)
class EndpointTargetGeneratorResult:
    """生成target/delta(m)とmoved/clamped/projected/held semantics。"""

    desired_endpoint_m: Vector3
    target_delta_m: Vector3
    target_generation_status: str
    target_generation_reason: str
    clamped: bool
    projected: bool
    held: bool
    last_valid_target_position_m: Vector3


def _coerce_vector3(name: str, value: object) -> Vector3:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain exactly three values")

    try:
        components = tuple(float(component) for component in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain numeric values") from exc

    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three values")

    for component_index, component in enumerate(components):
        if not isfinite(component):
            raise ValueError(f"{name} must contain only finite values at index {component_index}")

    return components


def _vector_norm_m(vector: Vector3) -> float:
    return sqrt(sum(component * component for component in vector))


def _add_vector3(lhs: Vector3, rhs: Vector3) -> Vector3:
    return (lhs[0] + rhs[0], lhs[1] + rhs[1], lhs[2] + rhs[2])


def _subtract_vector3(lhs: Vector3, rhs: Vector3) -> Vector3:
    return (lhs[0] - rhs[0], lhs[1] - rhs[1], lhs[2] - rhs[2])


def _scale_vector3(vector: Vector3, scalar: float) -> Vector3:
    return (vector[0] * scalar, vector[1] * scalar, vector[2] * scalar)


def _clamp_vector3(value: Vector3, minimum: Vector3, maximum: Vector3) -> Vector3:
    return tuple(max(minimum[index], min(maximum[index], value[index])) for index in range(3))  # type: ignore[return-value]


def _validate_config(config: EndpointTargetGeneratorConfig) -> EndpointTargetGeneratorConfig:
    gain_m_per_s = float(config.gain_m_per_s)
    deadzone = float(config.deadzone)
    max_step_m = float(config.max_step_m)
    smoothing_alpha = float(config.smoothing_alpha)
    workspace_min_m = _coerce_vector3("workspace_min_m", config.workspace_min_m)
    workspace_max_m = _coerce_vector3("workspace_max_m", config.workspace_max_m)

    for name, value in (
        ("gain_m_per_s", gain_m_per_s),
        ("deadzone", deadzone),
        ("max_step_m", max_step_m),
        ("smoothing_alpha", smoothing_alpha),
    ):
        if not isfinite(value):
            raise ValueError(f"{name} must be finite")

    if gain_m_per_s < 0.0:
        raise ValueError("gain_m_per_s must be non-negative")
    if deadzone < 0.0:
        raise ValueError("deadzone must be non-negative")
    if max_step_m <= 0.0:
        raise ValueError("max_step_m must be positive")
    if not 0.0 <= smoothing_alpha <= 1.0:
        raise ValueError("smoothing_alpha must be between 0.0 and 1.0")

    for index, (minimum, maximum) in enumerate(zip(workspace_min_m, workspace_max_m, strict=True)):
        if minimum > maximum:
            raise ValueError(f"workspace_min_m must be <= workspace_max_m at index {index}")

    return EndpointTargetGeneratorConfig(
        gain_m_per_s=gain_m_per_s,
        deadzone=deadzone,
        max_step_m=max_step_m,
        workspace_min_m=workspace_min_m,
        workspace_max_m=workspace_max_m,
        smoothing_alpha=smoothing_alpha,
    )


def _validate_input(generator_input: EndpointTargetGeneratorInput) -> EndpointTargetGeneratorInput:
    current_tip_position_m = _coerce_vector3("current_tip_position_m", generator_input.current_tip_position_m)
    input_vector = _coerce_vector3("input_vector", generator_input.input_vector)
    dt_s = float(generator_input.dt_s)
    if not isfinite(dt_s):
        raise ValueError("dt_s must be finite")
    if dt_s <= 0.0:
        raise ValueError("dt_s must be positive")
    if generator_input.control_frame != "world":
        raise ValueError("control_frame must be world")
    return EndpointTargetGeneratorInput(
        current_tip_position_m=current_tip_position_m,
        input_vector=input_vector,
        dt_s=dt_s,
        control_frame=generator_input.control_frame,
    )


def _validate_state(state: EndpointTargetGeneratorState) -> EndpointTargetGeneratorState:
    previous_desired_endpoint_m = (
        None
        if state.previous_desired_endpoint_m is None
        else _coerce_vector3("previous_desired_endpoint_m", state.previous_desired_endpoint_m)
    )
    last_valid_target_position_m = (
        None
        if state.last_valid_target_position_m is None
        else _coerce_vector3("last_valid_target_position_m", state.last_valid_target_position_m)
    )
    return EndpointTargetGeneratorState(
        previous_desired_endpoint_m=previous_desired_endpoint_m,
        last_valid_target_position_m=last_valid_target_position_m,
        previous_rejected=bool(state.previous_rejected),
    )


def generate_endpoint_target(
    config: EndpointTargetGeneratorConfig,
    state: EndpointTargetGeneratorState,
    generator_input: EndpointTargetGeneratorInput,
) -> EndpointTargetGeneratorResult:
    """入力を積分しworkspaceへprojectするか、deadzone/rejection時にholdする。

    validationはstate更新やRobot command生成より前に行う。``previous_rejected`` の次stepは
    last valid targetをholdし、solver/Robotへ新しい移動を要求しない。
    """

    config = _validate_config(config)
    state = _validate_state(state)
    generator_input = _validate_input(generator_input)

    if state.previous_rejected:
        hold_target_m = state.last_valid_target_position_m or generator_input.current_tip_position_m
        return EndpointTargetGeneratorResult(
            desired_endpoint_m=hold_target_m,
            target_delta_m=(0.0, 0.0, 0.0),
            target_generation_status="held_after_rejection",
            target_generation_reason="previous_rejection",
            clamped=False,
            projected=False,
            held=True,
            last_valid_target_position_m=hold_target_m,
        )

    if state.previous_desired_endpoint_m is None:
        return EndpointTargetGeneratorResult(
            desired_endpoint_m=generator_input.current_tip_position_m,
            target_delta_m=(0.0, 0.0, 0.0),
            target_generation_status="initialized",
            target_generation_reason="initial_current_tip",
            clamped=False,
            projected=False,
            held=False,
            last_valid_target_position_m=generator_input.current_tip_position_m,
        )

    previous_desired_endpoint_m = state.previous_desired_endpoint_m
    input_norm = _vector_norm_m(generator_input.input_vector)
    if input_norm <= config.deadzone:
        return EndpointTargetGeneratorResult(
            desired_endpoint_m=previous_desired_endpoint_m,
            target_delta_m=(0.0, 0.0, 0.0),
            target_generation_status="held",
            target_generation_reason="deadzone",
            clamped=False,
            projected=False,
            held=True,
            last_valid_target_position_m=state.last_valid_target_position_m or previous_desired_endpoint_m,
        )

    normalized_input = (
        _scale_vector3(generator_input.input_vector, 1.0 / input_norm)
        if input_norm > 1.0
        else generator_input.input_vector
    )
    raw_delta_m = _scale_vector3(
        normalized_input,
        config.gain_m_per_s * generator_input.dt_s * config.smoothing_alpha,
    )

    clamped = False
    delta_norm = _vector_norm_m(raw_delta_m)
    if delta_norm > config.max_step_m:
        raw_delta_m = _scale_vector3(raw_delta_m, config.max_step_m / delta_norm)
        clamped = True

    candidate_m = _add_vector3(previous_desired_endpoint_m, raw_delta_m)
    projected_candidate_m = _clamp_vector3(candidate_m, config.workspace_min_m, config.workspace_max_m)
    projected = projected_candidate_m != candidate_m
    target_delta_m = _subtract_vector3(projected_candidate_m, previous_desired_endpoint_m)

    status = "moved"
    reason = "input_motion"
    if projected:
        status = "projected"
        reason = "workspace_projection"
    elif clamped:
        status = "clamped"
        reason = "max_step"

    return EndpointTargetGeneratorResult(
        desired_endpoint_m=projected_candidate_m,
        target_delta_m=target_delta_m,
        target_generation_status=status,
        target_generation_reason=reason,
        clamped=clamped,
        projected=projected,
        held=False,
        last_valid_target_position_m=projected_candidate_m,
    )


def endpoint_target_generation_result_to_metadata(result: EndpointTargetGeneratorResult) -> dict[str, object]:
    """結果をdiagnostic metadataへ投影し、control decision自体は変更しない。"""

    return {
        "desired_endpoint_m": result.desired_endpoint_m,
        "target_delta_m": result.target_delta_m,
        "target_generation_status": result.target_generation_status,
        "target_generation_reason": result.target_generation_reason,
        "target_generation_clamped": result.clamped,
        "target_generation_projected": result.projected,
        "target_generation_held": result.held,
        "last_valid_target_position_m": result.last_valid_target_position_m,
    }


__all__ = [
    "EndpointTargetGeneratorConfig",
    "EndpointTargetGeneratorInput",
    "EndpointTargetGeneratorResult",
    "EndpointTargetGeneratorState",
    "endpoint_target_generation_result_to_metadata",
    "generate_endpoint_target",
]
