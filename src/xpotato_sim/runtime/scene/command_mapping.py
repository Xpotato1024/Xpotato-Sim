"""固定対象面と前commitの原力を、独立したcommand Mappingへ結ぶ。"""
from copy import deepcopy
from hashlib import sha256
import json
from math import fsum, hypot, isfinite

from xpotato_sim.plugins.mappings._contact_resistance import approach_velocity, resistance_gain
from xpotato_sim.runtime.scene.objects import canonical
from xpotato_sim.schemas.coordinated import EndpointVelocity, identifier, vector


def validate_command_mapping(value):
    """software比較の明示条件だけを受理し、未知fieldや法線の暗黙補正を拒否する。"""
    if value is None:
        return None
    keys = {"schema_version", "mode", "frame", "targets", "fixed_gain", "half_gain_load_n",
        "minimum_gain", "normal_match_cos_min", "max_sample_age_s"}
    if type(value) is not dict or set(value) != keys:
        raise ValueError("explicit approach-resistance/v1 condition required")
    result = deepcopy(value)
    if (value["schema_version"] != "approach-resistance/v1" or value["frame"] != "mujoco_world"
            or value["mode"] not in {"baseline", "fixed_gain", "contact_dependent"}):
        raise ValueError("unknown command Mapping condition")
    for key in ("fixed_gain", "half_gain_load_n", "minimum_gain", "normal_match_cos_min", "max_sample_age_s"):
        v = value[key]
        if type(v) not in (int, float) or not isfinite(v):
            raise ValueError("finite command Mapping parameter required")
        result[key] = float(v)
    if (not 0 <= result["fixed_gain"] <= 1 or not 0 <= result["minimum_gain"] <= 1
            or result["half_gain_load_n"] <= 0 or not .99 <= result["normal_match_cos_min"] <= 1
            or not 0 < result["max_sample_age_s"] <= .2):
        raise ValueError("command Mapping parameter outside its software range")
    targets = result["targets"]
    if type(targets) is not dict or not 1 <= len(targets) <= 2:
        raise ValueError("one or two explicit command Mapping targets required")
    for endpoint, target in targets.items():
        identifier(endpoint, "endpoint")
        if type(target) is not dict or set(target) != {"object_id", "approach_normal_world"}:
            raise ValueError("explicit target face required")
        identifier(target["object_id"], "object")
        normal = vector(target["approach_normal_world"], 3, "approach normal")
        if abs(hypot(*normal) - 1) > 1e-8:
            raise ValueError("unit approach normal required")
        target["approach_normal_world"] = list(normal)
    return result


def profile_command_mapping(profile):
    """対象を現在の固定boxとTaskへ結び、動く面を初期版へ読み替えない。"""
    value = None if profile.command_mapping_json is None else validate_command_mapping(json.loads(profile.command_mapping_json))
    if value is None:
        return None
    scene = profile.scene_plan
    if scene is None or scene.dynamics is None:
        raise ValueError("command Mapping requires a dynamic scene")
    if set(value["targets"]) != set(profile.model_registration().endpoint_ids):
        raise ValueError("command Mapping must bind every endpoint")
    objects = {o.instance_id: o for o in scene.manifest.objects}
    task_targets = set(json.loads(profile.task_parameters_json)["target_object_ids"])
    for target in value["targets"].values():
        obj = objects.get(target["object_id"])
        if obj is None or obj.motion_type != "fixed" or obj.instance_id not in task_targets:
            raise ValueError("command Mapping requires an explicit fixed Task target")
    return value


