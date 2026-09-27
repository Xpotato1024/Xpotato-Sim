"""起動設定を検証し、既存のRobot/Input/Mappingの選択へ接続する。

プロセス、通信、Source開始、physics step、実機許可は所有しない。
JSONの相対workspaceはprofileの所在位置を基準にする。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from hashlib import sha256
from ipaddress import ip_address
import json
from math import isfinite
from pathlib import Path
import re

from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.plugins.robots.catalog import ROBOT_CATALOG
from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_COMMAND_V1, ENDPOINT_POSE_V1, QPOS_FEASIBILITY_V1, RESET_INITIAL_STATE_V1,
)
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.runtime.experiment.composition import resolve_command_execution
from xpotato_sim.runtime.experiment.contracts import PluginSelection, VersionedIdentity
from xpotato_sim.runtime.experiment.input_source import InputSourceMode

LAUNCH_PROFILE_SCHEMA = "xpotato-sim-launch-profile/v1"
LEGACY_LAUNCH_PROFILE_SCHEMA = "selfrionette-launch-profile/v1"
MAX_PROFILE_BYTES = 262144
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


def _object(value: object, expected: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{label}: missing or unknown fields; expected {sorted(expected)}")
    return value


def _string(value: object, label: str) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        raise ValueError(f"{label}: non-empty canonical string required")
    return value


def _integer(value: object, label: str, maximum: int = 2**31 - 1) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{label}: integer in 1..{maximum} required")
    return value


def _seconds(value: object, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label}: finite positive number required")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label}: finite positive number required") from exc
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{label}: finite positive number required")
    return result


def _selection(value: object, label: str) -> PluginSelection:
    raw = _object(value, {"name", "version"}, label)
    return PluginSelection(_string(raw["name"], label), _integer(raw["version"], label))


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"duplicate JSON field: {name}")
        result[name] = value
    return result


def _invalid_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def _projection(value: object) -> object:
    """解決済みparameterの閲覧用projection。実行設定として逆変換しない。"""
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _projection(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {key: _projection(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_projection(item) for item in value]
    return value


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def repository_workspace() -> Path:
    """source checkoutから既定profileの所在を決め、cwdを探索しない。"""
    workspace = Path(__file__).resolve().parents[4]
    if not (workspace / "profiles").is_dir() or not (workspace / "apps/mujoco-viewer/package.json").is_file():
        raise ValueError("launch profiles require a source checkout; supply an explicit JSON path")
    return workspace


def list_launch_profiles() -> tuple[str, ...]:
    return tuple(sorted(path.stem for path in (repository_workspace() / "profiles").glob("*.json")
                        if _NAME.fullmatch(path.stem)))


@dataclass(frozen=True, slots=True)
class LaunchProfile:
    """検証済みの起動設定。mutableなJSON objectやpermissionを保持しない。"""
    source_path: Path
    workspace_path: Path
    name: str
    mode: str
    robot: PluginSelection
    input_source: PluginSelection
    mapping: PluginSelection
    mapping_parameters_json: str
    route: VersionedIdentity
    provider_id: str | None
    preset: str | None
    steps: int
    dt_s: float
    interval_s: float
    grace_period_s: float
    host: str
    web_port: int
    backend_port: int
    open_browser: bool
    document_json: str
    effective_parameters_json: str

    @property
    def mapping_parameters(self) -> dict:
        return json.loads(self.mapping_parameters_json)

    @property
    def configuration_sha256(self) -> str:
        # local absolute pathを含めず、同じ設定を別checkoutでも比較可能にする。
        return sha256(self.document_json.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {
            "configuration": json.loads(self.document_json),
            "configuration_sha256": self.configuration_sha256,
            "profile_path": str(self.source_path),
            "workspace_path": str(self.workspace_path),
            "resolved": {
                "command_route": self.route.canonical_id,
                "mapping_parameters": json.loads(self.effective_parameters_json),
                "simulation_duration_s": self.steps * self.dt_s,
                "scheduled_duration_s": self.steps * self.interval_s,
                "physical_output": "disabled",
            },
        }


def decode_launch_profile(document: bytes, *, source_path: Path) -> LaunchProfile:
    """strict JSONをpure readinessへ渡す。profileは実験/実機acceptanceではない。"""
    if type(document) is not bytes or len(document) > MAX_PROFILE_BYTES or document.startswith(b"\xef\xbb\xbf"):
        raise ValueError("profile must be bounded UTF-8 JSON without BOM")
    try:
        raw = json.loads(document.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("profile must be valid UTF-8 JSON") from exc
    raw = _object(raw, {"schema_version", "name", "workspace", "mode", "robot", "input", "mapping", "execution", "web"}, "profile")
    if raw["schema_version"] not in (LAUNCH_PROFILE_SCHEMA, LEGACY_LAUNCH_PROFILE_SCHEMA):
        raise ValueError("unsupported launch profile schema_version")
    name = _string(raw["name"], "name")
    if not _NAME.fullmatch(name):
        raise ValueError("invalid launch profile name")
    mode = raw["mode"]
    if mode not in ("simulation", "replay"):
        raise ValueError("only simulation/replay launch modes are permitted")
    source_path = Path(source_path).resolve()
    workspace = (source_path.parent / _string(raw["workspace"], "workspace")).resolve()
    if not (workspace / "pyproject.toml").is_file() or not (workspace / "apps/mujoco-viewer/package.json").is_file():
        raise ValueError("workspace must contain the Python project and mujoco-viewer")
    robot = _selection(raw["robot"], "robot")
    source = _object(raw["input"], {"plugin", "provider", "preset"}, "input")
    source_selection = _selection(source["plugin"], "input.plugin")
    provider, preset = source["provider"], source["preset"]
    if provider not in (None, "keyboard/v1", "gamepad/v1") or preset not in (None, "sweep_x"):
        raise ValueError("unsupported input provider or preset")
    mapping = _object(raw["mapping"], {"plugin", "parameters"}, "mapping")
    mapping_selection = _selection(mapping["plugin"], "mapping.plugin")
    if type(mapping["parameters"]) is not dict:
        raise ValueError("mapping.parameters must be a JSON object")
    execution = _object(raw["execution"], {"steps", "dt_s", "interval_s", "grace_period_s"}, "execution")
    steps = _integer(execution["steps"], "execution.steps")
    dt, interval, grace = (_seconds(execution[key], f"execution.{key}") for key in ("dt_s", "interval_s", "grace_period_s"))
    if not isfinite(steps * dt) or not isfinite(steps * interval):
        raise ValueError("execution duration must be finite")
    web = _object(raw["web"], {"host", "port", "websocket_port", "open_browser"}, "web")
    host = _string(web["host"], "web.host")
    if not ip_address(host).is_loopback:
        raise ValueError("launch profile v1 only permits a numeric loopback host")
    web_port = _integer(web["port"], "web.port", 65535)
    backend_port = _integer(web["websocket_port"], "web.websocket_port", 65535)
    if web_port == backend_port:
        raise ValueError("web and WebSocket ports must differ")
    if type(web["open_browser"]) is not bool:
        raise ValueError("web.open_browser must be boolean")
    # 1e999もrejectし、plugin固有のvalidationへ非有限値を渡さない。
    canonical = _json(raw)
    plugin = INPUT_SOURCE_CATALOG.resolve_plugin(source_selection)
    if mode == "simulation":
        if plugin.mode is not InputSourceMode.VIEWER_BRIDGE or provider is None or preset is not None:
            raise ValueError("simulation requires a viewer-bridge input, one provider, and no preset")
    elif plugin.mode not in (InputSourceMode.OFFLINE, InputSourceMode.REPLAY) or provider is not None:
        raise ValueError("replay requires an offline source without browser input")
    # 有限readiness sample数でschema/parameter/routeを検査し、全trialを先行生成しない。
    selected = select_runtime_input_source(source_selection.plugin_id, steps=1, preset=preset,
        control_mapping_selection=mapping_selection, control_mapping_parameters=mapping["parameters"])
    if selected.plugin_selection != source_selection or selected.control_mapping is None:
        raise ValueError("resolved input/mapping identity differs from profile")
    bundle = ROBOT_CATALOG.resolve_bundle(robot)
    command = resolve_command_execution(selected.control_mapping, bundle, selected.command_semantics_route_selection)
    for capability in (RESET_INITIAL_STATE_V1, ENDPOINT_POSE_V1, QPOS_FEASIBILITY_V1):
        bundle.provider(capability)
    if command.binding.requires_motion_generator:
        bundle.provider(ENDPOINT_COMMAND_V1)
    return LaunchProfile(source_path, workspace, name, mode, robot, source_selection, mapping_selection,
        _json(mapping["parameters"]), command.route.identity, provider, preset, steps, dt, interval, grace,
        host, web_port, backend_port, web["open_browser"], canonical, _json(_projection(selected.control_mapping_parameters)))


def load_launch_profile(selector: str | Path) -> LaunchProfile:
    """単純名はcheckout内profiles、それ以外は指定JSON。環境変数/継承fallbackなし。"""
    text = str(selector)
    path = repository_workspace() / "profiles" / f"{text}.json" if _NAME.fullmatch(text) else Path(selector)
    with path.open("rb") as stream:
        document = stream.read(MAX_PROFILE_BYTES + 1)
    return decode_launch_profile(document, source_path=path)


def override_launch_profile(profile: LaunchProfile, *, web_port: int | None = None,
                            backend_port: int | None = None, open_browser: bool | None = None) -> LaunchProfile:
    """明示CLI override > profile。意味条件は上書きせず、全設定を再検証する。"""
    raw = json.loads(profile.document_json)
    for key, value in (("port", web_port), ("websocket_port", backend_port), ("open_browser", open_browser)):
        if value is not None:
            raw["web"][key] = value
    return decode_launch_profile(_json(raw).encode("utf-8"), source_path=profile.source_path)
