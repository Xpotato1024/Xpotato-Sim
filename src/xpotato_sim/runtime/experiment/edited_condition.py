"""WorkbenchとCLIの展開済み条件。既存profile decoderを唯一のcomposition入口にする。"""
from dataclasses import asdict
from copy import deepcopy
from hashlib import sha256
import json

from xpotato_sim.runtime.composition.launch_profile import (
    decode_launch_profile, load_launch_profile, list_launch_profiles,
)
from xpotato_sim.runtime.experiment.trial_condition import TrialLimits, resolve_trial_profile
from xpotato_sim.runtime.scene.objects import canonical, fields, strict_json, number, decode_object_scene
from xpotato_sim.plugins.robots.catalog import ROBOT_CATALOG
from xpotato_sim.runtime.experiment.contracts import PluginSelection

SCHEMA = "workbench-condition/v1"
MAX_BYTES = 60000


def preset_condition(name, limits=None):
    """登録presetを完全展開し、local pathやportを輸出しない。"""
    if name not in list_launch_profiles():
        raise ValueError("登録preset IDが必要です")
    p = resolve_trial_profile(load_launch_profile(name))
    raw = json.loads(p.document_json)
    mapping = json.loads(p.effective_parameters_json)
    raw["mapping"]["parameters"] = mapping
    return {"schema_version": SCHEMA, "preset_id": name,
        "model_configuration_sha256": p.model_registration().configuration_sha256,
        "configuration": {k: raw[k] for k in ("robot", "model", "input", "mapping", "coordination", "execution")},
        "environment": raw.get("environment"), "task": raw.get("task"), "evaluation": None,
        "limits": asdict(limits or TrialLimits(p.steps, wall_s=360))}


def _leaves(value, path=()):
    if type(value) is dict:
        for k, v in value.items():
            yield from _leaves(v, path + (k,))
    elif type(value) is list:
        for i, v in enumerate(value):
            yield from _leaves(v, path + (i,))
    else:
        yield path, value


def descriptors(document):
    """型付きbackend条件からeditor metadataを生成する。値域は入口検査にも使う。"""
    result = []
    robot = document["configuration"]["robot"]
    models = ROBOT_CATALOG.resolve_registration(PluginSelection(robot["name"], robot["version"])).models
    for path, value in _leaves(document):
        key = str(path[-1])
        parent = str(path[-2]) if len(path) > 1 else ""
        numeric = type(value) in (float, int)
        editable = numeric and key != "version" and parent != "definition" and "version" not in path
        if "coordination" in path or "contact" in path or "keyboard_config" in path:
            editable = False
        choices = None
        if key == "motion_type":
            choices = ["fixed", "dynamic"] if document["configuration"]["execution"].get("dynamics") else ["fixed"]
            editable = True
        if path == ("configuration", "model", "name"):
            choices = [m.identity.name for m in models]
            editable = True
        if key == "integrator":
            choices = ["implicitfast", "Euler"]
            editable = True
        if key == "output_side":
            choices = ["left", "right"]
            editable = True
        if parent == "signs" and editable:
            choices = [-1, 1]
        unit = ""
        label = parent if key.isdigit() else key
        for suffix, u in (("_rad_s", "rad/s"), ("_m_s", "m/s"), ("_m_s2", "m/s²"), ("_kg", "kg"), ("_m", "m"), ("_s", "s"), ("_rad", "rad")):
            if label.endswith(suffix):
                unit = u
                break
        if label == "orientation_wxyz":
            unit = "単位quaternion (w,x,y,z)"
        if label in {"sliding_friction", "gamepad_deadzone", "neutral_threshold", "rgba", "signs"}:
            unit = "無次元"
        minimum, maximum, exclusive = None, None, False
        if editable and numeric:
            if label in {"half_extents_m", "mass_kg", "dt_s", "physics_dt_s", "tolerance", "max_joint_speed_rad_s", "max_tracking_error_rad", "duration_s", "input_wait_s", "wall_s", "prepare_s"}:
                minimum, exclusive = 0, True
            if label in {"gamepad_speed_m_s", "gamepad_deadzone", "gamepad_max_delta_m", "sliding_friction", "torsional_friction_m", "rolling_friction_m", "interval_s", "grace_period_s"}:
                minimum = 0
            if label == "rgba":
                minimum, maximum = 0, 1
            if label == "neutral_threshold":
                minimum, maximum = 0, .1
            if label in {"axes", "axis_indices", "trigger_button", "sign_button", "mode_button", "friction"}:
                minimum = 0
            if key in {"steps", "max_ticks", "iterations"}:
                minimum, maximum = 1, 1000 if key == "iterations" else 2**31-1
        integer = key in {"steps", "max_ticks", "iterations", "trigger_button", "sign_button", "mode_button"} or parent in {"axes", "axis_indices", "signs"}
        result.append({"path": list(path), "type": "integer" if numeric and integer else "number" if numeric else "enum" if choices else "string",
            "unit": unit, "minimum": minimum, "maximum": maximum, "exclusive_minimum": exclusive,
            "choices": choices, "available": editable,
            **({"model_bindings": {m.identity.name: ({"left": m.endpoint_ids[0]} if len(m.endpoint_ids)==1 else dict(zip(("left", "right"), m.endpoint_ids))) for m in models}} if path == ("configuration", "model", "name") else {}),
            **({"model_configuration_digests": {m.identity.name: m.configuration_sha256 for m in models}} if path == ("configuration", "model", "name") else {}),
            "reason": None if editable else ("接触数値条件はpreset固定（初期貫通判定の安全閾値を含む）" if "contact" in path else
                "keyboard parameterはnamed-model/Gamepad経路では使用しません" if "keyboard_config" in path else
                "Inputはnamed-model試行でviewer/gamepad/v1だけに対応します" if "input" in path else
                "正式Evaluation/metricsは未対応です" if path[0] == "evaluation" else
                "登録identity/bindingはpresetの契約に固定されています。対応presetで選択してください")})
    return result


