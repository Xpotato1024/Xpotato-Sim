"""要求検証、操作権、revision/epochと制御状態を所有し、physicsは実行しない。"""
from __future__ import annotations

from collections import OrderedDict, deque
import hmac
import json
from math import isfinite
import re
import secrets
from time import monotonic

from xpotato_sim.runtime.composition.launch_profile import list_launch_profiles, load_launch_profile
from xpotato_sim.runtime.experiment.trial_condition import TrialLimits, resolve_trial_profile
from xpotato_sim.runtime.experiment.edited_condition import preset_condition, resolve_condition, descriptors, condition_diff
from xpotato_sim.schemas import parse_viewer_control_message_json

ACTIVE = {"waiting_input", "running", "finalizing"}
INPUT_ACTIVE = {"waiting_input", "running"}
INPUT_FINISHED = {"terminal", "recording_failed"}
ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def validate_late_input(message):
    """終了済み同ticketの有効なGamepadだけを棄却する。不正入力は正常化しない。"""
    if not isinstance(message, str) or len(message.encode("utf-8")) > 65536:
        raise ValueError("bounded Gamepad message required")
    parsed = parse_viewer_control_message_json(message)
    if (parsed.gamepad is None or parsed.gamepad.stale or not parsed.gamepad.connected
            or parsed.source_kind != "gamepad" or parsed.sequence is None
            or not isinstance(parsed.metadata.get("viewer_provider_session_id"), str)
            or not parsed.metadata["viewer_provider_session_id"]):
        raise ValueError("unavailable or invalid late Gamepad input")


def decode_request(raw):
    """外部入力はJSON object、有限値、既知fieldだけ。例外は接続内で応答する。"""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
        raise ValueError("要求は64 KiB以内のJSON文字列です")
    def unique(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise ValueError("重複field")
            obj[k] = v
        return obj
    def invalid(value):
        raise ValueError("非有限値は禁止です")
    def finite_float(value):
        number = float(value)
        if not isfinite(number):
            invalid(value)
        return number
    obj = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite_float)
    if type(obj) is not dict or not isinstance(obj.get("op"), str):
        raise ValueError("opを持つobjectが必要です")
    return obj


def profile_catalog():
    result = []
    for name in list_launch_profiles():
        try:
            p = load_launch_profile(name)
            if p.model is None:
                raise ValueError("旧形式v1はWorkbench対象外です。既存のapp/replay経路で利用してください")
            p = resolve_trial_profile(p)
            result.append({"id": name, "available": True, "reason": None,
                "model": p.model.plugin_id, "dt_s": p.dt_s, "ticks": p.steps})
        except Exception as exc:
            result.append({"id": name, "available": False, "reason": str(exc)})
    return result


