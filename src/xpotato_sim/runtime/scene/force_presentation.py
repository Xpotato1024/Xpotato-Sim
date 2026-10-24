"""追加接触力cueの固定条件。表示条件はphysics・Mappingへ渡さない。"""
from hashlib import sha256
import json
from math import isfinite
import re

from xpotato_sim.runtime.scene.objects import canonical


def default_presentation():
    """通常映像を保持する対照条件。全cue面で同じ固定スケールを使用する。"""
    return {"schema_version": "force-presentation/v1", "mode": "no_force_cue",
        "scale_m_per_n": .002, "max_arrow_m": .12, "normal_load_full_scale_n": 30.,
        "max_sample_age_s": .2}


def validate_presentation(value):
    """未知conditionと非有限・非正scaleを拒否し、不変bytesへ投影できる値を返す。"""
    if value is None:
        return None
    if type(value) is not dict or set(value) != set(default_presentation()):
        raise ValueError("explicit force-presentation/v1 fields required")
    if value["schema_version"] != "force-presentation/v1" or value["mode"] not in ("no_force_cue", "force_cue"):
        raise ValueError("unknown force presentation condition")
    result = dict(value)
    for key in ("scale_m_per_n", "max_arrow_m", "normal_load_full_scale_n", "max_sample_age_s"):
        v = result[key]
        if type(v) not in (int, float) or not isfinite(v) or v <= 0:
            raise ValueError("positive finite force presentation scale required")
        result[key] = float(v)
    if result["max_sample_age_s"] > .2 or result["max_arrow_m"] > 1.:
        raise ValueError("force presentation age/geometry budget exceeded")
    return result


def profile_presentation(profile):
    """元launch documentと独立に、展開済み条件で明示された表示fieldを検証する。"""
    raw = profile.display_comparison_json
    value = None if raw is None else validate_presentation(json.loads(raw))
    if value is not None and (profile.scene_plan is None or profile.scene_plan.dynamics is None):
        raise ValueError("force presentation requires dynamic scene")
    return value


def viewer_identity(value=None):
    """固定build、明示dev、headlessを区別し、不明なbuildを検証済みにしない。"""
    if value is None:
        value = {"mode": "headless", "source_sha256": None, "build_sha256": None}
    if (type(value) is not dict or set(value) != {"mode", "source_sha256", "build_sha256"}
            or value["mode"] not in {"production", "development", "headless"}):
        raise ValueError("explicit viewer identity required")
    for key in ("source_sha256", "build_sha256"):
        v = value[key]
        if value["mode"] == "production":
            if type(v) is not str or not re.fullmatch(r"[0-9a-f]{64}", v):
                raise ValueError("production viewer digest required")
        elif v is not None:
            raise ValueError("unverified viewer mode cannot claim build identity")
    return dict(value)


def presentation_binding(profile, epoch, model_sha256):
    """typed scene identityへ結び、同じ条件を全Viewer面へ公開する。"""
    condition = profile_presentation(profile)
    if condition is None:
        return None
    return {"schema_version": "force-presentation-binding/v1", "condition": condition,
        "condition_sha256": sha256(canonical(condition)).hexdigest(), "epoch": epoch,
        "model_sha256": model_sha256, "scene_digest": profile.scene_plan.manifest.digest,
        "settings_digest": profile.scene_plan.dynamics.digest}
