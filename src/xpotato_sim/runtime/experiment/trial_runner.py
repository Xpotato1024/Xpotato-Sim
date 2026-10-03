"""条件未選択の待機から有限試行・immutable結果・同条件retryまでの単一所有者。"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
from threading import Lock, get_ident
from time import monotonic
from uuid import uuid4

from xpotato_sim.runtime.execution.model_execution import ModelExecution
from xpotato_sim.runtime.experiment.contracts import TaskTerminalClassification
from xpotato_sim.runtime.experiment.trial_condition import (
    TrialLimits, resolve_trial_profile, semantic_parameters, freeze_condition,
)
from xpotato_sim.runtime.experiment.trial_record import TrialRecorder, TrialResult, TerminalRecordingJob
from xpotato_sim.runtime.scene.objects import canonical
from xpotato_sim.schemas import parse_viewer_control_message_json


@dataclass(frozen=True, slots=True)
class TrialTicket:
    """現在ready試行の開始・入力権。旧ticketは同じconditionでも無効。"""
    trial_id: str
    epoch: str
    condition_sha256: str


class TrialRunner:
    """同期owner。callerがadvanceを継続し、wall/watchdogを監督する。"""
    def __init__(self, *, result_root: Path, software_revision: str, clock=monotonic, async_terminal_recording=False):
        if not isinstance(software_revision, str) or not software_revision.strip():
            raise ValueError("explicit software revision required")
        if not callable(clock):
            raise TypeError("monotonic clock required")
        self._root = Path(result_root).resolve()
        self._revision, self._clock = software_revision, clock
        self._owner, self._lock = get_ident(), Lock()
        self._status = "unselected"
        self._execution = None
        self._model_build_count = 0
        self._condition = self._parameters = self._provenance = self._limits = None
        self._ticket = self._result = self._recorder = self._start_ref = None
        self._last_now = self._started = self._ingress_time = None
        self._last_input = None
        self._input_accepting = None
        self._committed_projection = None
        self._frame = 0
        self._error = None
        self._async_terminal_recording = async_terminal_recording
        self._record_deadline = None
        self._record_job = None
        self._pending_terminal = None

    @contextmanager
    def _mutating(self):
        if get_ident() != self._owner:
            raise RuntimeError("trial mutator requires owner thread")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("reentrant trial mutation rejected")
        try:
            yield
        finally:
            self._lock.release()

    @property
    def status(self):
        return self._status

    @property
    def condition(self):
        return self._condition

    @property
    def ticket(self):
        return self._ticket

    @property
    def result(self):
        return self._result

    @property
    def error(self):
        return self._error

    @property
    def tick_count(self):
        return 0 if self._execution is None else self._execution.tick_count

    @property
    def model_build_count(self):
        """成功したModelExecution構築数。retryによるresetは含めない。"""
        return self._model_build_count

    @property
    def input_accepting_monotonic_s(self):
        """開始記録が成功し、入力待機を開始したhost時刻。"""
        return self._input_accepting

    @property
    def processed_input_sequence(self):
        """消費成功した最新sequenceだけを診断へ公開する。"""
        return None if self._last_input is None else self._last_input[1]

    @property
    def processed_input_diagnostics(self):
        """session IDやdevice値を含めず、消費済みsampleの時刻と順番を返す。"""
        value = self._last_input
        return {"processed_sequence": None if value is None else value[1],
                "processed_source_timestamp_s": None if value is None else value[2],
                "processed_receipt_s": None if value is None else value[3],
                "last_receipt_gap_s": None if self._execution is None else
                    self._execution.runtime.runtime.last_receipt_gap_s}

    @property
    def viewer_resources(self):
        """現在のnative modelが公開した不変viewer bundle。別modelをbuildしない。"""
        return None if self._execution is None else self._execution.instance.viewer

    def _now(self):
        now = self._clock()
        if type(now) not in (int, float) or not isfinite(now) or now < 0:
            raise ValueError("finite monotonic time required")
        if self._last_now is not None and now < self._last_now:
            raise ValueError("monotonic clock regressed")
        self._last_now = float(now)
        return float(now)

    def _source_clock(self):
        return self._now() if self._ingress_time is None else self._ingress_time

    def _check_ticket(self, ticket):
        if type(ticket) is not TrialTicket or ticket != self._ticket:
            raise ValueError("foreign or old trial ticket")

    def _new_ticket(self):
        trial_id = uuid4().hex
        self._ticket = TrialTicket(trial_id, "trial-" + trial_id, self._condition.digest)
        self._recorder = self._start_ref = self._started = self._last_input = None
        self._input_accepting = None
        self._committed_projection = None
        self._frame = 0

    def _stop(self):
        if self._execution is not None:
            self._execution.stop()

    def prepare(self, profile, limits: TrialLimits):
        """実効条件を再検証し、同条件ならmodelを再利用してreadyを発行する。"""
        with self._mutating():
            if self._status not in {"unselected", "ready", "terminal"}:
                raise RuntimeError("prepare requires inactive healthy runner")
            self._status = "preparing"
            try:
                started = self._now()
                validated = resolve_trial_profile(profile)
                parameters = semantic_parameters(validated, limits)
                self._stop()
                if self._parameters != canonical(parameters):
                    self._execution = None
                    execution = ModelExecution(validated, clock=self._source_clock)
                    self._model_build_count += 1
                    condition = freeze_condition(parameters, execution.instance)
                    self._execution = execution
                    self._condition = condition
                    self._parameters = canonical(parameters)
                self._limits = limits
                self._provenance = {"configuration": json.loads(profile.document_json),
                    "configuration_sha256": profile.configuration_sha256,
                    "name": profile.name}
                self._new_ticket()
                self._execution.reset(self._ticket.epoch)
                if self._now() - started >= limits.prepare_s:
                    raise TimeoutError("prepare watchdog exceeded")
                self._status = "ready"
                return self._ticket
            except Exception as exc:
                self._stop()
                self._error, self._status = str(exc), "faulted"
                raise

    def retry(self):
        """正常に確定した結果を保持し、新しいepochのreadyへ戻す。開始は別操作。"""
        with self._mutating():
            if self._status != "terminal":
                raise RuntimeError("retry requires a recorded terminal result")
            self._status = "preparing"
            try:
                started = self._now()
                self._new_ticket()
                self._execution.reset(self._ticket.epoch)
                if self._now() - started >= self._limits.prepare_s:
                    raise TimeoutError("reset watchdog exceeded")
                self._status = "ready"
                return self._ticket
            except Exception as exc:
                self._stop()
                self._error, self._status = str(exc), "faulted"
                raise

    def start(self, ticket, *, input_provenance):
        """開始記録が成立するまで入力・physicsを許可しない。"""
        with self._mutating():
            self._check_ticket(ticket)
            if self._status != "ready":
                raise RuntimeError("start requires ready; duplicate start rejected")
            provenance = canonical(input_provenance)
            if not isinstance(input_provenance, dict) or not input_provenance or len(provenance) > 16384:
                raise ValueError("explicit bounded input provenance required")
            try:
                self._started = self._now()
                initial_state = self._execution.instance.provider.trial_state()
            except Exception as exc:
                self._stop()
                self._error, self._status = str(exc), "faulted"
                raise
            try:
                self._recorder = TrialRecorder(self._root, ticket.trial_id)
                self._start_ref = self._recorder.start(condition=self._condition,
                    provenance=self._provenance,
                    initial_state=initial_state,
                    record={"schema_version": "trial-start/v1", **self._identity(),
                        "started_monotonic_s": self._started,
                        "input_provenance": json.loads(provenance)})
                if self._async_terminal_recording:
                    self._record_job = TerminalRecordingJob(self._recorder)
                self._input_accepting = self._now()
                self._execution.runtime.runtime.accept_inputs_after(self._input_accepting)
                self._status = "waiting_input"
            except Exception as exc:
                self._recording_failed(exc)
                raise

    def _identity(self):
        return {"trial_id": self._ticket.trial_id, "epoch": self._ticket.epoch,
            "condition_sha256": self._condition.digest, "software_revision": self._revision}

    def ingest(self, ticket, message: str, *, received_at_s: float):
        """受領元のmonotonic時刻を保持し、遅延sampleの鮮度を更新しない。"""
        return self.ingest_batch(ticket, ((message, received_at_s),))

    def ingest_batch(self, ticket, samples, *, check_freshness=True):
        """最大64件のreceipt履歴を消費後、最新sampleの実時刻鮮度を検査する。"""
        with self._mutating():
            self._check_ticket(ticket)
            if self._status not in {"waiting_input", "running"}:
                raise RuntimeError("input requires an active trial")
            try:
                self._consume_batch(ticket, samples, check_freshness=check_freshness)
            except Exception as exc:
                self._finish("technical_invalid", error=str(exc))
                raise

    def _consume_batch(self, ticket, samples, *, check_freshness=True):
        if type(samples) not in (tuple, list) or not 1 <= len(samples) <= 64:
            raise ValueError("bounded nonempty input batch required")
        now = self._now()
        for message, received_at_s in samples:
            self._ingest_received(ticket, message, received_at_s=received_at_s, now=now)
        age = self._now() - self._last_input[3]
        limit = self._execution.profile.max_input_age_s
        if check_freshness and age > limit:
            raise ValueError(f"input_stale: age_s={age:.6f}; limit_s={limit:.6f}; "
                             f"received_at_s={self._last_input[3]:.6f}; now_s={self._last_now:.6f}")

    def _ingest_received(self, ticket, message, *, received_at_s, now):
        self._committed_projection = None
        self._check_ticket(ticket)
        if self._status not in {"waiting_input", "running"}:
            raise RuntimeError("input requires an active trial")
        try:
            limit = self._execution.profile.max_input_age_s
            if type(received_at_s) not in (int, float) or not isfinite(received_at_s):
                raise ValueError(f"input_invalid_timestamp: finite receipt required; limit_s={limit:.6f}")
            age = now - received_at_s
            diagnostic = (f"age_s={age:.6f}; limit_s={limit:.6f}; received_at_s={received_at_s:.6f}; "
                          f"now_s={now:.6f}; trial_started_at_s={self._started:.6f}")
            if received_at_s < self._started:
                raise ValueError(f"input_pre_trial: {diagnostic}")
            if received_at_s < self._input_accepting:
                raise ValueError(f"input_pre_recording: input_accepting_monotonic_s={self._input_accepting:.6f}; {diagnostic}")
            if age < 0:
                raise ValueError(f"input_future: {diagnostic}")
            if type(message) is not str or len(message.encode("utf-8")) > 65536:
                raise ValueError("bounded Gamepad message required")
            parsed = parse_viewer_control_message_json(message)
            if parsed.gamepad is None or parsed.gamepad.stale or not parsed.gamepad.connected:
                raise ValueError("unavailable or stale Gamepad input")
            session = parsed.metadata.get("viewer_provider_session_id")
            identity = (session, parsed.sequence, parsed.timestamp_s, float(received_at_s))
            if not isinstance(session, str) or not session or parsed.sequence is None:
                raise ValueError("explicit input session/sequence required")
            if self._last_input is not None:
                old = self._last_input
                if (session != old[0] or parsed.sequence <= old[1]
                        or parsed.timestamp_s < old[2] or received_at_s < old[3]):
                    raise ValueError("duplicate, changed-source, or out-of-order input")
            self._ingress_time = float(received_at_s)
            self._execution.ingest(message)
            self._execution.runtime.consume_received_input()
            self._last_input = identity
        finally:
            self._ingress_time = None

    def snapshot(self):
        """表示sampleはphysics/Task/予算を進めない。未選択時はNone。"""
        with self._mutating():
            if self._execution is None:
                return None
            return self._execution.sample(self._frame)

    def take_committed_projection(self):
        """直前commitでTaskが検査した表示を一度だけ渡す。可変操作後は再観測する。"""
        with self._mutating():
            value, self._committed_projection = self._committed_projection, None
            return value

    def advance(self, ticket, *, pending_input=None):
        """実commitとTaskを同じ実行で進め、空入力でもwall監督を行う。"""
        with self._mutating():
            self._check_ticket(ticket)
            self._committed_projection = None
            if self._status == "finalizing":
                return self._poll_recording()
            if self._status == "terminal":
                return self._result
            if self._status not in {"waiting_input", "running"}:
                raise RuntimeError("advance requires active trial")
            try:
                now = self._now()
                if now - self._started >= self._limits.wall_s:
                    return self._finish("wall_timeout")
                if self._status == "waiting_input" and now - self._input_accepting >= self._limits.input_wait_s:
                    return self._finish("input_wait_timeout")
                if pending_input is not None:
                    samples = pending_input()
                    if samples is None:
                        return None
                    if samples:
                        self._consume_batch(ticket, samples, check_freshness=False)
                        if len(samples) == 64:
                            return None
                before = self.tick_count
                def freshness_barrier():
                    samples = pending_input()
                    if samples is None:
                        return True
                    if not samples:
                        return False
                    self._consume_batch(ticket, samples, check_freshness=False)
                    return True
                self._execution.tick(freshness_barrier=None if pending_input is None else freshness_barrier)
                if self._execution.state == "faulted":
                    return self._finish("technical_invalid", error=self._execution.reason)
                if self._execution.state == "running":
                    self._status = "running"
                if self.tick_count > before:
                    self._frame += 1
                    exhausted = self.tick_count >= self._limits.max_ticks
                    self._committed_projection = self._execution.sample(self._frame, advance_task=True, budget_exhausted=exhausted)
                    task = self._execution.task_state
                    if task is not None and task.classification is not TaskTerminalClassification.RUNNING:
                        reason = ("task_success" if task.classification is TaskTerminalClassification.SUCCESS
                            else "technical_invalid" if task.classification is TaskTerminalClassification.TECHNICAL_INVALID
                            else "simulation_budget" if exhausted else "task_failure")
                        return self._finish(reason)
                    if exhausted:
                        return self._finish("simulation_budget")
                return None
            except Exception as exc:
                return self._finish("technical_invalid", error=str(exc))

    def abort(self, ticket):
        """課題成功を作らず、operator停止を結果として確定する。"""
        with self._mutating():
            self._check_ticket(ticket)
            if self._status == "finalizing":
                return self._poll_recording()
            if self._status not in {"waiting_input", "running"}:
                raise RuntimeError("abort requires active trial")
            return self._finish("operator_abort")

    def fail(self, ticket, error):
        """通信/有界受渡しの障害を、操作中止へ置換せず正式記録へ残す。"""
        with self._mutating():
            self._check_ticket(ticket)
            if self._status not in {"waiting_input", "running"}:
                raise RuntimeError("failure requires active trial")
            return self._finish("technical_invalid", error=str(error))

    def _recording_failed(self, exc, record=None):
        self._stop()
        self._error, self._status = str(exc), "recording_failed"
        failed = ({"schema_version": "trial-terminal/v1", **self._identity(),
            "runner_stop_reason": "start_failed", "task_outcome": self._execution.task_view}
            if record is None else record)
        self._result = TrialResult(canonical({**failed, "recording": "failed", "recording_error": str(exc)}))

    def _finish(self, reason, *, error=None):
        self._committed_projection = None
        if self._status in {"terminal", "recording_failed"}:
            return self._result
        self._status = "finalizing"
        self._stop()
        if self._execution.runtime.runtime.state == "faulted":
            reason = "technical_invalid"
            error = error or self._execution.runtime.runtime.reason
        try:
            self._now()
            self._frame += 1
            self._execution.sample(self._frame, advance_task=True, stopped_reason=reason)
            final_state = self._execution.instance.provider.trial_state()
        except Exception as exc:
            # 壊れたstateを正常な最終snapshotとして作らない。
            reason, error = "technical_invalid", f"{error or ''}; final state unavailable:{exc}"
            final_state = {"schema_version": "unavailable-trial-state/v1", "reason": str(exc)}
        record = {"schema_version": "trial-terminal/v1", **self._identity(),
            "runner_stop_reason": reason, "error": error,
            "task_outcome": self._execution.task_view, "ticks": self.tick_count,
            "simulation_time_s": self.tick_count * self._execution.profile.dt_s,
            "start_ref": self._start_ref, "terminal_monotonic_s": self._last_now,
            "input_accepting_monotonic_s": self._input_accepting}
        try:
            if self._async_terminal_recording:
                self._pending_terminal = record
                self._record_deadline = min(self._last_now + 2, self._started + self._limits.wall_s + 2)
                self._record_job.submit(final_state, record)
                return None
            self._result = self._recorder.terminal(final_state=final_state, record=record)
            self._status = "terminal"
        except Exception as exc:
            if self._record_job is not None:
                self._record_job.close()
                self._record_job = None
            self._recording_failed(exc, record)
            self._pending_terminal = None
        return self._result

    @property
    def recording_pending(self):
        return self._pending_terminal is not None

    def _poll_recording(self):
        """期限内のstagingをownerが公開する。期限後はprocessを回収して未確定を報告する。"""
        if self._record_job is None:
            return None
        try:
            if self._now() >= self._record_deadline:
                raise TimeoutError("terminal recording deadline exceeded; completion unconfirmed")
            result = self._record_job.poll()
            if result is None:
                return None
            self._record_job.close()
            self._record_job = None
            self._recorder.commit_terminal()
            self._result, self._status = result, "terminal"
        except Exception as exc:
            if self._record_job is not None:
                self._record_job.close()
                self._record_job = None
            self._recording_failed(exc, self._pending_terminal)
        self._pending_terminal = None
        return self._result

    def close(self):
        """active試行はabortを記録し、native modelと可変ownerへの参照を解放する。"""
        with self._mutating():
            if self._status in {"waiting_input", "running"}:
                self._finish("operator_abort")
            if self._record_job is not None:
                if self._pending_terminal is None:
                    self._record_job.close()
                    self._record_job = None
            if self._record_job is not None:
                from time import perf_counter, sleep
                deadline = perf_counter() + 2
                while self._record_job is not None and perf_counter() < deadline:
                    self._poll_recording()
                    if self._record_job is not None:
                        sleep(.005)
                if self._record_job is not None:
                    self._record_job.close()
                    self._record_job = None
                    self._recording_failed(TimeoutError("close recording deadline exceeded; completion unconfirmed"), self._pending_terminal)
            self._stop()
            self._execution = None
            self._recorder = self._last_input = None
            if self._status != "recording_failed":
                self._status = "closed"

    def discard_prepared(self):
        """開始前の準備だけを取り消す。過去結果は保持し、未開始trialの記録は作らない。"""
        with self._mutating():
            if self._status not in {"unselected", "ready"}:
                raise RuntimeError("discard requires an unstarted prepared trial")
            self._stop()
            self._execution = None
            self._condition = self._parameters = self._provenance = self._limits = None
            self._ticket = self._recorder = self._start_ref = self._started = self._last_input = None
            self._status = "unselected"
