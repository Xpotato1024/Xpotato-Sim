"""Canonical viewer_keyboard_gamepad_mapping/v1 implementation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from hashlib import sha256
import json
from types import MappingProxyType

from xpotato_sim.plugins.mappings._continuous_endpoint_velocity import (
    build_continuous_endpoint_velocity_intent,
)
from .gamepad_axes import GamepadAxisMap, coerce_gamepad_axis_map, apply_gamepad_axis_map
from .gamepad_triggers import TriggerControlConfig, TriggerControlSession, SIDES, coerce_trigger_control

from xpotato_sim.plugins.mappings._command_routes import (
    local_endpoint_velocity_command_route,
)
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping.keyboard import (
    KeyboardBinding,
    KeyboardInputConfig,
    build_default_keyboard_input_config,
    build_keyboard_continuous_velocity_intent,
)
from xpotato_sim.runtime.experiment.contracts import (
    ControlMappingPlugin,
    LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
    ParameterContract,
    ParameterField,
    VersionedIdentity,
)
from xpotato_sim.schemas import InputIntent, RawInputFrame, coerce_viewer_control_message
from xpotato_sim.schemas.coordinated import CoordinatedInput, EndpointVelocity
from xpotato_sim.schemas.viewer_input import (
    VIEWER_CONTROL_SAMPLE_SCHEMA,
    ViewerCanonicalInputSample,
)

VIEWER_CONTROL_MAPPING_IDENTITY = VersionedIdentity("viewer_keyboard_gamepad_mapping", 1)
VIEWER_MAPPING_SEMANTICS_IDENTITY = VersionedIdentity("viewer_keyboard_gamepad_semantics", 1)
VIEWER_CONTROL_SAMPLE_IDENTITY = VersionedIdentity("viewer_control_sample", 1)
_DEFAULT_GAMEPAD_SPEED_M_S = 0.1
_DEFAULT_GAMEPAD_DEADZONE = 0.1
_DEFAULT_GAMEPAD_MAX_DELTA_M = 0.03
_LEGACY_FRONTEND_GAMEPAD_DEADZONE = 0.1


def _as_json_wire_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _as_json_wire_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_as_json_wire_value(item) for item in value]
    return value


def _coerce_frame_sample(frame: RawInputFrame) -> ViewerCanonicalInputSample:
    sample = frame.metadata.get("viewer_input_sample")
    if not isinstance(sample, Mapping):
        raise ValueError("viewer mapping requires canonical viewer_input_sample metadata")
    if sample.get("schema") != VIEWER_CONTROL_SAMPLE_SCHEMA:
        raise ValueError("viewer mapping received an incompatible canonical sample schema")

    source_kind = sample.get("source_kind")
    provider_id = sample.get("provider_id")
    provider_schema = sample.get("provider_schema")
    if not isinstance(source_kind, str):
        raise ValueError("viewer mapping canonical sample source_kind is required")
    if not isinstance(provider_id, str) or not isinstance(provider_schema, str):
        raise ValueError("viewer mapping canonical sample provider identity is required")
    if source_kind == "keyboard":
        keyboard_payload = sample.get("keyboard")
        if not isinstance(keyboard_payload, Mapping):
            raise ValueError("viewer keyboard sample payload is required")
        message_payload = {
            "type": "viewer_control_message",
            "timestamp_s": sample.get("timestamp_s"),
            "source_kind": "keyboard",
            "provider_id": provider_id,
            "provider_schema": provider_schema,
            "keyboard": _as_json_wire_value(keyboard_payload),
        }
        if sample.get("sequence") is not None:
            message_payload["sequence"] = sample.get("sequence")
        message = coerce_viewer_control_message(message_payload)
        return ViewerCanonicalInputSample(
            provider_id=provider_id,  # type: ignore[arg-type]
            provider_schema=provider_schema,  # type: ignore[arg-type]
            source_kind="keyboard",
            timestamp_s=float(sample["timestamp_s"]),
            sequence=sample.get("sequence"),
            requested_control_frame=str(sample.get("requested_control_frame", "world")),
            keyboard=message.keyboard,
            source_active=bool(sample.get("source_active", False)),
            zero_state=bool(sample.get("zero_state", True)),
            stale_reason=sample.get("stale_reason"),
            diagnostics=sample.get("diagnostics", {}),  # type: ignore[arg-type]
        )

    if source_kind != "gamepad":
        raise ValueError("viewer mapping received an unknown sample source kind")
    gamepad_payload = sample.get("gamepad")
    if not isinstance(gamepad_payload, Mapping):
        raise ValueError("viewer gamepad sample payload is required")

    message_payload = {
        "type": "viewer_control_message",
        "timestamp_s": sample.get("timestamp_s"),
        "source_kind": "gamepad",
        "provider_id": provider_id,
        "provider_schema": provider_schema,
        "gamepad": _as_json_wire_value(gamepad_payload),
    }
    if sample.get("sequence") is not None:
        message_payload["sequence"] = sample.get("sequence")
    message = coerce_viewer_control_message(message_payload)
    return ViewerCanonicalInputSample(
        provider_id=provider_id,  # type: ignore[arg-type]
        provider_schema=provider_schema,  # type: ignore[arg-type]
        source_kind="gamepad",
        timestamp_s=float(sample["timestamp_s"]),
        sequence=sample.get("sequence"),
        requested_control_frame=str(sample.get("requested_control_frame", "world")),
        gamepad=message.gamepad,
        source_active=bool(sample.get("source_active", False)),
        zero_state=bool(sample.get("zero_state", True)),
        stale_reason=sample.get("stale_reason"),
        diagnostics=sample.get("diagnostics", {}),  # type: ignore[arg-type]
    )


def _normalize_control_frame(value: object) -> str:
    if not isinstance(value, str):
        return "world"
    value = value.strip().lower()
    return value if value in {"world", "tool"} else "world"


def _coerce_axis_vector3(axes: Sequence[float]) -> tuple[float, float, float]:
    values = tuple(float(axis) for axis in axes)
    if not all(isfinite(axis) for axis in values):
        raise ValueError("gamepad axes must be finite")
    return (
        values[0] if len(values) > 0 else 0.0,
        values[1] if len(values) > 1 else 0.0,
        values[2] if len(values) > 2 else 0.0,
    )


def _normalize_gamepad_axis_for_legacy_frontend(value: float) -> float:
    """Reproduce gamepad/v1's fixed frontend compatibility projection."""

    clamped = max(-1.0, min(1.0, value))
    magnitude = abs(clamped)
    if magnitude <= _LEGACY_FRONTEND_GAMEPAD_DEADZONE:
        return 0.0
    scaled = (magnitude - _LEGACY_FRONTEND_GAMEPAD_DEADZONE) / max(
        1.0 - _LEGACY_FRONTEND_GAMEPAD_DEADZONE, 1e-12
    )
    return (1.0 if clamped > 0.0 else -1.0) * max(0.0, min(1.0, scaled))


