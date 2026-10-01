"""旧profileの保存byteを保持し、実行fieldから別の有限試行条件を固定する。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite

from xpotato_sim.plugins.environments.catalog import resolve_environment_plugin
from xpotato_sim.runtime.composition.launch_profile import (
    LaunchProfile, decode_launch_profile,
)
from xpotato_sim.runtime.experiment.contracts import PluginSelection
from xpotato_sim.runtime.scene.objects import canonical


@dataclass(frozen=True, slots=True)
class TrialLimits:
    """commit数と実時間監督を分離する。無期限値は受け付けない。"""
    max_ticks: int
    input_wait_s: float = 5.0
    wall_s: float = 60.0
    prepare_s: float = 30.0

    def __post_init__(self):
        if type(self.max_ticks) is not int or not 1 <= self.max_ticks <= 2**31 - 1:
            raise ValueError("bounded positive max_ticks required")
        for name in ("input_wait_s", "wall_s", "prepare_s"):
            value = getattr(self, name)
            if type(value) not in (float, int) or not isfinite(value) or value <= 0:
                raise ValueError(f"finite positive {name} required")
            object.__setattr__(self, name, float(value))


def _selection(value):
    return {"name": value.plugin_id, "version": value.contract_version}


def resolve_trial_profile(profile: LaunchProfile) -> LaunchProfile:
    """実効fieldを既存decoderで再検証する。元JSONは来歴以外の正本にしない。"""
    if type(profile) is not LaunchProfile or profile.model is None:
        raise ValueError("finite trial requires a named-model LaunchProfile v2/v3/v4; v1 unsupported")
    origin = json.loads(profile.document_json)
    # scene providerのidentityだけはcatalogで照合し、任意objectへ差替えさせない。
    scene = profile.scene_plan
    env = None
    if scene is not None:
        env = origin.get("environment")
        if not isinstance(env, dict):
            raise ValueError("scene requires explicit registered Environment provenance")
        selection = PluginSelection(env["plugin"]["name"], env["plugin"]["version"])
        if resolve_environment_plugin(selection).scene_provider is not scene.provider:
            raise ValueError("scene provider differs from registered Environment")
    if (scene is None) != (profile.task_selection is None):
        raise ValueError("scene and Task must be selected together")
    raw = {
        "schema_version": "xpotato-sim-launch-profile/v2" if scene is None else
            ("xpotato-sim-launch-profile/v3" if scene.dynamics is None else "xpotato-sim-launch-profile/v4"),
        "name": profile.name, "workspace": str(profile.workspace_path), "mode": profile.mode,
        "robot": _selection(profile.robot), "model": _selection(profile.model),
        "input": {"plugin": _selection(profile.input_source), "provider": profile.provider_id, "preset": profile.preset},
        "mapping": {"plugin": _selection(profile.mapping), "parameters": profile.mapping_parameters},
        "coordination": {**json.loads(profile.coordination_json), "epoch": "condition-ready"},
        "execution": {"steps": profile.steps, "dt_s": profile.dt_s, "interval_s": profile.interval_s,
            "grace_period_s": profile.grace_period_s},
        "web": {"host": profile.host, "port": profile.web_port,
            "websocket_port": profile.backend_port, "open_browser": profile.open_browser},
    }
    if scene is not None:
        raw["environment"] = {"plugin": env["plugin"], "parameters": {"scene": scene.manifest.to_document()},
            "robot_collision_profile": scene.collision_profile}
        raw["task"] = {"plugin": _selection(profile.task_selection),
            "parameters": json.loads(profile.task_parameters_json)}
        if scene.dynamics is not None:
            raw["execution"]["dynamics"] = scene.dynamics.to_document()
    validated = decode_launch_profile(canonical(raw), source_path=profile.source_path)
    if validated.route != profile.route:
        raise ValueError("resolved execution route differs from effective profile")
    return validated


def semantic_parameters(profile, limits):
    """build前の同条件判定。モデル構築後にartifactと数値条件を追加する。"""
    if type(limits) is not TrialLimits or not isfinite(limits.max_ticks * profile.dt_s):
        raise ValueError("finite trial limits required")
    scene = profile.scene_plan
    registration = profile.model_registration()
    return {
        "schema_version": "trial-condition/v1",
        "robot": _selection(profile.robot), "model": _selection(profile.model),
        "model_configuration": json.loads(registration.configuration_json),
        "model_configuration_sha256": registration.configuration_sha256,
        "input": {"plugin": _selection(profile.input_source), "provider": profile.provider_id},
        "mapping": {"plugin": _selection(profile.mapping),
            "parameters": json.loads(profile.effective_parameters_json)},
        "route": profile.route.canonical_id, "side_to_endpoint": profile.side_to_endpoint,
        "dt_s": profile.dt_s, "max_input_age_s": profile.max_input_age_s,
        "limits": asdict(limits), "simulation_budget_s": limits.max_ticks * profile.dt_s,
        "environment": None if scene is None else json.loads(profile.document_json)["environment"]["plugin"],
        "scene": None if scene is None else scene.manifest.to_document(),
        "collision_profile": None if scene is None else scene.collision_profile,
        "dynamics": None if scene is None or scene.dynamics is None else scene.dynamics.to_document(),
        "task": None if profile.task_selection is None else {"plugin": _selection(profile.task_selection),
            "parameters": json.loads(profile.task_parameters_json)},
        "evaluation": None, "display_comparison": None, "physical_output": "disabled",
    }


@dataclass(frozen=True, slots=True)
class FrozenTrialCondition:
    """canonical byteが正本。呼出側へmutableな実行設定を渡さない。"""
    document: bytes

    @property
    def digest(self):
        return sha256(self.document).hexdigest()

    def to_document(self):
        return json.loads(self.document)


def freeze_condition(parameters, instance):
    provider = instance.provider
    if not callable(getattr(provider, "numerical_condition", None)) or not callable(getattr(provider, "trial_state", None)):
        raise ValueError("model provider does not support finite trial state/condition recording")
    return FrozenTrialCondition(canonical({**parameters,
        "model_sha256": provider.snapshot().model_sha256,
        "numerical_condition": provider.numerical_condition()}))