class ContactResistanceCommandMapping:
    """原力の取得はruntime ownerへ残し、最新commandの因果traceを1件だけ保持する。"""
    def __init__(self, condition, *, epoch, model_sha256, scene_digest, settings_digest):
        self._condition = validate_command_mapping(condition)
        if self._condition is None:
            raise ValueError("explicit command Mapping required")
        self.digest = sha256(canonical(self._condition)).hexdigest()
        self.epoch, self.model, self.scene, self.settings = epoch, model_sha256, scene_digest, settings_digest
        self.latest = None

    def invalidate(self, reason):
        if self.latest is not None:
            self.latest = {**self.latest, "status": "invalidated", "reason": reason}

    def apply(self, commands, *, source_input, signal, contacts, epoch, before, tick, now_s, dt_s):
        """tick kの確定観測をcommand k+1へ使い、入力sampleとreceiptは変更しない。"""
        cfg = self._condition
        if epoch != self.epoch or before.model_sha256 != self.model or set(cfg["targets"]) != {c.endpoint_id for c in commands}:
            raise ValueError("command Mapping identity changed")
        if type(now_s) not in (int, float) or not isfinite(now_s) or type(dt_s) not in (int, float) or not isfinite(dt_s) or dt_s <= 0:
            raise ValueError("finite command Mapping clock/dt required")
        valid = (signal is not None and signal.status == "available" and signal.epoch == self.epoch
            and signal.model_sha256 == self.model and signal.scene_digest == self.scene and signal.settings_digest == self.settings
            and signal.control_tick == tick and signal.simulation_time_s == before.simulation_time_s
            and 0 <= now_s - signal.observed_monotonic_s <= cfg["max_sample_age_s"])
        pairs = {} if signal is None else {(p.endpoint_id, p.object_id): p for p in signal.pairs}
        mapped, trace = [], []
        for command in commands:
            if command.control_frame != "world":
                raise ValueError("command Mapping requires world velocity")
            target = cfg["targets"][command.endpoint_id]
            normal = target["approach_normal_world"]
            pair = pairs.get((command.endpoint_id, target["object_id"]))
            usable = valid and pair is not None and pair.status in {"active", "no_contact"}
            if usable and pair.status == "active":
                for index in pair.contact_indices:
                    contact = contacts[index]
                    direction = 1. if contact["role1"]["kind"] == "tool" else -1.
                    native = contact["normal_geom1_to_geom2_world"]
                    if fsum(direction*a*b for a, b in zip(native, normal, strict=True)) < cfg["normal_match_cos_min"]:
                        usable = False
            if cfg["mode"] == "contact_dependent" and not usable:
                self.invalidate("force_or_target_face_unavailable")
                raise ValueError("force_or_target_face_unavailable")
            load = None if not usable else max(0., -fsum(f*n for f, n in zip(pair.raw_force_world_n, normal, strict=True)))
            gain = (1. if cfg["mode"] == "baseline" else cfg["fixed_gain"] if cfg["mode"] == "fixed_gain" else
                resistance_gain(load, half_gain_load_n=cfg["half_gain_load_n"], minimum_gain=cfg["minimum_gain"]))
            velocity = approach_velocity(command.velocity_m_s, normal, gain=gain)
            mapped.append(EndpointVelocity(command.endpoint_id, velocity, "world"))
            trace.append({"endpoint_id": command.endpoint_id, "object_id": target["object_id"],
                "raw_velocity_world_m_s": command.velocity_m_s, "processed_velocity_world_m_s": velocity,
                "raw_force_world_n": None if pair is None else pair.raw_force_world_n,
                "compressive_load_n": load, "gain": gain, "force_status": "usable" if usable else "unavailable"})
        self.latest = {"schema_version": "approach-resistance-trace/v1", "condition_sha256": self.digest,
            "epoch": self.epoch, "status": "mapped", "reason": None, "command_tick": tick + 1,
            "source_tick": None if signal is None else signal.control_tick,
            "source_simulation_time_s": None if signal is None else signal.simulation_time_s,
            "source_observed_monotonic_s": None if signal is None else signal.observed_monotonic_s,
            "command_monotonic_s": now_s, "dt_s": dt_s, "endpoints": trace,
            "input_ref": {"source_epoch": source_input.source_epoch, "source_sequence": source_input.source_sequence,
                "source_timestamp_s": source_input.source_timestamp_s, "received_at_s": source_input.received_at_s,
                "source_payload_sha256": source_input.source_payload_sha256}}
        if source_input.ordering_receipt_s is not None:
            self.latest["input_ref"]["ordering_receipt_s"] = source_input.ordering_receipt_s
        return tuple(mapped)