@dataclass(frozen=True, slots=True)
class ViewerControlMappingParameters:
    keyboard_config: KeyboardInputConfig
    gamepad_speed_m_s: float = _DEFAULT_GAMEPAD_SPEED_M_S
    gamepad_deadzone: float = _DEFAULT_GAMEPAD_DEADZONE
    gamepad_max_delta_m: float = _DEFAULT_GAMEPAD_MAX_DELTA_M
    gamepad_axis_map: GamepadAxisMap | None = None
    gamepad_trigger_control: TriggerControlConfig | None = None

    def __post_init__(self) -> None:
        if self.gamepad_trigger_control is not None:
            if not isinstance(self.gamepad_trigger_control, TriggerControlConfig):
                raise TypeError("gamepad_trigger_control must be validated")
            if self.gamepad_axis_map is not None:
                raise ValueError("static axis map and trigger control are mutually exclusive")
        if self.gamepad_axis_map is not None and not isinstance(self.gamepad_axis_map, GamepadAxisMap):
            raise TypeError("gamepad_axis_map must be a validated GamepadAxisMap")
        if not isinstance(self.keyboard_config, KeyboardInputConfig):
            raise ValueError("keyboard_config must be a KeyboardInputConfig")
        for name, value in (
            ("gamepad_speed_m_s", self.gamepad_speed_m_s),
            ("gamepad_deadzone", self.gamepad_deadzone),
            ("gamepad_max_delta_m", self.gamepad_max_delta_m),
        ):
            if not isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")


