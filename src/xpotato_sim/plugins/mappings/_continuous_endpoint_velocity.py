"""複数Mappingが共有するcontinuous endpoint-velocity algorithm primitive。

入力axisは正規化対象の ``(x, y, z)``、出力velocityは ``control_frame`` のm/s。
deadzone、supplement、norm clampの順序を所有するがstateは保持しない。
``max_delta_m`` はdownstream command ownerへ渡すper-step limitで、ここでは適用しない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite, sqrt

from xpotato_sim.schemas import ContinuousEndpointVelocityIntent


def build_continuous_endpoint_velocity_intent(
    axis_values: Sequence[float],
    *,
    source_kind: str,
    source_timestamp_s: float,
    speed_m_s: float,
    deadzone: float,
    max_delta_m: float,
    control_frame: str = "world",
    source_active: bool = True,
    stale_reason: str | None = None,
    source_diagnostics: Mapping[str, object] | None = None,
    supplemental_axis_values: Sequence[float] = (0.0, 0.0, 0.0),
    clamp_before_deadzone: bool = False,
) -> ContinuousEndpointVelocityIntent:
    """source axisをclamp/deadzoneしてversioned velocity intentへ写像する。

    ``axis_values`` とsupplementは3要素finite値で、結果はnorm 1以下へclampされる。
    sourceのinactive/stale判定は呼出側が所有し、この関数はその状態を変更しない。
    """
    components = tuple(float(component) for component in axis_values)
    if len(components) != 3:
        raise ValueError("axis_values must contain exactly three values")
    if not all(isfinite(component) for component in components):
        raise ValueError("axis_values must contain only finite values")
    supplements = tuple(float(component) for component in supplemental_axis_values)
    if len(supplements) != 3 or not all(isfinite(component) for component in supplements):
        raise ValueError("supplemental_axis_values must contain exactly three finite values")
    for name, value in (("deadzone", deadzone), ("speed_m_s", speed_m_s), ("max_delta_m", max_delta_m)):
        if not isfinite(value) or value < 0.0:
            raise ValueError(f"{name} must be finite and non-negative")

    raw_magnitude = sqrt(sum(component * component for component in components))
    raw_was_clamped = raw_magnitude > 1.0
    pre_deadzoned = (
        tuple(component / raw_magnitude for component in components)
        if clamp_before_deadzone and raw_was_clamped
        else components
    )
    deadzoned_base = tuple(0.0 if abs(component) <= deadzone else component for component in pre_deadzoned)
    deadzoned_magnitude = sqrt(sum(component * component for component in deadzoned_base))
    post_deadzone_was_clamped = deadzoned_magnitude > 1.0
    normalized_base = (
        tuple(component / deadzoned_magnitude for component in deadzoned_base)
        if post_deadzone_was_clamped
        else deadzoned_base
    )
    supplemented = tuple(
        base + supplement for base, supplement in zip(normalized_base, supplements, strict=True)
    )
    magnitude = sqrt(sum(component * component for component in supplemented))
    final_was_clamped = magnitude > 1.0
    normalized = (
        tuple(component / magnitude for component in supplemented)
        if final_was_clamped
        else supplemented
    )
    velocity = tuple(component * speed_m_s for component in normalized)
    return ContinuousEndpointVelocityIntent(
        source_kind=source_kind,
        source_timestamp_s=source_timestamp_s,
        axis_values=normalized,
        deadzone_applied_axis_values=deadzoned_base,
        local_endpoint_velocity_m_s=velocity,
        control_frame=control_frame,
        source_active=source_active,
        stale_reason=stale_reason,
        local_endpoint_speed_m_s=speed_m_s,
        local_endpoint_max_delta_m=max_delta_m,
        norm_clamped=(clamp_before_deadzone and raw_was_clamped)
        or post_deadzone_was_clamped
        or final_was_clamped,
        source_diagnostics={} if source_diagnostics is None else source_diagnostics,
    )


def build_normalized_analog_fixture_intent(
    axis_values: Sequence[float],
    *,
    source_kind: str = "fixture_analog",
    source_timestamp_s: float,
    speed_m_s: float,
    deadzone: float,
    max_delta_m: float,
    control_frame: str = "world",
    source_active: bool = True,
    stale_reason: str | None = None,
    source_diagnostics: Mapping[str, object] | None = None,
) -> ContinuousEndpointVelocityIntent:
    """recorded analog fixtureを同じunit/frame/clamp semanticsへroutingする。

    fixture acquisitionやparameter selectionは行わず、algorithm ownershipを
    ``build_continuous_endpoint_velocity_intent`` に一元化する。
    """

    return build_continuous_endpoint_velocity_intent(
        axis_values,
        source_kind=source_kind,
        source_timestamp_s=source_timestamp_s,
        speed_m_s=speed_m_s,
        deadzone=deadzone,
        max_delta_m=max_delta_m,
        control_frame=control_frame,
        source_active=source_active,
        stale_reason=stale_reason,
        source_diagnostics=source_diagnostics,
    )


__all__ = [
    "build_continuous_endpoint_velocity_intent",
    "build_normalized_analog_fixture_intent",
]
