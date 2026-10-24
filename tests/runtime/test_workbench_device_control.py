"""通常の操作権/revision/ticketを保ったDevice準備gate。"""
import pytest

from tests.runtime.test_workbench_control import controller, request, TICKET, KEY


def device_controller():
    c = controller()
    c.state.update(phase="ready", ticket=TICKET, input_provider="selfrionette-device/v1",
        device_input={"ready": False, "roles": {"left": {"phase": "configured", "pending": None, "clock_synchronized": True}}})
    c.renderer_epoch = TICKET["epoch"]
    return c


def test_device_start_cannot_skip_operator_steps_or_accept_browser_input():
    c = device_controller()
    with pytest.raises(ValueError, match="tare"):
        c.command("owner", request(c, "start"))
    c.state["phase"] = "running"
    with pytest.raises(ValueError, match="Gamepad"):
        c.command("owner", {"op": "input", "capability": KEY, "ticket": TICKET, "message": "{}"})


def test_device_action_reuses_authority_revision_and_exact_phase_gate():
    c = device_controller()
    action = request(c, "device", role="left", action="connect", confirmed=True, observed_phase="configured")
    for extra in ({"confirmed": False}, {"observed_phase": "connected"}, {"role": "right"},
            {"action": "apply"}, {"capability": "invalid"}, {"revision": -1}, {"ticket": None},
            {"port": "browser-cannot-pick-a-port"}):
        with pytest.raises(ValueError):
            c.command("owner", {**action, **extra})
    accepted, cmd = c.command("owner", action)
    assert accepted["type"] == "accepted" and cmd["confirmed"] is True
    assert "capability" not in cmd and cmd["generation"] == c.generation
    assert c.command("owner", action) == (accepted, None)


def test_device_action_is_unavailable_during_active_trial():
    c = device_controller()
    c.state["phase"] = "running"
    with pytest.raises(ValueError):
        c.command("owner", request(c, "device", role="left", action="connect", confirmed=True, observed_phase="configured"))


def test_ready_device_owner_disconnect_requires_supervised_stop():
    from xpotato_sim.runtime.application.workbench_control import owner_disconnect_requires_stop
    c = device_controller()
    assert owner_disconnect_requires_stop(c.state, None)
    assert not owner_disconnect_requires_stop({"phase": "ready", "device_input": None}, None)
    assert owner_disconnect_requires_stop({"phase": "running"}, None)