def build_viewer_control_mapping_parameters(
    parameters: Mapping[str, object] | None = None,
) -> ViewerControlMappingParameters:
    values = {} if parameters is None else dict(parameters)
    if "gamepad_plane_control" in values:
        raise ValueError("gamepad_plane_control is retired; select trigger control explicitly")
    allowed = {
        "keyboard_config",
        "gamepad_speed_m_s",
        "gamepad_deadzone",
        "gamepad_max_delta_m",
        "gamepad_axis_map",
        "gamepad_trigger_control",
    }
    unknown = tuple(sorted(set(values) - allowed))
    if unknown:
        raise ValueError(f"unknown viewer mapping parameters: {unknown}")
    keyboard_config = values.get("keyboard_config")
    if keyboard_config is None:
        keyboard_config = build_default_keyboard_input_config()
    elif isinstance(keyboard_config, Mapping):
        bindings_payload = keyboard_config.get("bindings")
        if not isinstance(bindings_payload, Mapping):
            raise ValueError("keyboard_config.bindings must be a mapping")
        bindings: dict[str, KeyboardBinding] = {}
        for key, binding in bindings_payload.items():
            if not isinstance(binding, Mapping):
                raise ValueError("keyboard_config bindings must use mapping values")
            if "axis" not in binding or "direction" not in binding:
                raise ValueError(f"binding {key!r} must include axis and direction")
            if type(binding["direction"]) is not int or binding["direction"] not in {-1, 1}:
                raise ValueError(f"invalid direction for binding {key!r}")
            if not isinstance(binding["axis"], str):
                raise ValueError(f"invalid axis for binding {key!r}")
            direction = binding["direction"]
            bindings[str(key)] = KeyboardBinding(
                axis=binding["axis"],
                direction=direction,
            )
        speed_m_s = keyboard_config.get("speed_m_s")
        deadzone = keyboard_config.get("deadzone")
        max_delta_m = keyboard_config.get("max_delta_m")
        if speed_m_s is None or deadzone is None or max_delta_m is None:
            raise ValueError("keyboard_config requires speed_m_s, deadzone, and max_delta_m")
        keyboard_config = KeyboardInputConfig(
            bindings=bindings,
            speed_m_s=float(speed_m_s),
            deadzone=float(deadzone),
            max_delta_m=float(max_delta_m),
        )
    return ViewerControlMappingParameters(
        keyboard_config=keyboard_config,
        gamepad_speed_m_s=float(values.get("gamepad_speed_m_s", _DEFAULT_GAMEPAD_SPEED_M_S)),
        gamepad_deadzone=float(values.get("gamepad_deadzone", _DEFAULT_GAMEPAD_DEADZONE)),
        gamepad_max_delta_m=float(values.get("gamepad_max_delta_m", _DEFAULT_GAMEPAD_MAX_DELTA_M)),
        gamepad_axis_map=(coerce_gamepad_axis_map(values["gamepad_axis_map"]) if "gamepad_axis_map" in values else None),
        gamepad_trigger_control=(coerce_trigger_control(values["gamepad_trigger_control"]) if "gamepad_trigger_control" in values else None),
    )


