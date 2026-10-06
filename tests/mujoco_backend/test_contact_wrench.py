"""接触frameの許容差・向き・finite拒否と、geom2のworld wrenchを確認する。"""
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from xpotato_sim.mujoco_backend.contact_wrench import read_contact_wrench


def read(monkeypatch, frame, local=(1., 2., 3., 4., 5., 6.)):
    def force(model, data, index, buffer):
        buffer[:] = local
    monkeypatch.setattr(mujoco, "mj_contactForce", force)
    data = SimpleNamespace(ncon=1, contact=[SimpleNamespace(efc_address=0)])
    return read_contact_wrench(None, data, 0, frame)


def test_world_force_and_torque_keep_row_major_frame_and_geom2_sign(monkeypatch):
    frame = (0., 1., 0., -1., 0., 0., 0., 0., 1.)
    wrench = read(monkeypatch, frame)
    assert wrench.local == (1., 2., 3., 4., 5., 6.)
    assert wrench.force_on_geom2_world_n == (-2., 1., 3.)
    assert wrench.torque_on_geom2_world_nm == (-5., 4., 6.)


@pytest.mark.parametrize("frame", [
    np.eye(3), np.diag((1.+2e-9, 1., 1.)),
    np.array([[np.cos(.37), np.sin(.37), 0.], [-np.sin(.37), np.cos(.37), 0.], [0., 0., 1.]]),
])
def test_orthonormal_tolerance_accepts_same_frames(monkeypatch, frame):
    result = read(monkeypatch, frame)
    assert result.force_on_geom2_world_n == tuple(frame.T @ np.array((1., 2., 3.)))


@pytest.mark.parametrize("frame", [
    np.diag((-1., 1., 1.)), np.diag((1.+6e-9, 1., 1.)),
    np.zeros((3, 3)), np.full((3, 3), float("nan")), np.full((3, 3), float("inf")),
])
def test_invalid_contact_frames_are_rejected(monkeypatch, frame):
    with pytest.raises(ValueError, match="orthonormal"):
        read(monkeypatch, frame)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_solver_wrench_is_rejected(monkeypatch, value):
    with pytest.raises(ValueError, match="nonfinite"):
        read(monkeypatch, np.eye(3), (value, 0., 0., 0., 0., 0.))
