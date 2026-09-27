"""送信直前・arming前のhealth失効を実通信なしで再現する。"""
import socket
import pytest
from tests.runtime import test_coordinated_output as support
from xpotato_sim.runtime.output.coordinated import CoordinatedPhysicalOutputGroup


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no network permitted")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def test_health_expiry_during_prepare_blocks_every_dispatch(monkeypatch):
    group, _, senders, evaluations, stops, _, _ = support.setup(monkeypatch, health_age=.25)
    clock = [1.0]
    group.clock = lambda: clock[0]
    prepare = senders["right"].prepare
    def delayed_prepare(endpoint):
        result = prepare(endpoint)
        clock[0] = 1.5
        return result
    monkeypatch.setattr(senders["right"], "prepare", delayed_prepare)
    result = group.submit(evaluations, input=support.source(1))
    assert result.state == "faulted"
    assert not result.dispatch_call_arms
    assert all(not sender.send_calls for sender in senders.values())
    assert stops == ["left", "right"]


def test_health_deadline_is_exclusive(monkeypatch):
    group, _, _, _, stops, _, _ = support.setup(monkeypatch, health_age=.25)
    assert group.poll(now_s=1.25).state == "faulted"
    assert stops == ["left", "right"]


@pytest.mark.parametrize("arm_id", [None, 3, [], "unknown"])
def test_invalid_arm_identifier_still_faults_safely(monkeypatch, arm_id):
    group, _, _, _, stops, _, _ = support.setup(monkeypatch, health_age=.25)
    assert group.observe_health(arm_id, b"invalid", now_s=1.01) is None
    assert group.state == "faulted"
    assert stops == ["left", "right"]


def test_invalid_prearm_packet_invalidates_previous_healthy_value(monkeypatch):
    session, _, _, _, sender, _, physical, transmission = support.support._new_session(
        target_robot_id="target_left")
    stops = []
    group = CoordinatedPhysicalOutputGroup(
        {"left": session}, scene_preflight=lambda _: True,
        stop_requesters={"left": lambda: stops.append("left")},
        max_input_age_s=10., max_router_health_age_s=.25, clock=lambda: 1.)
    assert group.observe_health("left", support.health("target_left"), now_s=1.)
    assert group.observe_health("left", b"invalid", now_s=1.01) is None
    neutral = support.source()
    from dataclasses import replace
    neutral = replace(neutral, endpoints=(neutral.endpoints[0],))
    result = group.arm({"left": (physical, transmission)}, neutral=neutral, now_s=1.02)
    assert result.state == "faulted"
    assert "router_health_missing" in result.reason
    assert not sender.send_calls
    assert stops == ["left"]


def test_health_after_fault_does_not_rearm(monkeypatch):
    group, sessions, _, _, stops, permissions, _ = support.setup(monkeypatch, health_age=.25)
    group.observe_health("left", support.health("target_left", "stale"), now_s=1.01)
    for arm, session in sessions.items():
        group.observe_health(arm, support.health(session.target_robot_id), now_s=1.02)
    assert group.arm(permissions, neutral=support.source(2), now_s=1.03).state == "faulted"
    assert stops == ["left", "right"]


@pytest.mark.parametrize("arguments", [
    ("router-target-health/v2", "healthy", .0, 0),
    ("router-target-health/v1", "unknown", .0, 0),
    ("router-target-health/v1", "healthy", -1.0, 0),
    ("router-target-health/v1", "awaiting_state", .0, 0),
    ("router-target-health/v1", "healthy", .0, 1),
    ("router-target-health/v1", "watchdog_tripped", .0, 0),
    ("router-target-health/v1", "healthy", -.5, 0),
    ("router-target-health/v1", "healthy", float("nan"), 0),
    ("router-target-health/v1", "healthy", float("inf"), 0),
    ("router-target-health/v1", "healthy", 0, 0),
    ("router-target-health/v1", "healthy", .0, False),
    ("router-target-health/v1", "healthy", .0, 2),
    ("router-target-health/v1", "healthy", .0),
])
def test_health_wire_rejects_inconsistent_values(arguments):
    from xpotato_sim.plugins.robots.fast_arm.adapter.physical_output import (
        parse_fast_arm_router_target_health,
    )
    with pytest.raises((TypeError, ValueError)):
        parse_fast_arm_router_target_health("/router/armL/health", arguments)