def normalize_viewer_control_mapping_parameters(
    parameters: Mapping[str, object],
) -> Mapping[str, object]:
    """Validate and freeze viewer mapping parameters before source execution."""

    normalized = build_viewer_control_mapping_parameters(parameters)
    keyboard_config = KeyboardInputConfig(
        bindings=MappingProxyType(
            dict(sorted(normalized.keyboard_config.bindings.items()))
        ),
        speed_m_s=normalized.keyboard_config.speed_m_s,
        deadzone=normalized.keyboard_config.deadzone,
        max_delta_m=normalized.keyboard_config.max_delta_m,
    )
    result = {
            "keyboard_config": keyboard_config,
            "gamepad_speed_m_s": normalized.gamepad_speed_m_s,
            "gamepad_deadzone": normalized.gamepad_deadzone,
            "gamepad_max_delta_m": normalized.gamepad_max_delta_m,
        }
    if normalized.gamepad_axis_map is not None:
        result["gamepad_axis_map"] = MappingProxyType({
            "axis_indices": normalized.gamepad_axis_map.axis_indices,
            "axis_signs": normalized.gamepad_axis_map.axis_signs,
        })
    if normalized.gamepad_trigger_control is not None:
        result["gamepad_trigger_control"] = normalized.gamepad_trigger_control.to_mapping()
    return MappingProxyType(result)


