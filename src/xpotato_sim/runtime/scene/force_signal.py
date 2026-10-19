"""確定済みdynamic観測から手先・対象別の反力を導出する。physics/Taskを所有しない。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import math
import re

from xpotato_sim.runtime.contact.force_filter import ForceVectorFilter
from xpotato_sim.runtime.contact.virtual_reaction_force import (
    VirtualReactionForceConfig, VirtualReactionForceFrame,
)
from xpotato_sim.runtime.scene.objects import canonical


def default_force_config(max_gap_s: float) -> VirtualReactionForceConfig:
    """初期条件はworld表現の原力を維持し、加工する場合も設定をdigestに含める。"""
    return VirtualReactionForceConfig(VirtualReactionForceFrame.MUJOCO_WORLD,
        0., 0., 1, None, None, max_gap_s)


def force_condition(profile) -> dict | None:
    """同一sceneの全手先とTaskの明示対象集合をfreezeする。非対象の台を混ぜない。"""
    if profile.scene_plan is None:
        return None
    import json
    parameters = json.loads(profile.task_parameters_json)
    targets = parameters.get("target_object_ids")
    if type(targets) is not list or not targets:
        raise ValueError("force signal requires explicit target_object_ids")
    return {"schema_version": "scene-force-condition/v1", "frame": "mujoco_world",
        "sign_convention": "object_on_tool", "unit": "N",
        "endpoint_ids": sorted(profile.side_to_endpoint.values()),
        "target_object_ids": sorted(targets),
        "filter": default_force_config(profile.max_input_age_s).to_document(),
        "filter_clock": "simulation_time_s",
        "observation_phase": "post_control_commit_last_substep",
        "aggregation": "vector_sum_and_sum_of_compressive_normal_components"}


def _finite(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("finite force evidence number required")
    return float(value)


def _vector(value):
    if type(value) not in (tuple, list) or len(value) != 3:
        raise ValueError("three force evidence components required")
    return tuple(_finite(v) for v in value)


@dataclass(frozen=True, slots=True)
class ForcePairSignal:
    """一組の対象物→手先反力。部分欠測では和と荷重を両方nullにする。"""
    endpoint_id: str
    object_id: str
    status: str
    contact_indices: tuple[int, ...] = ()
    unavailable_contacts: int = 0
    raw_force_world_n: tuple[float, float, float] | None = None
    normal_load_n: float | None = None
    force_world_n: tuple[float, float, float] | None = None
    anchor_world_m: tuple[float, float, float] | None = None
    filtered: bool = False
    deadbanded: bool = False
    rate_limited: bool = False
    saturated: bool = False


@dataclass(frozen=True, slots=True)
class SceneForceSignal:
    """元sampleの時計とidentityを保持する不変DTO。表示再送でsampleを更新しない。"""
    manifest_sha256: str
    model_sha256: str
    scene_digest: str
    settings_digest: str | None
    epoch: str
    control_tick: int
    source_frame_index: int
    simulation_time_s: float
    observed_monotonic_s: float
    status: str
    pairs: tuple[ForcePairSignal, ...]
    reason: str | None = None

    def to_document(self):
        return {"schema_version": "scene-force-signal/v1", "frame": "mujoco_world",
            "unit": "N", "sign_convention": "object_on_tool", **asdict(self)}


class SceneForceProcessor:
    """有界な手先×対象集合と最新sampleのみを保持する。描画からの再stepはない。"""
    def __init__(self, *, model_sha256: str, scene_digest: str, settings_digest: str | None,
                 endpoint_ids: tuple[str, ...], object_ids: tuple[str, ...],
                 target_object_ids: tuple[str, ...], config: VirtualReactionForceConfig):
        if model_sha256 is None or scene_digest is None:
            raise ValueError("model and scene digest are required")
        for digest in (model_sha256, scene_digest, settings_digest):
            if digest is not None and (type(digest) is not str or not re.fullmatch(r"[a-f0-9]{64}", digest)):
                raise ValueError("force signal requires canonical digest")
        for values in (endpoint_ids, object_ids, target_object_ids):
            if (type(values) is not tuple or not 1 <= len(values) <= 32
                    or len(set(values)) != len(values)
                    or any(type(v) is not str or not v for v in values)):
                raise ValueError("bounded distinct force signal identities required")
        if not set(target_object_ids) <= set(object_ids):
            raise ValueError("unknown force target")
        if (type(config) is not VirtualReactionForceConfig
                or config.output_frame is not VirtualReactionForceFrame.MUJOCO_WORLD
                or config.smoothing_window_samples > 256):
            raise ValueError("bounded world-frame force config required")
        self.model, self.scene, self.settings = model_sha256, scene_digest, settings_digest
        self.endpoints, self.objects = endpoint_ids, object_ids
        self.keys = tuple((e, o) for e in sorted(endpoint_ids) for o in sorted(target_object_ids))
        self.config = config
        self.manifest = {"schema_version": "scene-force-manifest/v1", "model_sha256": self.model,
            "scene_digest": self.scene, "settings_digest": self.settings,
            "pairs": self.keys, "filter": config.to_document(), "frame": "mujoco_world",
            "sign_convention": "object_on_tool", "unit": "N", "filter_clock": "simulation_time_s",
            "observation_phase": "post_control_commit_last_substep",
            "aggregation": "vector_sum_and_sum_of_compressive_normal_components"}
        self.digest = sha256(canonical(self.manifest)).hexdigest()
        self._filters = {key: ForceVectorFilter() for key in self.keys}
        self.reset()

    def reset(self):
        self.latest: SceneForceSignal | None = None
        for processor in self._filters.values():
            processor.reset()

    def _empty(self, epoch, tick, frame, time, now, status, reason=None):
        for processor in self._filters.values():
            processor.reset()
        return SceneForceSignal(self.digest, self.model, self.scene, self.settings,
            epoch, tick, frame, time, now, status,
            tuple(ForcePairSignal(e, o, status) for e, o in self.keys), reason)

    def invalidate(self, reason="stopped"):
        """STOP時は最後の元sample identityを維持して表示とfilterを失効させる。"""
        if self.latest is not None:
            old = self.latest
            self.latest = self._empty(old.epoch, old.control_tick, old.source_frame_index,
                old.simulation_time_s, old.observed_monotonic_s, "unavailable", reason)

    def process(self, observation, *, epoch, tick, frame_index, simulation_time_s, now_s):
        """原力を変更せず導出。同tickの再送は同一sample、期限後はstaleを返す。"""
        time, now = _finite(simulation_time_s), _finite(now_s)
        if (not isinstance(epoch, str) or not epoch or time < 0 or now < 0
                or type(tick) is not int or tick < 0 or type(frame_index) is not int or frame_index < 0):
            raise ValueError("valid force sample identity required")
        if self.latest is not None and epoch != self.latest.epoch:
            self.reset()
        previous = self.latest
        def empty(status, reason):
            return self._empty(epoch, tick, frame_index, time, now, status, reason)
        try:
            if previous is not None and (tick < previous.control_tick or time < previous.simulation_time_s
                    or frame_index < previous.source_frame_index or now < previous.observed_monotonic_s):
                raise ValueError("force sample clock/order regressed")
            if observation is None:
                result = empty("not_evaluated_kinematic" if self.settings is None else "missing", "force evidence missing")
            else:
                if (type(observation) is not dict or observation.get("schema_version") != "scene-dynamics-observation/v1"
                        or observation.get("model_sha256") != self.model or observation.get("scene_digest") != self.scene
                        or observation.get("settings_digest") != self.settings or self.settings is None
                        or observation.get("frame_index") != frame_index or observation.get("simulation_time_s") != time
                        or observation.get("semantics") != "coordinated_actuator_servo_dynamic/v1"):
                    raise ValueError("force observation identity mismatch")
                if previous is not None and tick == previous.control_tick:
                    if time != previous.simulation_time_s:
                        raise ValueError("same force tick has a different simulation time")
                    if now - previous.observed_monotonic_s > self.config.max_inter_sample_gap_s:
                        self.invalidate("force sample expired")
                        self.latest = replace(self.latest, status="stale",
                            pairs=tuple(replace(p, status="stale") for p in self.latest.pairs))
                    return self.latest
                delta = None if previous is None else time - previous.simulation_time_s
                if previous is not None and (delta <= 0 or delta > self.config.max_inter_sample_gap_s
                        or now - previous.observed_monotonic_s > self.config.max_inter_sample_gap_s):
                    result = empty("stale", "force observation gap exceeds frozen limit")
                else:
                    pairs = self._aggregate(observation.get("contacts"), delta)
                    result = SceneForceSignal(self.digest, self.model, self.scene, self.settings,
                        epoch, tick, frame_index, time, now, "available", pairs)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            result = empty("invalid", str(exc))
        self.latest = result
        return result

    def _aggregate(self, contacts, delta):
        if type(contacts) is not list or len(contacts) > 4096:
            raise ValueError("bounded native contact evidence required")
        groups = {key: [] for key in self.keys}
        for index, contact in enumerate(contacts):
            if type(contact) is not dict:
                raise ValueError("contact record required")
            a, b = contact["role1"], contact["role2"]
            for role in (a, b):
                if (type(role) is not dict or set(role) != {"kind", "id"}
                        or type(role["id"]) is not str or not role["id"]
                        or role["kind"] not in {"tool", "object", "support"}
                        or role["kind"] == "tool" and role["id"] not in self.endpoints
                        or role["kind"] == "object" and role["id"] not in self.objects):
                    raise ValueError("unbound contact role")
            if (a["kind"], b["kind"]) == ("tool", "object"):
                key, sign = (a["id"], b["id"]), -1.
            elif (a["kind"], b["kind"]) == ("object", "tool"):
                key, sign = (b["id"], a["id"]), 1.
            else:
                continue
            if key not in groups:
                continue
            normal = _vector(contact["normal_geom1_to_geom2_world"])
            if abs(math.fsum(v*v for v in normal) - 1) > 1e-8:
                raise ValueError("unit native contact normal required")
            point = _vector(contact["point_world_m"])
            status = contact["status"]
            if status == "measurement_unavailable":
                if contact["force_on_geom2_world_n"] is not None or contact["torque_on_geom2_world_nm"] is not None:
                    raise ValueError("unavailable contact contains force")
                groups[key].append((index, None, None, point))
            elif status == "measured":
                native = _vector(contact["force_on_geom2_world_n"])
                _vector(contact["torque_on_geom2_world_nm"])
                # geom2への力とgeom1→geom2法線の内積は圧縮で正。両geom順で同じ荷重になる。
                load = math.fsum(f*n for f, n in zip(native, normal, strict=True))
                if load < -1e-9:
                    raise ValueError("negative compressive contact load")
                groups[key].append((index, tuple(sign*f for f in native), max(0., load), point))
            else:
                raise ValueError("unknown native force status")
        result = []
        for key, records in groups.items():
            e, o = key
            missing = sum(record[1] is None for record in records)
            if missing:
                self._filters[key].reset()
                result.append(ForcePairSignal(e, o, "partial_missing" if missing < len(records) else "measurement_unavailable",
                    tuple(r[0] for r in records), missing))
            elif not records:
                self._filters[key].reset()
                result.append(ForcePairSignal(e, o, "no_contact", raw_force_world_n=(0.,0.,0.),
                    normal_load_n=0., force_world_n=(0.,0.,0.)))
            else:
                raw = tuple(math.fsum(r[1][i] for r in records) for i in range(3))
                load = math.fsum(r[2] for r in records)
                point = tuple(math.fsum(r[3][i] for r in records)/len(records) for i in range(3))
                filtered = self._filters[key].apply(raw, config=self.config, sample_delta=delta)
                result.append(ForcePairSignal(e, o, "active", tuple(r[0] for r in records), 0,
                    raw, load, filtered.force, point, filtered.filtered, filtered.deadbanded,
                    filtered.rate_limited, filtered.clamped))
        return tuple(result)
