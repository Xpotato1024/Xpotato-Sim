"""Pure physical-output permission evaluation.

This module only evaluates a typed request against a typed permission snapshot.
It does not open a socket, import a transport, or call a Robot provider.
"""

from __future__ import annotations

from collections.abc import Collection
from math import isfinite
from numbers import Real

from xpotato_sim.schemas import (
    PhysicalOutputDecision,
    PhysicalOutputPermission,
    PhysicalOutputRequest,
)


def evaluate_physical_output_permission(
    request: PhysicalOutputRequest,
    permission: PhysicalOutputPermission,
    *,
    known_target_robot_ids: Collection[str] | None = None,
    known_endpoint_ids: Collection[str] | None = None,
    now_s: float | None = None,
    max_age_s: float | None = None,
) -> PhysicalOutputDecision:
    """Return an explicit accepted/rejected decision without performing output.

    Optional identity and freshness context is supplied by the caller rather than
    resolved here.  The evaluator never discovers a Robot, reads a clock, or
    contacts a transport, so omitting that context cannot create an implicit
    allow path.
    """

    if not isinstance(request, PhysicalOutputRequest):
        raise TypeError("physical output permission evaluation requires PhysicalOutputRequest")
    if not isinstance(permission, PhysicalOutputPermission):
        raise TypeError(
            "physical output permission evaluation requires PhysicalOutputPermission"
        )

    validation_reason = _validate_request_context(
        request,
        known_target_robot_ids=known_target_robot_ids,
        known_endpoint_ids=known_endpoint_ids,
        now_s=now_s,
        max_age_s=max_age_s,
    )
    if validation_reason is not None:
        return PhysicalOutputDecision(
            request=request,
            permission=permission,
            status="rejected",
            reason=validation_reason,
        )

    if permission.mode == "disabled":
        return PhysicalOutputDecision(
            request=request,
            permission=permission,
            status="rejected",
            reason="physical_output_disabled",
        )
    if permission.mode == "dry_run":
        return PhysicalOutputDecision(
            request=request,
            permission=permission,
            status="accepted",
        )
    if permission.mode == "transmission_enabled":
        reason = None if permission.allows_transmission else "explicit_operator_enable_required"
    elif permission.mode == "physical_actuation":
        reason = (
            None
            if permission.allows_physical_actuation
            else "explicit_operator_enable_required"
        )
    else:  # PhysicalOutputPermission validates this branch away.
        reason = "unknown_physical_output_mode"

    if reason is not None:
        return PhysicalOutputDecision(
            request=request,
            permission=permission,
            status="rejected",
            reason=reason,
        )
    return PhysicalOutputDecision(
        request=request,
        permission=permission,
        status="accepted",
    )


def _validate_request_context(
    request: PhysicalOutputRequest,
    *,
    known_target_robot_ids: Collection[str] | None,
    known_endpoint_ids: Collection[str] | None,
    now_s: float | None,
    max_age_s: float | None,
) -> str | None:
    """Validate optional caller-owned identity/freshness facts fail-closed."""

    if known_target_robot_ids is not None and request.target_robot_id not in known_target_robot_ids:
        return "unknown_target_robot"
    if known_endpoint_ids is not None and request.endpoint_id not in known_endpoint_ids:
        return "unknown_output_endpoint"
    if max_age_s is not None:
        if isinstance(max_age_s, bool) or not isinstance(max_age_s, Real):
            raise TypeError("max_age_s must be numeric")
        max_age = float(max_age_s)
        if not isfinite(max_age) or max_age < 0.0:
            raise ValueError("max_age_s must be finite and non-negative")
        if now_s is None:
            raise ValueError("now_s is required when max_age_s is provided")
        if isinstance(now_s, bool) or not isinstance(now_s, Real):
            raise TypeError("now_s must be numeric")
        now = float(now_s)
        if not isfinite(now):
            raise ValueError("now_s must be finite")
        age = now - request.timestamp_s
        if age < 0.0:
            return "physical_output_timestamp_in_future"
        if age > max_age:
            return "physical_output_request_stale"
    elif now_s is not None:
        if isinstance(now_s, bool) or not isinstance(now_s, Real):
            raise TypeError("now_s must be numeric")
        if not isfinite(float(now_s)):
            raise ValueError("now_s must be finite")
    return None


__all__ = ["evaluate_physical_output_permission"]