class ViewerKeyboardGamepadMappingStrategy:
    mapping_semantics_identity = VIEWER_MAPPING_SEMANTICS_IDENTITY

    def __init__(self, *, session: bool = False) -> None:
        self._trigger_session = TriggerControlSession() if session else None
        self._latest_trigger_presentation: dict[str, object] | None = None


    @property
    def latest_trigger_presentation(self) -> dict[str, object] | None:
        if self._latest_trigger_presentation is None:
            return None
        return json.loads(json.dumps(self._latest_trigger_presentation, allow_nan=False))

    def map_input(self, input_intent: object, parameters: Mapping[str, object]) -> InputIntent:
        if not isinstance(input_intent, RawInputFrame):
            raise TypeError("viewer mapping accepts a canonical RawInputFrame sample")
        mapping_parameters = build_viewer_control_mapping_parameters(parameters)
        sample_payload = input_intent.metadata.get("viewer_input_sample")
        if mapping_parameters.gamepad_trigger_control is not None:
            sample = _coerce_frame_sample(input_intent) if isinstance(sample_payload, Mapping) else None
            return self._map_triggers(input_intent, sample, mapping_parameters)
        if not isinstance(sample_payload, Mapping):
            intent = build_continuous_endpoint_velocity_intent(
                (0.0, 0.0, 0.0),
                source_kind="viewer",
                source_timestamp_s=input_intent.timestamp_s,
                speed_m_s=mapping_parameters.gamepad_speed_m_s,
                deadzone=mapping_parameters.gamepad_deadzone,
                max_delta_m=mapping_parameters.gamepad_max_delta_m,
                control_frame="world",
                source_active=False,
                stale_reason=input_intent.metadata.get("stale_reason"),
            )
            metadata = dict(input_intent.metadata)
            metadata.pop("gamepad_trigger_control_v1", None)
            metadata.update(intent.to_metadata())
            metadata.update(
                {
                    "endpoint_velocity_m_s": intent.local_endpoint_velocity_m_s,
                    "resolved_world_endpoint_velocity_m_s": intent.local_endpoint_velocity_m_s,
                    "endpoint_velocity_frame": "mujoco_world",
                }
            )
            return InputIntent(
                source="viewer",
                timestamp_s=input_intent.timestamp_s,
                values=intent.axis_values,
                buttons=(),
                metadata=metadata,
            )
        sample = _coerce_frame_sample(input_intent)
        control_frame = _normalize_control_frame(sample.requested_control_frame)

        if sample.source_kind == "keyboard":
            assert sample.keyboard is not None
            intent = build_keyboard_continuous_velocity_intent(
                sample.keyboard.active_key_codes if sample.source_active else (),
                timestamp_s=sample.timestamp_s,
                config=mapping_parameters.keyboard_config,  # type: ignore[arg-type]
                control_frame=control_frame,
                source_active=sample.source_active,
                stale_reason=sample.stale_reason,
                source_kind="viewer_keyboard",
            )
            buttons = tuple(sample.keyboard.key_state.get(code, False) for code in sample.keyboard.active_key_codes)
        else:
            assert sample.gamepad is not None
            supplements = [0.0, 0.0, 0.0]
            for index, button in enumerate(sample.gamepad.buttons):
                if index == 0 and button.pressed:
                    supplements[2] += 1.0
                if index == 1 and button.pressed:
                    supplements[2] -= 1.0
            source_axes = (
                sample.gamepad.raw_axes
                if sample.gamepad.raw_axes is not None
                else sample.gamepad.axes
            )
            mapping_axes = (
                tuple(
                    _normalize_gamepad_axis_for_legacy_frontend(value)
                    for value in source_axes
                )
                if sample.gamepad.raw_axes is not None
                else tuple(source_axes)
            ) if sample.source_active else (0.0, 0.0, 0.0)
            if not sample.source_active:
                supplements = [0.0, 0.0, 0.0]
            axis_map = mapping_parameters.gamepad_axis_map
            if sample.source_active and axis_map is not None:
                mapping_axes = apply_gamepad_axis_map(mapping_axes, axis_map)
            diagnostics = {"raw_axes": tuple(source_axes)}
            if axis_map is not None:
                diagnostics["axis_indices"] = axis_map.axis_indices
                diagnostics["axis_signs"] = axis_map.axis_signs
            intent = build_continuous_endpoint_velocity_intent(
                _coerce_axis_vector3(mapping_axes),
                source_kind="viewer_gamepad",
                source_timestamp_s=sample.timestamp_s,
                speed_m_s=mapping_parameters.gamepad_speed_m_s,
                # Raw gamepad axes received the fixed legacy frontend
                # projection above. The selected gamepad deadzone is the
                # configurable legacy backend threshold and remains the
                # mapping-owned second stage. Legacy axes already carry the
                # frontend projection and therefore skip that first stage.
                deadzone=mapping_parameters.gamepad_deadzone,
                max_delta_m=mapping_parameters.gamepad_max_delta_m,
                control_frame=control_frame,
                source_active=sample.source_active,
                stale_reason=sample.stale_reason,
                supplemental_axis_values=tuple(supplements),
                source_diagnostics=diagnostics,
            )
            buttons = tuple(button.pressed for button in sample.gamepad.buttons)

        metadata = dict(input_intent.metadata)
        metadata.pop("gamepad_trigger_control_v1", None)
        metadata.update(intent.to_metadata())
        metadata.update(
            {
                "endpoint_velocity_m_s": intent.local_endpoint_velocity_m_s,
                "resolved_world_endpoint_velocity_m_s": (
                    intent.local_endpoint_velocity_m_s
                    if control_frame == "world"
                    else None
                ),
                "endpoint_velocity_frame": "mujoco_world",
            }
        )
        metadata.update(
            {
                "viewer_source_kind": sample.source_kind,
                "sequence": sample.sequence,
                "control_frame": control_frame,
                "source_active": sample.source_active,
                "stale_reason": sample.stale_reason,
            }
        )
        return InputIntent(
            source="viewer",
            timestamp_s=sample.timestamp_s,
            values=intent.axis_values,
            buttons=buttons,
            metadata=metadata,
        )


    def _build_trigger_intents(
        self,
        frame: RawInputFrame,
        sample: ViewerCanonicalInputSample | None,
        parameters: ViewerControlMappingParameters,
    ):
        """単一手先と共同実行が共有するstick XY + trigger Z計算。"""
        if self._trigger_session is None:
            raise ValueError("trigger control requires a runtime mapping session")
        config = parameters.gamepad_trigger_control
        assert config is not None
        if sample is not None and sample.source_kind != "gamepad":
            self._trigger_session.reset()
            raise ValueError("trigger control accepts only gamepad samples")
        control_frame = "world" if sample is None else sample.requested_control_frame
        if control_frame not in {"world", "tool"}:
            self._trigger_session.reset()
            raise ValueError("trigger control requires an explicit world/tool frame")
        raw = () if sample is None or sample.gamepad is None or sample.gamepad.raw_axes is None else sample.gamepad.raw_axes
        projected = tuple(_normalize_gamepad_axis_for_legacy_frontend(v) for v in raw)
        outputs, triggers, reason = self._trigger_session.update(
            sample,
            config,
            projected,
            settings_key=(
                parameters.gamepad_speed_m_s,
                parameters.gamepad_deadzone,
                parameters.gamepad_max_delta_m,
                control_frame,
            ),
        )
        intents = {
            side: build_continuous_endpoint_velocity_intent(
                outputs[side],
                source_kind="viewer_gamepad",
                source_timestamp_s=frame.timestamp_s,
                speed_m_s=parameters.gamepad_speed_m_s,
                deadzone=parameters.gamepad_deadzone,
                max_delta_m=parameters.gamepad_max_delta_m,
                control_frame=control_frame,
                source_active=bool(sample is not None and sample.source_active),
                stale_reason=frame.metadata.get("stale_reason") if sample is None else sample.stale_reason,
                source_diagnostics={"raw_axes": tuple(raw), "input_side": side, "trigger_value": triggers[side]},
            )
            for side in SIDES
        }
        return intents, triggers, reason, control_frame, raw

    def _map_triggers(
        self,
        frame: RawInputFrame,
        sample: ViewerCanonicalInputSample | None,
        parameters: ViewerControlMappingParameters,
    ) -> InputIntent:
        self._latest_trigger_presentation = None
        intents, triggers, reason, control_frame, _ = self._build_trigger_intents(frame, sample, parameters)
        config = parameters.gamepad_trigger_control
        assert config is not None and self._trigger_session is not None
        intent = intents[config.output_side]
        presentation = self._trigger_session.presentation(
            config,
            {side: intents[side].local_endpoint_velocity_m_s for side in SIDES},
            triggers,
            reason,
        )
        self._latest_trigger_presentation = presentation
        metadata = dict(frame.metadata)
        metadata.update(intent.to_metadata())
        metadata.update({
            "endpoint_velocity_m_s": intent.local_endpoint_velocity_m_s,
            "resolved_world_endpoint_velocity_m_s": intent.local_endpoint_velocity_m_s if control_frame == "world" else None,
            "endpoint_velocity_frame": "mujoco_world",
            "viewer_source_kind": "gamepad",
            "sequence": None if sample is None else sample.sequence,
            "gamepad_trigger_control_v1": presentation,
        })
        return InputIntent(
            source="viewer",
            timestamp_s=frame.timestamp_s,
            values=intent.axis_values,
            buttons=() if sample is None or sample.gamepad is None else tuple(b.pressed for b in sample.gamepad.buttons),
            metadata=metadata,
        )



    def reset_coordinated_presentation(
        self, parameters: Mapping[str, object], *, reason: str,
    ) -> None:
        """未取得・終了時の表示状態をresetする。観測や運動指令は生成しない。"""
        normalized = build_viewer_control_mapping_parameters(parameters)
        self._latest_trigger_presentation = None
        if normalized.gamepad_trigger_control is not None:
            if self._trigger_session is None:
                raise ValueError("coordinated presentation requires a trigger-control session")
            self._trigger_session.reset()
            self._latest_trigger_presentation = self._trigger_session.presentation(
                normalized.gamepad_trigger_control,
                {side: (0.0, 0.0, 0.0) for side in SIDES},
                {side: 0.0 for side in SIDES},
                reason,
                output_scope="coordinated",
            )
            return
        raise ValueError("coordinated presentation requires explicit gamepad control")

    def map_coordinated_input(self, frame: RawInputFrame, parameters: Mapping[str, object], *,
                              side_to_endpoint: Mapping[str, str], received_at_s: float) -> CoordinatedInput:
        """明示した腕へtyped指令を返す。診断metadataから指令を逆生成しない。"""
        if (not isinstance(frame, RawInputFrame) or not isinstance(side_to_endpoint, Mapping)
                or not 1 <= len(side_to_endpoint) <= 2 or not set(side_to_endpoint) <= set(SIDES)
                or len(set(side_to_endpoint.values())) != len(side_to_endpoint)):
            raise ValueError("explicit distinct side-to-endpoint binding required")
        normalized = build_viewer_control_mapping_parameters(parameters)
        sample = _coerce_frame_sample(frame) if isinstance(frame.metadata.get("viewer_input_sample"), Mapping) else None
        self._latest_trigger_presentation = None

        if normalized.gamepad_trigger_control is not None:
            intents, triggers, reason, control_frame, raw = self._build_trigger_intents(frame, sample, normalized)
            assert self._trigger_session is not None
            self._latest_trigger_presentation = self._trigger_session.presentation(
                normalized.gamepad_trigger_control,
                {side: intents[side].local_endpoint_velocity_m_s for side in SIDES},
                triggers,
                reason,
                output_scope="coordinated",
            )
            available = reason is None and sample is not None
            threshold = normalized.gamepad_trigger_control.neutral_threshold
            neutral = available and all(
                all(abs(raw[i]) <= threshold for i in getattr(normalized.gamepad_trigger_control, side).axes)
                and triggers[side] <= threshold
                for side in side_to_endpoint
            )
        else:
            raise ValueError("coordinated input requires explicit gamepad control")

        provider_epoch = None if sample is None else sample.diagnostics.get("provider_session_id")
        device = None if sample is None or sample.gamepad is None else (sample.gamepad.index, sample.gamepad.id)
        source_epoch = None if provider_epoch is None else sha256(
            json.dumps((provider_epoch, device), separators=(",", ":")).encode()
        ).hexdigest()
        payload = {
            "raw": raw,
            "buttons": [] if sample is None or sample.gamepad is None else
                [(b.pressed, b.value) for b in sample.gamepad.buttons],
            "frame": control_frame,
            "sequence": None if sample is None else sample.sequence,
            "timestamp": frame.timestamp_s,
            "source_epoch": source_epoch,
        }
        digest = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        return CoordinatedInput(
            tuple(EndpointVelocity(endpoint, intents[side].local_endpoint_velocity_m_s, control_frame)
                  for side, endpoint in side_to_endpoint.items()),
            source_epoch,
            None if sample is None else sample.sequence,
            frame.timestamp_s,
            received_at_s,
            available,
            bool(neutral),
            digest,
        )


