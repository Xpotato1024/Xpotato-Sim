"""Robot・入力・Mappingを選択しない、受動物理Sceneの正式なprofile。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from ipaddress import ip_address
from math import isfinite
from pathlib import Path
import json

from xpotato_sim.plugins.environments.catalog import resolve_environment_plugin
from xpotato_sim.runtime.experiment.contracts import PluginSelection, VersionedIdentity
from xpotato_sim.runtime.execution.scene_physics import ScenePhysicsSettings
from xpotato_sim.runtime.scene.contracts import ModelScenePlan, ObjectSceneProvider
from xpotato_sim.runtime.scene.objects import fields, number, canonical

SCENE_ONLY_SCHEMA = "xpotato-sim-launch-profile/v5"


@dataclass(frozen=True, slots=True)
class SceneOnlyProfile:
    """既存LaunchProfileと同じworkspace/条件/有限予算の概念を保つ、Robotなしモデル。"""
    source_path: Path
    workspace_path: Path
    name: str
    scene_plan: ModelScenePlan
    steps: int
    dt_s: float
    interval_s: float
    grace_period_s: float
    host: str
    web_port: int
    backend_port: int
    open_browser: bool
    document_json: str
    route: VersionedIdentity = VersionedIdentity("passive_scene", 1)
    mode: str = "simulation"
    robot: None = None
    model: None = None
    input_source: None = None
    mapping: None = None
    task_selection: None = None
    task_parameters_json: str = "{}"
    display_comparison_json: None = None
    command_mapping_json: None = None

    @property
    def configuration_sha256(self):
        return sha256(self.document_json.encode("utf-8")).hexdigest()

    def build_model(self):
        from xpotato_sim.runtime.execution.scene_only import build_scene_only_model
        return build_scene_only_model(self.scene_plan)

    def bind_scene_task(self):
        return None

    def to_dict(self):
        return {"configuration": json.loads(self.document_json),
                "configuration_sha256": self.configuration_sha256,
                "profile_path": str(self.source_path),
                "workspace_path": str(self.workspace_path),
                "resolved": {"command_route": self.route.canonical_id, "robot": None,
                             "input": None, "mapping": None, "task": None,
                             "scene": self.scene_plan.manifest.to_document(),
                             "scene_digest": self.scene_plan.manifest.digest,
                             "dynamics": self.scene_plan.dynamics.to_document(),
                             "dynamics_digest": self.scene_plan.dynamics.digest,
                             "simulation_duration_s": self.steps * self.dt_s,
                             "scheduled_duration_s": self.steps * self.interval_s,
                             "physical_output": "disabled"}}


def decode_scene_only_profile(raw, *, source_path: Path) -> SceneOnlyProfile:
    data = fields(raw, {"schema_version", "name", "workspace", "mode", "robot", "model",
                        "input", "mapping", "task", "environment", "execution", "web"}, "scene-only profile")
    if data["schema_version"] != SCENE_ONLY_SCHEMA:
        raise ValueError("scene-only profile version mismatch")
    if any(data[k] is not None for k in ("robot", "model", "input", "mapping", "task")):
        raise ValueError("scene-only requires explicit null Robot, model, input, Mapping and Task")
    if data["mode"] != "simulation":
        raise ValueError("scene-only supports finite simulation only")
    from xpotato_sim.runtime.composition.launch_profile import _NAME
    if type(data["name"]) is not str or not _NAME.fullmatch(data["name"]):
        raise ValueError("invalid scene-only name")
    if type(data["workspace"]) is not str or not data["workspace"]:
        raise ValueError("workspace must be explicit")
    path = Path(source_path).resolve()
    workspace = (path.parent / data["workspace"]).resolve()
    if not (workspace / "pyproject.toml").is_file() or not (workspace / "apps/mujoco-viewer/package.json").is_file():
        raise ValueError("workspace must be a Sim checkout")
    env = fields(data["environment"], {"plugin", "parameters", "robot_collision_profile"}, "environment")
    if env["robot_collision_profile"] is not None:
        raise ValueError("scene-only cannot request Robot collider bindings")
    selection = fields(env["plugin"], {"name", "version"}, "environment plugin")
    if type(selection["name"]) is not str or type(selection["version"]) is not int:
        raise ValueError("versioned Environment selection required")
    plugin = resolve_environment_plugin(PluginSelection(selection["name"], selection["version"]))
    if not isinstance(plugin.scene_provider, ObjectSceneProvider):
        raise ValueError("registered object Environment required")
    manifest = plugin.scene_provider.resolve_parameters(env["parameters"])
    if manifest.world is None:
        raise ValueError("scene-only requires an explicit world")
    ex = fields(data["execution"], {"steps", "dt_s", "interval_s", "grace_period_s", "physics"}, "execution")
    if type(ex["steps"]) is not int or not 1 <= ex["steps"] <= 2**31-1:
        raise ValueError("bounded scene-only steps required")
    dt, interval, grace = (number(ex[k], positive=True) for k in ("dt_s","interval_s","grace_period_s"))
    if not isfinite(ex["steps"]*dt) or not isfinite(ex["steps"]*interval):
        raise ValueError("scene-only duration overflow")
    physics = ScenePhysicsSettings.from_document(ex["physics"])
    physics.substeps(dt)
    web = fields(data["web"], {"host", "port", "websocket_port", "open_browser"}, "web")
    if type(web["host"]) is not str or not ip_address(web["host"]).is_loopback:
        raise ValueError("numeric loopback web host required")
    for key in ("port","websocket_port"):
        if type(web[key]) is not int or not 1 <= web[key] <= 65535:
            raise ValueError("bounded web port required")
    if web["port"] == web["websocket_port"] or type(web["open_browser"]) is not bool:
        raise ValueError("distinct web ports and explicit browser flag required")
    plan = ModelScenePlan(manifest, plugin.scene_provider, "no_robot", physics)
    return SceneOnlyProfile(path, workspace, data["name"], plan, ex["steps"], dt, interval, grace,
                            web["host"], web["port"], web["websocket_port"], web["open_browser"],
                            canonical(data).decode("utf-8"))