def resolve_condition(document):
    """未知field、コード/外部参照、path、非有限値を拒否して共通resolverへ渡す。"""
    if type(document) is bytes:
        if len(document) > MAX_BYTES:
            raise ValueError("条件は60,000 bytes以内です")
        document = strict_json(document)
    else:
        encoded = canonical(document)
        if len(encoded) > MAX_BYTES:
            raise ValueError("条件は60,000 bytes以内です")
        document = strict_json(encoded)
    fields(document, {"schema_version", "preset_id", "model_configuration_sha256", "configuration", "environment", "task", "evaluation", "limits"}, "condition")
    if document["schema_version"] != SCHEMA or document["evaluation"] is not None:
        raise ValueError("診断条件/v1のみ対応。正式Evaluation/metricsは未対応です")
    baseline = preset_condition(document["preset_id"])
    robot = baseline["configuration"]["robot"]
    model = document["configuration"].get("model") if type(document["configuration"]) is dict else None
    if type(model) is not dict:
        raise ValueError("explicit model selection required")
    registered = ROBOT_CATALOG.resolve_model(PluginSelection(robot["name"], robot["version"]), PluginSelection(model.get("name"), model.get("version")))
    baseline["model_configuration_sha256"] = registered.configuration_sha256
    if model != baseline["configuration"]["model"]:
        bindings = descriptors(baseline)
        binding = next(d["model_bindings"][registered.identity.name] for d in bindings if "model_bindings" in d)
        baseline["configuration"]["coordination"]["side_to_endpoint"] = binding
    if baseline["environment"] is not None and type(document["environment"]) is dict:
        # motion変更だけに必要なvelocity構造を正規化し、scene decoderで再検査する。
        env = fields(document["environment"], set(baseline["environment"]), "environment")
        params = fields(env["parameters"], {"scene"}, "environment parameters")
        scene = fields(params["scene"], set(baseline["environment"]["parameters"]["scene"]), "scene")
        decode_object_scene(canonical(scene))
        objects = scene["objects"]
        if type(objects) is not list:
            raise ValueError("bounded object array required")
        for actual, expected in zip(objects, baseline["environment"]["parameters"]["scene"]["objects"]):
            if type(actual) is dict and actual.get("motion_type") == "fixed":
                expected.pop("initial_velocity", None)
            elif type(actual) is dict and actual.get("motion_type") == "dynamic" and "initial_velocity" not in expected:
                expected["initial_velocity"] = {"frame": "mujoco_world", "linear_m_s": [0,0,0], "angular_rad_s": [0,0,0]}
    # 任意plugin/参照やnested unknown fieldを入口で拒否する。形状と配列集合はpreset契約に束縛。
    def shape(actual, expected, path=()):
        if type(expected) is dict:
            fields(actual, set(expected), str(path))
            for k in expected:
                shape(actual[k], expected[k], path+(k,))
        elif type(expected) is list:
            if type(actual) is not list or len(actual) != len(expected):
                raise ValueError(f"{path}: presetの有界配列構造が必要です")
            for i, v in enumerate(expected):
                shape(actual[i], v, path+(i,))
    shape(document, baseline)
    expected_leaves = dict(_leaves(baseline))
    metadata = {tuple(d["path"]): d for d in descriptors(baseline)}
    for path, value in _leaves(document):
        d = metadata[path]
        if path == ("preset_id",):
            continue
        expected_value = expected_leaves[path]
        same_type = type(value) is type(expected_value) or (type(value) in (int, float) and type(expected_value) in (int, float))
        if not d["available"] and (not same_type or value != expected_value):
            raise ValueError(f"{path}: {d['reason']}")
        if d["available"]:
            if d["choices"] is not None:
                if value not in d["choices"]:
                    raise ValueError(f"{path}: unsupported choice")
            if d["type"] in {"integer", "number"}:
                n = number(value)
                if d["type"] == "integer" and type(value) is not int:
                    raise ValueError(f"{path}: integer required")
                if d["minimum"] is not None and (n < d["minimum"] or d["exclusive_minimum"] and n == d["minimum"]):
                    raise ValueError(f"{path}: minimum {d['minimum']}")
                if d["maximum"] is not None and n > d["maximum"]:
                    raise ValueError(f"{path}: maximum {d['maximum']}")
    for descriptor in metadata.values():
        if descriptor["available"] and descriptor["type"] == "number" and descriptor["path"][0] == "task":
            target = document
            for key in descriptor["path"][:-1]:
                target = target[key]
            target[descriptor["path"][-1]] = float(target[descriptor["path"][-1]])
    p = load_launch_profile(document["preset_id"])
    raw = json.loads(p.document_json)
    raw.update(deepcopy(document["configuration"]))
    for key in ("environment", "task"):
        if document[key] is not None:
            raw[key] = deepcopy(document[key])
    profile = resolve_trial_profile(decode_launch_profile(canonical(raw), source_path=p.source_path))
    limits = TrialLimits(**fields(document["limits"], set(asdict(TrialLimits(1))), "limits"))
    # normalize済み値を保存し、同じ展開済み条件をCLI/GUIへ返す。
    normalized = deepcopy(document)
    normalized["configuration"]["mapping"]["parameters"] = json.loads(profile.effective_parameters_json)
    normalized["limits"] = asdict(limits)
    normalized["configuration"]["execution"] = {"steps": profile.steps, "dt_s": profile.dt_s,
        "interval_s": profile.interval_s, "grace_period_s": profile.grace_period_s,
        **({"dynamics": profile.scene_plan.dynamics.to_document()} if profile.scene_plan and profile.scene_plan.dynamics else {})}
    if profile.scene_plan:
        normalized["environment"]["parameters"] = {"scene": profile.scene_plan.manifest.to_document()}
    return profile, limits, strict_json(canonical(normalized))


def condition_diff(before, after):
    """両条件を再検証し、pathごとの値差だけを返す。copyは共有参照を持たない。"""
    a = dict(_leaves(resolve_condition(before)[2]))
    b = dict(_leaves(resolve_condition(after)[2]))
    return [{"path": list(p), "before": a.get(p), "after": b.get(p)} for p in sorted(a.keys() | b.keys(), key=str) if a.get(p) != b.get(p)]


def condition_digest(document):
    return sha256(canonical(resolve_condition(document)[2])).hexdigest()