VIEWER_CONTROL_MAPPING_PLUGIN = ControlMappingPlugin(
    identity=VIEWER_CONTROL_MAPPING_IDENTITY,
    strategy=ViewerKeyboardGamepadMappingStrategy(),
    session_strategy_factory=lambda: ViewerKeyboardGamepadMappingStrategy(session=True),
    accepted_input_sample_schemas=frozenset({VIEWER_CONTROL_SAMPLE_IDENTITY}),
    parameter_contract=ParameterContract(
        (
            ParameterField("keyboard_config", object, required=False),
            ParameterField("gamepad_speed_m_s", float, required=False),
            ParameterField("gamepad_deadzone", float, required=False),
            ParameterField("gamepad_max_delta_m", float, required=False),
            ParameterField("gamepad_axis_map", object, required=False),
            ParameterField("gamepad_trigger_control", object, required=False),
        )
    ),
    control_frame=None,
    comparison_family_identity=VersionedIdentity("viewer_keyboard_gamepad_comparison", 1),
    mapping_semantics_identity=VIEWER_MAPPING_SEMANTICS_IDENTITY,
    command_semantics_routes=frozenset(
        {
            local_endpoint_velocity_command_route(
                route_identity=LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
                control_semantics_identity=VIEWER_MAPPING_SEMANTICS_IDENTITY,
            )
        }
    ),
    parameter_normalizer=normalize_viewer_control_mapping_parameters,
)


__all__ = [
    "VIEWER_CONTROL_MAPPING_IDENTITY",
    "VIEWER_CONTROL_MAPPING_PLUGIN",
    "ViewerControlMappingParameters",
    "build_viewer_control_mapping_parameters",
    "normalize_viewer_control_mapping_parameters",
    "VIEWER_CONTROL_SAMPLE_IDENTITY",
    "VIEWER_MAPPING_SEMANTICS_IDENTITY",
    "ViewerKeyboardGamepadMappingStrategy",
]
