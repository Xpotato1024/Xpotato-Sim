"""左右の鏡映・慣性・名前addressを実MuJoCoで照合する。実機計測ではない。"""
from dataclasses import replace
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
import pytest

from fast_arm_core.assembly import FastArmAssembly, FastArmInstance, resolve_assembly_addresses
from fast_arm_core.assembly_model import build_fast_arm_assembly_model

S = np.diag([1.,-1.,1.])


def assembly():
    return FastArmAssembly((
        FastArmInstance("right",False,(0,-.4,0),(1,0,0,0)),
        FastArmInstance("left",True,(0,.4,0),(1,0,0,0)),
    ))


def load(spec):
    bundle = build_fast_arm_assembly_model(spec)
    model = mujoco.MjModel.from_xml_string(bundle.xml.decode(), dict(bundle.assets))
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model,data,0)
    mujoco.mj_forward(model,data)
    return model, data, resolve_assembly_addresses(model,spec)


@pytest.mark.parametrize("selected", [(0,), (1,), (0,1), (1,0)])
def test_single_original_single_mirror_and_both_share_one_model(selected):
    source = assembly()
    spec = FastArmAssembly(tuple(source.instances[i] for i in selected))
    model, data, addresses = load(spec)
    assert (model.nq,model.nv,model.nu) == (4*len(selected),)*3
    assert tuple(a.arm_id for a in addresses) == spec.arm_ids
    for address in addresses:
        assert len(address.joint_names) == 4
        assert np.isfinite(data.site_xpos[address.tip_site_id]).all()
        np.testing.assert_array_equal(model.actuator_forcerange[list(address.actuator_ids)],
                                      [[-24,24],[-24,24],[-12,12],[-12,12]])


def test_random_pose_fk_jacobian_mass_and_gravity_are_true_reflections():
    model, data, (right,left) = load(assembly())
    rpos, lpos = np.array([0,-.4,0]), np.array([0,.4,0])
    rng = np.random.default_rng(569)
    qrs, qls = list(right.qpos_addresses), list(left.qpos_addresses)
    drs, dls = list(right.dof_addresses), list(left.dof_addresses)
    for q in rng.uniform(-1.8,1.8,(32,4)):
        data.qpos[qrs] = q; data.qpos[qls] = q
        data.qvel[:] = 0
        mujoco.mj_forward(model,data)
        for suffix in ("base_link","sholder_link_1","sholder_link_2","upper_arm_link","fore_arm_link"):
            rb, lb = model.body("right__"+suffix).id, model.body("left__"+suffix).id
            np.testing.assert_allclose(data.xpos[lb]-lpos, S@(data.xpos[rb]-rpos), atol=1e-12)
            np.testing.assert_allclose(data.xmat[lb].reshape(3,3), S@data.xmat[rb].reshape(3,3)@S, atol=1e-12)
            np.testing.assert_allclose(data.xipos[lb]-lpos, S@(data.xipos[rb]-rpos), atol=1e-12)
            ri, li = data.ximat[rb].reshape(3,3), data.ximat[lb].reshape(3,3)
            np.testing.assert_allclose(li@np.diag(model.body_inertia[lb])@li.T,
                                      S@ri@np.diag(model.body_inertia[rb])@ri.T@S, atol=1e-12)
            assert model.body_mass[lb] == model.body_mass[rb]
        jp_r, jr_r, jp_l, jr_l = (np.zeros((3,model.nv)) for _ in range(4))
        mujoco.mj_jacSite(model,data,jp_r,jr_r,right.tip_site_id)
        mujoco.mj_jacSite(model,data,jp_l,jr_l,left.tip_site_id)
        np.testing.assert_allclose(jp_l[:,dls], S@jp_r[:,drs], atol=1e-12)
        np.testing.assert_allclose(jr_l[:,dls], -S@jr_r[:,drs], atol=1e-12)
        np.testing.assert_allclose(jp_r[:,dls], 0, atol=1e-12)
        np.testing.assert_allclose(jp_l[:,drs], 0, atol=1e-12)
        mass = np.zeros((model.nv,model.nv)); mujoco.mj_fullM(model,mass,data.qM)
        np.testing.assert_allclose(mass[np.ix_(drs,drs)],mass[np.ix_(dls,dls)],atol=1e-11)
        np.testing.assert_allclose(mass[np.ix_(drs,dls)],0,atol=1e-12)
        np.testing.assert_allclose(data.qfrc_bias[drs],data.qfrc_bias[dls],atol=1e-10)


