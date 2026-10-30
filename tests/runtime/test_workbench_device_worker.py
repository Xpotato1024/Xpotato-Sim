"""実workerと共通runnerのDevice準備・再試行・失敗時の所有終了を検査する。"""
from copy import deepcopy
from dataclasses import dataclass
import json

import pytest

from tests.plugins.input_sources.selfrionette_device.test_source import wire
from tests.runtime.test_workbench_worker import owner_projection
import xpotato_sim.runtime.application.workbench_worker as worker
from xpotato_sim.runtime.application.device_bridge import OPERATOR_PHASES
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
from xpotato_sim.runtime.experiment.edited_condition import preset_condition


pytestmark = pytest.mark.usefixtures("owner_projection")


class FakeBridgeGroup:
    """IPCだけを置換し、校正値のfixtureを既存Device Sourceへ渡す。"""

    def __init__(self, profile, clock, session):
        self.parameters = deepcopy(profile.input_parameters)
        self.clock = clock
        self.session = session
        self.phases = {role: "configured" for role in self.parameters["bindings"]}
        self.actions = []
        self.closed = False
        self.close_count = 0
        self.started_at = None
        self.next_sample_at = None
        self.sequence = 80
        self.delivered = []

    def state(self):
        roles = {role: {"phase": phase, "pending": None, "clock_synchronized": True,
            "ready": not self.closed and phase in {"applied", "streaming"},
            "session_neutral_valid": phase in {"neutral_ready", "applied", "streaming"},
            "expected": self.parameters["bindings"][role]}
            for role, phase in self.phases.items()}
        return {"transport": "selfrionette-bridge/v1", "error": None, "roles": roles,
            "ready": not self.closed and all(item["phase"] == "applied" for item in roles.values())}

    def operate(self, role, action, confirmed, observed_phase):
        if (self.closed or confirmed is not True or role not in self.phases
                or action not in OPERATOR_PHASES or observed_phase != self.phases[role]
                or observed_phase != OPERATOR_PHASES[action]):
            raise ValueError("explicit current Device preparation step required")
        self.actions.append((role, action))
        self.phases[role] = {"connect": "connected", "tare": "awaiting_neutral",
            "neutral": "neutral_ready", "apply": "applied"}[action]

    def start(self):
        if not self.state()["ready"]:
            raise ValueError("all Device roles must be prepared before Start")
        self.started_at = self.clock()
        self.next_sample_at = self.started_at + .01
        self.phases = {role: "streaming" for role in self.phases}

    def poll(self):
        if self.closed or self.next_sample_at is None or self.clock() < self.next_sample_at:
            return ()
        # 制御tickによる再送はしない。開始後の中立sampleに続いて新規sampleを送る。
        now = self.clock()
        self.next_sample_at = now + .01
        self.sequence += 1
        value = 0. if self.sequence == 81 else .15
        samples = []
        for role in self.phases:
            raw = json.loads(wire(role, sequence=self.sequence, session=self.session, value=value))
            source_receipt = 1000000 + (self.sequence - 81) * 12500
            raw["event"].update(host_received_us=source_receipt,
                host_emitted_us=source_receipt + 1500)
            samples.append((json.dumps(raw), now))
            self.delivered.append((role, self.session, self.sequence))
        return tuple(samples)

    def close(self):
        if not self.closed:
            self.closed = True
            self.close_count += 1
            self.phases = {role: "stopped" for role in self.phases}


@dataclass
class WaitForTicks:
    ticks: int