class WorkbenchControl:
    """制御revision、connection所有権、bounded履歴。physicsは所有しない。"""
    def __init__(self, catalog, capability, *, require_renderer=True, launcher_limits=None):
        self.catalog = catalog
        self.capability = capability
        self.require_renderer = require_renderer
        self.owner = None
        self.revision = 0
        self.generation = 0
        self.busy = None
        self.busy_operation = None
        self.history = OrderedDict()
        self.results = deque(maxlen=32)
        self.state = {"phase": "unselected", "ticket": None, "ticks": 0, "profile_id": None,
                      "result": None, "error": None}
        self.renderer_epoch = None
        self.stop_id = None
        self.stop_deadline = None
        self.dead = False
        self.next_condition = None
        self.launcher_limits = launcher_limits or {}
        self.last_input_receipt_s = None

    def complete(self, rid, error=None):
        if rid in self.history:
            fingerprint, response = self.history[rid]
            self.history[rid] = (fingerprint, {**response, "completed": True, "error": error})

    def supervise_stop(self, rid=None):
        """STOP固有IDだけで監督する。通常status/prepare完了は期限を解除しない。"""
        if self.stop_id is not None:
            return None
        if self.busy != rid:
            self.complete(self.busy, "停止要求により取り消しました")
        self.generation += 1
        self.stop_id = rid or "stop-" + secrets.token_hex(16)
        self.stop_deadline = monotonic() + 2
        self.busy, self.busy_operation = self.stop_id, "stop"
        self.renderer_epoch = None
        return {"op": "stop", "id": self.stop_id, "generation": self.generation}

    def worker_event(self, event):
        if self.dead or event.get("generation") != self.generation:
            return False
        if self.stop_id is not None:
            if (event.get("id") != self.stop_id or event.get("completed_op") != "stop"
                    or event.get("error") or event["state"]["phase"] not in {"unselected", "terminal", "recording_failed", "faulted"}):
                return False
            self.stop_id = self.stop_deadline = None
        self.update(event)
        return True

    def status(self):
        return {"type": "status", **self.state, "revision": self.revision,
                "generation": self.generation, "busy": self.busy, "profiles": self.catalog,
                "busy_operation": self.busy_operation,
                "service_last_input_receipt_s": self.last_input_receipt_s,
                "results": list(self.results), "renderer_ready": self.renderer_epoch is not None,
                "history_size": len(self.history), "history_limit": 128}

    def authorize(self, client, request):
        capability = request.get("capability")
        if not isinstance(capability, str) or not hmac.compare_digest(capability, self.capability):
            raise ValueError("制御資格がありません")
        if self.owner != client:
            raise ValueError("この接続は制御所有者ではありません")

    def command(self, client, r):
        op = r["op"]
        if op == "status":
            if set(r) != {"op"}:
                raise ValueError("未知field")
            return self.status(), None
        if op == "claim":
            if set(r) != {"op", "capability"}:
                raise ValueError("未知field")
            value = r["capability"]
            if not isinstance(value, str) or not hmac.compare_digest(value, self.capability):
                raise ValueError("制御資格がありません")
            if self.owner not in (None, client) or self.state["phase"] in ACTIVE or self.busy:
                raise ValueError("別の接続または試行が所有しています")
            self.owner = client
            return {"type": "claimed"}, None
        self.authorize(client, r)
        if op in {"edit", "clone", "export", "import", "diff"}:
            return self._editor_command(r)
        if op == "input":
            return self._input_command(r)
        return self._lifecycle_command(r)

    def _editor_command(self, r):
        """認可済みeditor要求を同じrevisionと次条件へ適用する。"""
        op = r["op"]
        expected = {"op", "capability", "revision", "ticket"}
        if "request_id" in r:
            expected.add("request_id")
            if not isinstance(r["request_id"], str) or not ID.fullmatch(r["request_id"]):
                raise ValueError("有効なeditor要求IDが必要です")
        correlation = {"request_id": r["request_id"]} if "request_id" in r else {}
        if op in {"edit", "import", "diff"}:
            expected.add("condition")
        if op == "clone":
            expected.add("profile_id")
        if set(r) != expected or type(r["revision"]) is not int or r["revision"] != self.revision or r["ticket"] != self.state["ticket"]:
            raise ValueError("未知fieldまたは旧revision/ticketです")
        if self.busy or self.state["phase"] not in {"unselected", "ready", "terminal"}:
            raise ValueError("条件編集は停止中・記録確定後だけです")
        if op == "diff":
            if self.next_condition is None:
                raise ValueError("次条件がありません")
            return {"type": "condition_diff", **correlation, "changes": condition_diff(self.state.get("applied_condition") or self.next_condition, r["condition"])}, None
        if op == "export":
            if self.next_condition is None:
                raise ValueError("次条件がありません")
        else:
            if op == "clone":
                if r["profile_id"] not in list_launch_profiles():
                    raise ValueError("cloneは登録preset IDだけです。server pathは受け付けません")
                selected = load_launch_profile(r["profile_id"])
                value = preset_condition(r["profile_id"], TrialLimits(self.launcher_limits.get("ticks") or selected.steps,
                    self.launcher_limits.get("input_wait_s", 5), self.launcher_limits.get("wall_s", 360),
                    self.launcher_limits.get("prepare_s", 30)))
            else:
                value = r["condition"]
            if op == "import":
                if type(value) is not str:
                    raise ValueError("importはJSON文字列です。server pathは受け付けません")
                value = value.encode("utf-8")
            self.next_condition = resolve_condition(value)[2]
            self.revision += 1
        return {"type": "edited_condition", **correlation, "condition": self.next_condition,
            "descriptors": descriptors(self.next_condition), "revision": self.revision,
            "generation": self.generation, "ticket": self.state["ticket"]}, None

    def _input_command(self, r):
        """認可済み入力をticketとphaseで検査し、元receiptを保持する。"""
        op = r["op"]
        if set(r) != {"op", "capability", "ticket", "message"}:
            raise ValueError("未知field")
        if r["ticket"] is None or r["ticket"] != self.state["ticket"]:
            raise ValueError("旧epochまたは実行前の入力です")
        if not isinstance(r["message"], str):
            raise ValueError("入力messageは文字列です")
        if self.state.get("fixture_mode"):
            raise ValueError("明示fixture実行ではbrowser入力を受け付けません")
        if ((not self.busy and self.state["phase"] in INPUT_FINISHED)
                or (self.stop_id is not None and self.state["phase"] in INPUT_ACTIVE | INPUT_FINISHED)):
            validate_late_input(r["message"])
            return self.status(), None
        if self.busy or self.state["phase"] not in INPUT_ACTIVE:
            raise ValueError("旧epochまたは実行前の入力です")
        self.last_input_receipt_s = monotonic()
        return None, {"op": op, "ticket": r["ticket"], "message": r["message"],
                      "received_at_s": self.last_input_receipt_s}
    def _lifecycle_command(self, r):
        """認可済み操作の履歴、revision、開始・停止gateを所有する。"""
        op = r["op"]
        fields = {"op", "capability", "id", "revision", "ticket"}
        if op == "prepare":
            fields.add("profile_id")
            if "condition" in r:
                fields.add("condition")
        if set(r) != fields or op not in {"prepare", "start", "retry", "stop", "renderer_ready"}:
            raise ValueError("未知operationまたはfieldです")
        rid = r["id"]
        if not isinstance(rid, str) or not ID.fullmatch(rid):
            raise ValueError("有効な要求IDが必要です")
        fingerprint = json.dumps({k: v for k, v in r.items() if k != "capability"}, sort_keys=True)
        if rid in self.history:
            old, response = self.history[rid]
            if old != fingerprint:
                raise ValueError("同一要求IDの内容変更は禁止です")
            return response, None
        if type(r["revision"]) is not int or r["revision"] != self.revision:
            raise ValueError("旧revisionです。状態を再取得してください")
        if r["ticket"] != self.state["ticket"]:
            raise ValueError("旧trial ticketです")
        phase = self.state["phase"]
        if op == "stop" and self.stop_id is not None:
            raise ValueError("停止要求は受付済みです")
        if op != "stop" and self.busy:
            raise ValueError("処理中です")
        if op == "prepare":
            if phase not in {"unselected", "ready", "terminal", "faulted", "recording_failed"}:
                raise ValueError("停止中の正常なownerだけが準備できます")
            if not any(p["id"] == r["profile_id"] and p["available"] for p in self.catalog):
                raise ValueError("利用可能な登録profile IDが必要です")
            if "condition" in r:
                _, _, normalized = resolve_condition(r["condition"])
                if normalized["preset_id"] != r["profile_id"]:
                    raise ValueError("preset/condition ID不一致")
                r = {**r, "condition": normalized}
            self.generation += 1
            self.renderer_epoch = None
        elif op == "retry":
            if phase != "terminal":
                raise ValueError("保存済み終端だけが再試行できます")
            self.renderer_epoch = None
        elif op == "renderer_ready":
            if phase != "ready" or r["ticket"] is None:
                raise ValueError("ready試行の初期scene ACKが必要です")
            self.renderer_epoch = r["ticket"]["epoch"]
        elif op == "start":
            if phase != "ready" or (self.require_renderer and self.renderer_epoch != r["ticket"]["epoch"]):
                raise ValueError("初期scene・shader準備完了後に開始してください")
        elif op == "stop" and not self.busy and phase not in ACTIVE | {"ready"}:
            raise ValueError("停止対象がありません")
        self.revision += 1
        response = {"type": "accepted", "id": rid, "revision": self.revision,
                    "completed": op == "renderer_ready"}
        self.history[rid] = (fingerprint, response)
        while len(self.history) > 128:
            self.history.popitem(last=False)
        if op == "renderer_ready":
            return response, None
        if op == "stop":
            return response, self.supervise_stop(rid)
        self.busy = rid
        self.busy_operation = op
        command = {k: v for k, v in r.items() if k != "capability"}
        command["generation"] = self.generation
        return response, command

    def update(self, event):
        self.state.update(event["state"])
        if event.get("id") == self.busy:
            self.busy = None
            self.busy_operation = None
        rid = event.get("id")
        self.complete(rid, event.get("error"))
        result = self.state.get("result")
        if result and not any(x["trial_id"] == result["trial_id"] for x in self.results):
            self.results.append(result)