def test_object_freejoint_before_arms_does_not_corrupt_qpos_or_dof_addresses():
    spec = assembly(); bundle = build_fast_arm_assembly_model(spec)
    tree = ET.fromstring(bundle.xml); tree.remove(tree.find("keyframe"))
    obj = ET.Element("body", {"name":"object","pos":"2 0 1"})
    ET.SubElement(obj,"freejoint",{"name":"object_free"})
    ET.SubElement(obj,"geom",{"type":"sphere","size":".1","mass":"1"})
    tree.find("worldbody").insert(0,obj)
    model = mujoco.MjModel.from_xml_string(ET.tostring(tree,encoding="unicode"),dict(bundle.assets))
    right,left = resolve_assembly_addresses(model,spec)
    assert right.qpos_addresses == (7,8,9,10)
    assert right.dof_addresses == (6,7,8,9)
    assert left.qpos_addresses == (11,12,13,14)
    assert left.dof_addresses == (10,11,12,13)


def test_mount_rotation_is_applied_after_local_mirror():
    original = assembly().instances[1]
    s1 = FastArmAssembly((replace(original,position_m=(0,0,0)),))
    s2 = FastArmAssembly((replace(original,position_m=(1,2,3),quaternion_wxyz=(0,0,0,1)),))
    _,d1,(a1,) = load(s1); _,d2,(a2,) = load(s2)
    np.testing.assert_allclose(d2.site_xpos[a2.tip_site_id],
                              np.diag([-1,-1,1])@d1.site_xpos[a1.tip_site_id]+[1,2,3],atol=1e-12)


def test_source_collision_settings_are_preserved_not_claimed_as_safe_geometry():
    model,_,_ = load(assembly())
    mesh_geoms = np.flatnonzero(model.geom_type == mujoco.mjtGeom.mjGEOM_MESH)
    assert len(mesh_geoms) == 10
    np.testing.assert_array_equal(model.geom_contype[mesh_geoms],0)
    np.testing.assert_array_equal(model.geom_conaffinity[mesh_geoms],0)


def test_mesh_world_bounds_are_reflections_not_only_joint_sites():
    model,data,_ = load(assembly())
    for suffix in ('geom_1','geom_2','geom_3','geom_4','geom_5'):
        bounds=[]
        for side,offset in [('right',np.array([0,-.4,0])),('left',np.array([0,.4,0]))]:
            geom = model.geom(side+'__'+suffix).id
            mesh = model.geom_dataid[geom]
            start, count = model.mesh_vertadr[mesh], model.mesh_vertnum[mesh]
            vertices = model.mesh_vert[start:start+count].astype(float)
            points = vertices@data.geom_xmat[geom].reshape(3,3).T + data.geom_xpos[geom] - offset
            bounds.append(points)
        reflected = bounds[0]@S
        np.testing.assert_allclose(bounds[1].min(axis=0),reflected.min(axis=0),atol=2e-6)
        np.testing.assert_allclose(bounds[1].max(axis=0),reflected.max(axis=0),atol=2e-6)


def test_both_actuator_chains_evolve_in_one_dynamics_clock():
    model,data,(right,left) = load(assembly())
    q = np.array([.15,-.7,.2,-1.])
    data.qpos[list(right.qpos_addresses)] = q
    data.qpos[list(left.qpos_addresses)] = q
    target = q + np.array([.02,.01,-.02,.01])
    data.ctrl[list(right.actuator_ids)] = target
    data.ctrl[list(left.actuator_ids)] = target
    model.opt.timestep = .002
    for _ in range(30):
        mujoco.mj_step(model,data)
    assert data.time == pytest.approx(.06)
    np.testing.assert_allclose(data.qpos[list(right.qpos_addresses)],data.qpos[list(left.qpos_addresses)],atol=1e-10)
    np.testing.assert_allclose(data.qvel[list(right.dof_addresses)],data.qvel[list(left.dof_addresses)],atol=1e-10)
    np.testing.assert_allclose(data.actuator_force[list(right.actuator_ids)],data.actuator_force[list(left.actuator_ids)],atol=1e-9)
    assert not np.allclose(data.qpos[list(right.qpos_addresses)],q)
