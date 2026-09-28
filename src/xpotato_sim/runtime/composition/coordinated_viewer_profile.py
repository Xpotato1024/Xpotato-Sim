"""FastArm双腕Viewerを明示的に選ぶstrict application profile。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from ipaddress import ip_address
import json
from math import isfinite
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

from fast_arm_core.assembly import FastArmAssembly, FastArmInstance
from xpotato_sim.plugins.robots.fast_arm.adapter.assembly_viewer import (
    FastArmAssemblyViewerBundle, build_fast_arm_assembly_viewer_bundle,
)
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping.implementation import (
    normalize_viewer_control_mapping_parameters,
)

COORDINATED_VIEWER_PROFILE_SCHEMA = "fast-arm-coordinated-viewer-profile/v1"
MAX_PROFILE_BYTES = 262144
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


def _object(value: object, expected: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{label}: missing or unknown fields; expected {sorted(expected)}")
    return value


def _string(value: object, label: str) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        raise ValueError(f"{label}: canonical non-empty string required")
    return value


def _positive_number(value: object, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label}: positive finite number required")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label}: positive finite number required") from exc
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{label}: positive finite number required")
    return result


def _positive_int(value: object, label: str, maximum: int = 2**31 - 1) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{label}: positive integer required")
    return value


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


@dataclass(frozen=True, slots=True)
class CoordinatedViewerProfile:
    source_path: Path
    workspace_path: Path
    name: str
    assembly: FastArmAssembly
    side_to_arm: Mapping[str, str]
    mapping_parameters_json: str
    epoch: str
    steps: int
    dt_s: float
    interval_s: float
    grace_period_s: float
    max_input_age_s: float
    host: str
    web_port: int
    backend_port: int
    open_browser: bool
    document_json: str

    def build_viewer_bundle(self) -> FastArmAssemblyViewerBundle:
        """明示assemblyをRobot所有の描画資源へ接続する。設定decodeでは実行しない。"""
        return build_fast_arm_assembly_viewer_bundle(self.assembly)

    @property
    def mapping_parameters(self) -> dict[str, object]:
        return json.loads(self.mapping_parameters_json)

    @property
    def configuration_sha256(self) -> str:
        return sha256(self.document_json.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "configuration": json.loads(self.document_json),
            "configuration_sha256": self.configuration_sha256,
            "profile_path": str(self.source_path),
            "workspace_path": str(self.workspace_path),
            "resolved": {
                "physical_output": "disabled",
                "assembly": self.assembly.to_dict(),
                "side_to_arm": dict(self.side_to_arm),
                "mapping_parameters": json.loads(self.mapping_parameters_json),
            },
        }


def decode_coordinated_viewer_profile(
    document: bytes, *, source_path: Path
) -> CoordinatedViewerProfile:
    if (
        type(document) is not bytes
        or len(document) > MAX_PROFILE_BYTES
        or document.startswith(b"\xef\xbb\xbf")
    ):
        raise ValueError("profile must be bounded UTF-8 JSON without BOM")
    try:
        raw = json.loads(
            document.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=_invalid_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("profile must be valid UTF-8 JSON") from exc
    root = _object(
        raw,
        {
            "schema_version", "name", "workspace", "assembly", "side_to_arm",
            "mapping_parameters", "epoch", "execution", "web",
        },
        "profile",
    )
    if root["schema_version"] != COORDINATED_VIEWER_PROFILE_SCHEMA:
        raise ValueError("unsupported coordinated viewer profile schema")

    source_path = Path(source_path).resolve()
    workspace = (source_path.parent / _string(root["workspace"], "workspace")).resolve()
    if not (workspace / "pyproject.toml").is_file() or not (
        workspace / "apps/mujoco-viewer/package.json"
    ).is_file():
        raise ValueError("workspace must contain the Python project and viewer")

    raw_instances = root["assembly"]
    if type(raw_instances) is not list or len(raw_instances) != 2:
        raise ValueError("coordinated viewer requires exactly two assembly instances")
    instances: list[FastArmInstance] = []
    for index, value in enumerate(raw_instances):
        item = _object(
            value,
            {"arm_id", "mirror_y", "position_m", "quaternion_wxyz"},
            f"assembly[{index}]",
        )
        instances.append(FastArmInstance(**item))
    assembly = FastArmAssembly(tuple(instances))

    side_to_arm_raw = _object(root["side_to_arm"], {"left", "right"}, "side_to_arm")
    side_to_arm = {side: _string(side_to_arm_raw[side], f"side_to_arm.{side}") for side in ("left", "right")}
    if set(side_to_arm.values()) != set(assembly.arm_ids):
        raise ValueError("side_to_arm must cover both assembly arms exactly once")

    mapping_raw = root["mapping_parameters"]
    if type(mapping_raw) is not dict:
        raise ValueError("mapping_parameters must be an object")
    normalized = normalize_viewer_control_mapping_parameters(mapping_raw)
    if "gamepad_plane_control" not in normalized:
        raise ValueError("coordinated viewer requires gamepad_plane_control")

    execution = _object(
        root["execution"],
        {"steps", "dt_s", "interval_s", "grace_period_s", "max_input_age_s"},
        "execution",
    )
    steps = _positive_int(execution["steps"], "execution.steps")
    dt_s = _positive_number(execution["dt_s"], "execution.dt_s")
    interval_s = _positive_number(execution["interval_s"], "execution.interval_s")
    grace_period_s = _positive_number(execution["grace_period_s"], "execution.grace_period_s")
    max_input_age_s = _positive_number(execution["max_input_age_s"], "execution.max_input_age_s")

    web = _object(root["web"], {"host", "port", "websocket_port", "open_browser"}, "web")
    host = _string(web["host"], "web.host")
    if not ip_address(host).is_loopback:
        raise ValueError("coordinated viewer permits only a numeric loopback host")
    web_port = _positive_int(web["port"], "web.port", 65535)
    backend_port = _positive_int(web["websocket_port"], "web.websocket_port", 65535)
    if web_port == backend_port:
        raise ValueError("web and websocket ports must differ")
    if type(web["open_browser"]) is not bool:
        raise ValueError("web.open_browser must be boolean")

    name = _string(root["name"], "name")
    epoch = _string(root["epoch"], "epoch")
    canonical = _canonical_json(root)
    return CoordinatedViewerProfile(
        source_path=source_path,
        workspace_path=workspace,
        name=name,
        assembly=assembly,
        side_to_arm=MappingProxyType(side_to_arm),
        mapping_parameters_json=_canonical_json(mapping_raw),
        epoch=epoch,
        steps=steps,
        dt_s=dt_s,
        interval_s=interval_s,
        grace_period_s=grace_period_s,
        max_input_age_s=max_input_age_s,
        host=host,
        web_port=web_port,
        backend_port=backend_port,
        open_browser=web["open_browser"],
        document_json=canonical,
    )


def repository_workspace() -> Path:
    workspace = Path(__file__).resolve().parents[4]
    if not (workspace / "profiles/coordinated").is_dir():
        raise ValueError("coordinated viewer profiles require a source checkout")
    return workspace


def list_coordinated_viewer_profiles() -> tuple[str, ...]:
    root = repository_workspace() / "profiles/coordinated"
    return tuple(
        sorted(path.stem for path in root.glob("*.json") if _NAME.fullmatch(path.stem))
    )


def load_coordinated_viewer_profile(
    selector: str | Path,
) -> CoordinatedViewerProfile:
    text = str(selector)
    source = (
        repository_workspace() / "profiles/coordinated" / f"{text}.json"
        if _NAME.fullmatch(text)
        else Path(selector)
    )
    with source.open("rb") as stream:
        document = stream.read(MAX_PROFILE_BYTES + 1)
    return decode_coordinated_viewer_profile(document, source_path=source)


def override_coordinated_viewer_profile(
    profile: CoordinatedViewerProfile,
    *,
    web_port: int | None = None,
    backend_port: int | None = None,
    open_browser: bool | None = None,
) -> CoordinatedViewerProfile:
    raw = json.loads(profile.document_json)
    for key, value in (
        ("port", web_port),
        ("websocket_port", backend_port),
        ("open_browser", open_browser),
    ):
        if value is not None:
            raw["web"][key] = value
    return decode_coordinated_viewer_profile(
        _canonical_json(raw).encode("utf-8"),
        source_path=profile.source_path,
    )


__all__ = [
    "COORDINATED_VIEWER_PROFILE_SCHEMA",
    "CoordinatedViewerProfile",
    "decode_coordinated_viewer_profile",
    "list_coordinated_viewer_profiles",
    "load_coordinated_viewer_profile",
    "override_coordinated_viewer_profile",
]