class WorkerScenario:
    """仮想時計でも実runnerの中立gate・MuJoCo step・記録を通す。"""

    def __init__(self, tmp_path, monkeypatch, commands, *, fail_bridge_creation=None):
        self.now = 10.
        self.commands = list(commands)
        self.events = []
        self.observations = {}
        self.groups = []
        self.runners = []
        self.creation_count = 0
        self.result_root = tmp_path / "results"
        self.asset_root = tmp_path / "assets"

        def new_runner(**kwargs):
            runner = TrialRunner(**kwargs, clock=lambda: self.now)
            self.runners.append(runner)
            return runner

        def new_bridge(profile, connection):
            if profile.provider_id != "selfrionette-device/v1":
                return None
            self.creation_count += 1
            assert all(group.closed for group in self.groups), "previous bridge owner still live"
            if self.creation_count == fail_bridge_creation:
                raise RuntimeError("injected retry bridge startup failure")
            group = FakeBridgeGroup(profile, lambda: self.now, session=2147 + self.creation_count)
            self.groups.append(group)
            return group

        monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY", "test")
        monkeypatch.setattr(worker, "monotonic", lambda: self.now)
        monkeypatch.setattr(worker, "TrialRunner", new_runner)
        monkeypatch.setattr(worker, "create_application_input", new_bridge)
        monkeypatch.setattr(worker, "AsyncWorkerConnection", lambda *args, **kwargs: self)

    def send(self, message):
        event = json.loads(message)
        self.events.append(event)
        if event.get("type") == "worker_status" and event.get("id"):
            self.observations[event["id"]] = {
                "groups_closed": tuple(group.closed for group in self.groups),
                "record_count": len(list(self.result_root.glob("*/start.json"))),
            }

    def recv(self, *, timeout):
        if isinstance(self.commands[0], WaitForTicks):
            runner = self.runners[-1]
            if runner.status == "running" and runner.tick_count >= self.commands[0].ticks:
                self.commands.pop(0)
            else:
                assert runner.status in {"waiting_input", "running"}, runner.error
                assert self.now < 12., "worker did not advance the bounded Device trial"
                # advance内の非blocking確認では時間を進めず、sampleを新しく見せない。
                if timeout > 0:
                    self.now += max(.002, min(.02, timeout))
                raise TimeoutError
        return json.dumps(self.commands.pop(0))

    def close(self):
        pass

    def run(self):
        worker.execution_worker("ws://test", {"result_root": str(self.result_root),
            "asset_root": str(self.asset_root), "software_revision": "software-fixture",
            "ticks": 12, "input_wait_s": 5, "wall_s": 30, "prepare_s": 30})
        assert not self.commands
        return {event["id"]: event for event in self.events
            if event.get("type") == "worker_status" and event.get("id")}


def prepare(profile="inputs/single-selfrionette-fixture"):
    return {"op": "prepare", "id": "prepare", "generation": 1, "profile_id": profile}


def operator_steps(roles, prefix="first"):
    return [{"op": "device", "id": f"{prefix}-{role}-{action}", "role": role,
        "action": action, "confirmed": True, "observed_phase": phase}
        for role in roles for action, phase in OPERATOR_PHASES.items()]


def test_shared_task_rebinds_gamepad_device_gamepad_with_new_epochs_and_no_demo_files(tmp_path, monkeypatch):
    device_condition = preset_condition("tasks/research-spring", input_provider="selfrionette-device/v1")
    commands = [{**prepare("tasks/research-spring"), "session_mode": "demo"},
        {"op": "stop", "id": "stop-gamepad", "generation": 2},
        {**prepare("tasks/research-spring"), "id": "device-prepare", "generation": 3,
            "condition": device_condition, "session_mode": "demo"},
        *operator_steps(("left",)),
        {"op": "start", "id": "device-start", "generation": 3}, WaitForTicks(2),
        {"op": "stop", "id": "stop-device", "generation": 4},
        {**prepare("tasks/research-spring"), "id": "gamepad-prepare", "generation": 5,
            "session_mode": "demo"}, {"op": "close"}]
    scenario = WorkerScenario(tmp_path, monkeypatch, commands)
    states = scenario.run()
    first = states["prepare"]["state"]
    second = states["device-prepare"]["state"]
    third = states["gamepad-prepare"]["state"]
    assert [state["profile_id"] for state in (first, second, third)] == ["tasks/research-spring"] * 3
    assert [state["input_provider"] for state in (first, second, third)] == [
        "gamepad/v1", "selfrionette-device/v1", "gamepad/v1"]
    assert len({state["ticket"]["epoch"] for state in (first, second, third)}) == 3
    assert states["stop-device"]["state"]["ticks"] == 2
    assert all(group.closed for group in scenario.groups)
    assert not scenario.result_root.exists() or not list(scenario.result_root.iterdir())


