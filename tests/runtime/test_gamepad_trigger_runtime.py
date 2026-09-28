import asyncio
from dataclasses import replace
import pytest

from xpotato_sim.plugins.input_sources.viewer import ViewerInputSource
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.runtime.control.viewer_control_ingress import ingest_viewer_control_message
from xpotato_sim.runtime.execution.input_step_loop import (
    build_runtime_input_source_step_loop_plan,
    run_runtime_input_source_step_loop,
)
from xpotato_sim.schemas import ViewerControlKeyboardMessage, ViewerControlMessage
from tests.support.transport_doubles import NoOpStatePublisher
from tests.plugins.mappings.viewer_keyboard_gamepad_mapping.test_gamepad_triggers import (
    message,
    parameters,
)


def make_plan(side):
    source = ViewerInputSource(clock=lambda: 0.)
    profile = load_launch_profile(f"sim-gamepad-{side}-xyz")
    selection = select_runtime_input_source(
        "viewer", steps=1, control_mapping_parameters=profile.mapping_parameters,
    )
    plan = build_runtime_input_source_step_loop_plan(
        selection, viewer_clock=lambda: 0., viewer_input_source=source,
        publisher=NoOpStatePublisher(),
    )
    return source, plan
def run_sequence(source, plan, messages):
    pending = iter(messages[1:])

    class Publisher:
        async def publish(self, state):
            nxt = next(pending, None)
            if nxt is not None:
                ingest_viewer_control_message(source, nxt)

    plan.pipeline.publisher = Publisher()
    ingest_viewer_control_message(source, messages[0])
    return asyncio.run(run_runtime_input_source_step_loop(
        plan, steps=len(messages), dt_s=1 / 60,
    ))


def keyboard_message(key=None):
    return ViewerControlMessage(
        type="viewer_control_message",
        timestamp_s=0. if key is None else 1 / 60,
        source_kind="keyboard",
        keyboard=ViewerControlKeyboardMessage(
            active_key_codes=() if key is None else (key,),
            key_state={} if key is None else {key: True},
            focus_state="focused",
            zero_state=key is None,
        ),
    )


@pytest.mark.parametrize("side", ("left", "right"))
@pytest.mark.parametrize("key,axis,sign", [
    ("KeyD", 0, 1), ("KeyA", 0, -1),
    ("KeyW", 1, 1), ("KeyS", 1, -1),
    ("Space", 2, 1), ("ShiftLeft", 2, -1),
])
def test_six_directions_match_keyboard_in_mujoco(side, key, axis, sign):
    source, plan = make_plan(side)
    offset = 0 if side == "left" else 2
    sign_button = 4 if side == "left" else 5
    trigger_index = 0 if side == "left" else 1
    raw = [0.] * 4
    triggers = [0., 0.]
    held = ()

    if axis < 2:
        raw[offset + axis] = 1.0 * sign if axis == 0 else -1.0 * sign
    else:
        triggers[trigger_index] = 1.0
        held = (sign_button,) if sign < 0 else ()

    neutral = message(held=held, sequence=0)
    move = message(tuple(raw), triggers=tuple(triggers), held=held, sequence=1)
    record = run_sequence(source, plan, [neutral, move])[-1]

    keyboard_source = ViewerInputSource(clock=lambda: 0.)
    keyboard_plan = build_runtime_input_source_step_loop_plan(
        select_runtime_input_source("viewer", steps=1),
        viewer_clock=lambda: 0.,
        viewer_input_source=keyboard_source,
        publisher=NoOpStatePublisher(),
    )
    expected = run_sequence(
        keyboard_source, keyboard_plan,
        [keyboard_message(), keyboard_message(key)],
    )[-1]
    assert record.state.qpos == pytest.approx(expected.state.qpos, abs=1e-12)
    assert record.state.metadata["actual_tip_delta_m"] == pytest.approx(
        expected.state.metadata["actual_tip_delta_m"], abs=1e-12,
    )
    assert record.state.metadata["gamepad_trigger_control_v1"]["output_side"] == side
    assert record.intent.metadata["local_endpoint_velocity_m_s"] == expected.intent.metadata["local_endpoint_velocity_m_s"]
def test_pipeline_session_reset_isolates_trigger_sign_state():
    source, plan = make_plan("left")
    source2, plan2 = make_plan("left")

    def mapped(src, current_plan, msg):
        ingest_viewer_control_message(src, msg)
        return current_plan.pipeline.map_input(src.read_frame())

    mapped(source, plan, message(held=(4,), sequence=0))
    negative = mapped(source, plan, message(triggers=(.55, 0.), held=(4,), sequence=1))
    assert negative.metadata["local_endpoint_velocity_m_s"][2] < 0

    unarmed = mapped(source2, plan2, message(triggers=(.55, 0.), sequence=1))
    assert unarmed.metadata["local_endpoint_velocity_m_s"][2] == 0
    assert unarmed.metadata["gamepad_trigger_control_v1"]["sides"]["left"]["status"] == "waiting_trigger_neutral"

    plan.pipeline.reset_mapping_session()
    after = mapped(source, plan, message(triggers=(.55, 0.), sequence=2))
    assert after.metadata["local_endpoint_velocity_m_s"][2] == 0


def test_bounded_loop_drops_session_on_end_and_next_attempt():
    source, plan = make_plan("left")
    rows = run_sequence(source, plan, [message(sequence=0)])
    assert rows[0].intent.metadata["gamepad_trigger_control_v1"]["sides"]["left"]["status"] == "armed"
    assert plan.pipeline._mapping_strategy is None

    rows = run_sequence(source, plan, [message(triggers=(.55, 0.), sequence=1)])
    assert rows[0].intent.values[2] == 0
def test_invalid_input_drops_mapping_session():
    source, plan = make_plan("left")
    bad = message(sequence=1)
    bad = replace(bad, gamepad=replace(bad.gamepad, raw_axes=(1., 0.)))
    with pytest.raises(ValueError):
        run_sequence(source, plan, [message(sequence=0), bad])
    assert plan.pipeline._mapping_strategy is None


@pytest.mark.parametrize("side", ("left", "right"))
def test_xyz_profiles_use_trigger_control_and_old_generic_profile_is_unchanged(side):
    profile = load_launch_profile(f"sim-gamepad-{side}-xyz")
    assert profile.mapping_parameters == parameters(side)
    resolved = profile.to_dict()["resolved"]["mapping_parameters"]["gamepad_trigger_control"]
    assert resolved["output_side"] == side
    assert resolved[side]["trigger_button"] == (6 if side == "left" else 7)
    assert resolved[side]["sign_button"] == (4 if side == "left" else 5)
    assert load_launch_profile("sim-gamepad").mapping_parameters == {}
