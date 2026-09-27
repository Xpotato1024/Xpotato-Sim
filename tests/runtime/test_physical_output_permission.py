from __future__ import annotations

from xpotato_sim.runtime.output import evaluate_physical_output_permission
from xpotato_sim.schemas import PhysicalOutputPermission

from tests.schemas.test_physical_output_contract import _endpoint_request


def test_default_permission_rejects_without_side_effect() -> None:
    decision = evaluate_physical_output_permission(
        _endpoint_request(),
        PhysicalOutputPermission(),
    )

    assert decision.status == "rejected"
    assert decision.reason == "physical_output_disabled"
    assert decision.requested
    assert not decision.sent
    assert not decision.acknowledged


def test_dry_run_accepts_request_but_never_claims_delivery() -> None:
    decision = evaluate_physical_output_permission(
        _endpoint_request(),
        PhysicalOutputPermission(mode="dry_run"),
    )

    assert decision.status == "accepted"
    assert decision.reason is None
    assert decision.requested
    assert not decision.sent
    assert not decision.acknowledged


def test_transmission_and_actuation_require_explicit_operator_enable() -> None:
    request = _endpoint_request()

    transmission = evaluate_physical_output_permission(
        request,
        PhysicalOutputPermission(
            mode="transmission_enabled",
            operator_id="operator-1",
            enable_token_id="gate-1",
        ),
    )
    actuation = evaluate_physical_output_permission(
        request,
        PhysicalOutputPermission(
            mode="physical_actuation",
            operator_id="operator-1",
            enable_token_id="gate-1",
        ),
    )

    assert transmission.status == "accepted"
    assert actuation.status == "accepted"
    assert transmission.permission.state == "enabled"
    assert actuation.permission.allows_physical_actuation


def test_optional_identity_and_freshness_context_rejects_unknown_or_stale_request() -> None:
    request = _endpoint_request()
    permission = PhysicalOutputPermission(mode="dry_run")

    unknown = evaluate_physical_output_permission(
        request,
        permission,
        known_target_robot_ids=("other_robot",),
    )
    assert unknown.status == "rejected"
    assert unknown.reason == "unknown_target_robot"

    stale = evaluate_physical_output_permission(
        request,
        permission,
        known_target_robot_ids=("fast_arm",),
        known_endpoint_ids=("tool_endpoint",),
        now_s=2.0,
        max_age_s=0.5,
    )
    assert stale.status == "rejected"
    assert stale.reason == "physical_output_request_stale"