@pytest.mark.parametrize("fail_retry", [False, True])
def test_device_demo_success_retry_failure_and_reselection_never_record(tmp_path, monkeypatch, fail_retry):
    commands = [{**prepare(), "session_mode": "demo"}, *operator_steps(("left",)),
        {"op": "start", "id": "start", "generation": 1}, WaitForTicks(3),
        {"op": "stop", "id": "stop", "generation": 2},
        {"op": "retry", "id": "retry", "generation": 2}]
    if not fail_retry:
        commands += operator_steps(("left",), "retry")
        commands += [{"op": "start", "id": "retry-start", "generation": 2}, WaitForTicks(2),
            {"op": "stop", "id": "retry-stop", "generation": 3}]
    commands += [{**prepare("tasks/research-spring"), "id": "reselect", "generation": 4, "session_mode": "demo"},
        {"op": "close"}]
    scenario = WorkerScenario(tmp_path, monkeypatch, commands, fail_bridge_creation=2 if fail_retry else None)
    states = scenario.run()
    assert states["stop"]["state"]["ticks"] == 3
    assert states["stop"]["state"]["session_mode"] == "demo"
    assert states["reselect"]["state"]["input_provider"] == "gamepad/v1"
    assert states["reselect"]["state"]["ticks"] == 0
    assert all(group.closed for group in scenario.groups)
    if fail_retry:
        assert states["retry"]["error"] and states["retry"]["state"]["ticket"] is None
    assert not scenario.result_root.exists() or not list(scenario.result_root.iterdir())


@pytest.mark.parametrize("roles", [("left",), ("left", "right")])
def test_device_explicit_preparation_common_runner_stop_and_retry(tmp_path, monkeypatch, roles):
    """左右の片側だけでは開始せず、再試行は新processで全準備をやり直す。"""
    commands = [prepare("inputs/single-selfrionette-fixture" if len(roles) == 1 else "inputs/bimanual-selfrionette-fixture"),
        {"op": "start", "id": "premature-start", "generation": 1}]
    if len(roles) == 2:
        commands += operator_steps(("left",))
        commands += [{"op": "start", "id": "partial-start", "generation": 1}]
        commands += operator_steps(("right",))
    else:
        commands += operator_steps(roles)
    commands += [{"op": "start", "id": "start", "generation": 1}, WaitForTicks(3),
        {"op": "stop", "id": "stop", "generation": 2},
        {"op": "retry", "id": "retry", "generation": 2},
        {"op": "start", "id": "retry-premature-start", "generation": 2}]
    commands += operator_steps(roles, "retry")
    commands += [{"op": "start", "id": "retry-start", "generation": 2}, WaitForTicks(3),
        {"op": "stop", "id": "retry-stop", "generation": 3}, {"op": "close"}]
    scenario = WorkerScenario(tmp_path, monkeypatch, commands)
    states = scenario.run()

    rejected = ["premature-start", "retry-premature-start"]
    if len(roles) == 2:
        rejected.append("partial-start")
    for name in rejected:
        assert states[name]["error"] == "Device preparation is incomplete"
        assert states[name]["state"]["phase"] == "ready"
        assert states[name]["state"]["ticks"] == 0
        assert not states[name]["state"]["device_input"]["ready"]
    assert scenario.observations["premature-start"]["record_count"] == 0
    assert scenario.observations["retry-premature-start"]["record_count"] == 1
    for name in ("start", "retry-start"):
        assert states[name]["error"] is None
        assert states[name]["state"]["phase"] == "waiting_input"
    for name in ("stop", "retry-stop"):
        state = states[name]["state"]
        assert state["phase"] == "terminal" and state["ticks"] == 3
        assert state["result"]["runner_stop_reason"] == "operator_abort"
        assert state["input_provider"] == "selfrionette-device/v1"
        assert set(state["input_diagnostics"]["streams"]) == set(roles)
        assert state["input_diagnostics"]["processed_sequence"] > 81
        assert all(scenario.observations[name]["groups_closed"])
    assert states["retry"]["error"] is None
    assert states["retry"]["assets"] == states["prepare"]["assets"]
    assert states["retry"]["state"]["native_builds"] == 1
    assert states["retry"]["state"]["ticket"]["epoch"] != states["prepare"]["state"]["ticket"]["epoch"]
    assert scenario.observations["retry"]["groups_closed"] == (True, False)
    assert len(scenario.groups) == 2
    for group in scenario.groups:
        assert group.actions == [(role, action) for role in roles for action in OPERATOR_PHASES]
        assert group.close_count == 1
        assert len(group.delivered) == len(set(group.delivered))
    assert scenario.groups[0].session != scenario.groups[1].session
    records = [json.loads(path.read_text()) for path in scenario.result_root.glob("*/start.json")]
    assert len(records) == 2
    assert all(record["input_provenance"]["source"] == "selfrionette-bridge/v1" for record in records)


def test_prepare_asset_failure_closes_device_before_reporting_no_ready(tmp_path, monkeypatch):
    """asset公開が失敗した時点で、準備済みbridgeと開始可能ticketを残さない。"""
    from pathlib import Path
    scenario = WorkerScenario(tmp_path, monkeypatch, [prepare(),
        {"op": "start", "id": "start-after-failure", "generation": 1}, {"op": "close"}])
    original_mkdir = Path.mkdir

    def mkdir(path, *args, **kwargs):
        if path == scenario.asset_root:
            raise OSError("injected asset publication failure")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    states = scenario.run()
    failed = states["prepare"]
    assert "injected asset publication failure" in failed["error"]
    assert failed["state"]["phase"] == "unselected"
    assert failed["state"]["ticket"] is None
    assert failed["state"]["device_input"] is None
    assert failed["assets"] == []
    assert scenario.observations["prepare"]["groups_closed"] == (True,)
    assert states["start-after-failure"]["error"]
    assert states["start-after-failure"]["state"]["phase"] == "unselected"
    assert not scenario.result_root.exists()


def test_retry_bridge_creation_failure_revokes_ready_and_start(tmp_path, monkeypatch):
    """旧結果は保持し、再試行のbridge起動失敗をGamepad開始へ落とさない。"""
    commands = [prepare(), *operator_steps(("left",)),
        {"op": "start", "id": "start", "generation": 1}, WaitForTicks(3),
        {"op": "stop", "id": "stop", "generation": 2},
        {"op": "retry", "id": "retry", "generation": 2},
        {"op": "start", "id": "start-after-failure", "generation": 2}, {"op": "close"}]
    scenario = WorkerScenario(tmp_path, monkeypatch, commands, fail_bridge_creation=2)
    states = scenario.run()
    failed = states["retry"]
    assert "injected retry bridge startup failure" in failed["error"]
    assert failed["state"]["phase"] == "unselected"
    assert failed["state"]["ticket"] is None
    assert failed["state"]["device_input"] is None
    assert failed["assets"] == []
    assert scenario.observations["retry"]["groups_closed"] == (True,)
    assert states["start-after-failure"]["error"]
    assert states["start-after-failure"]["state"]["phase"] == "unselected"
    assert scenario.observations["start-after-failure"]["record_count"] == 1
    records = [json.loads(path.read_text()) for path in scenario.result_root.glob("*/terminal.json")]
    assert len(records) == 1
    assert records[0]["trial_id"] == states["stop"]["state"]["ticket"]["trial_id"]
    assert records[0]["runner_stop_reason"] == "operator_abort"
