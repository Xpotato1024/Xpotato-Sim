import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it } from "node:test";
import loadMujoco, { type MainModule } from "@mujoco/mujoco";
import { applyMujocoDisplayPose, createMujocoDisplayOption } from "../src/wasm-scene/mujocoDisplayPose.js";
import { matrixFromMujocoGeom } from "../src/wasm-scene/mujocoSceneTransforms.js";

const api = await loadMujoco();
const repo = resolve(process.cwd(), "../..");
const resources = resolve(repo, "src/xpotato_sim/plugins/robots/fast_arm/adapter/resources");
const core = resolve(repo, "src/xpotato_sim/plugins/robots/fast_arm/core/src/fast_arm_core/resources/model");

// 接触はbaselineだけで評価する。表示候補は同じqposから描画値のみを更新する。
const contactModel = `<mujoco model="display-contact">
  <asset><mesh name="tetra" vertex="0 0 0 .05 0 0 0 .05 0 0 0 .05" face="0 2 1 0 1 3 0 3 2 1 2 3"/></asset>
  <worldbody>
    <site name="anchor" pos="0 -.3 .5"/>
    <geom name="wrap" type="sphere" pos="-.15 -.15 .5" size=".04" contype="0" conaffinity="0"/>
    <body name="left" pos="-.3 0 .5">
      <joint name="left_slide" type="slide" axis="1 0 0"/>
      <geom name="left_tip" type="sphere" size=".1"/><site name="left_site"/>
      <camera name="attached" pos="0 -.5 .5" mode="trackcom"/>
      <light name="attached_light" pos="0 -.2 .3" mode="targetbodycom" target="object"/>
    </body>
    <body name="right" pos=".3 0 .5">
      <joint name="right_slide" type="slide" axis="1 0 0"/>
      <geom name="right_tip" type="sphere" size=".1"/><site name="right_site"/>
    </body>
    <body name="object" pos="0 0 .5">
      <freejoint name="object_free"/>
      <geom name="cube" type="box" size=".1 .1 .1"/>
      <geom name="mesh" type="mesh" mesh="tetra" contype="0" conaffinity="0"/>
      <site name="object_site" pos="0 0 .1"/>
    </body>
  </worldbody>
  <deformable><flex name="attached_flex" dim="2" body="object object object"
    vertex="0 0 .15 .05 0 .15 0 .05 .15" element="0 1 2" radius=".005"/></deformable>
  <tendon><spatial name="cable" width=".005"><site site="left_site"/><geom geom="wrap"/><site site="anchor"/></spatial></tendon>
  <actuator><motor joint="left_slide"/></actuator>
  <sensor><rangefinder site="object_site"/></sensor>
</mujoco>`;

const pose = (left = 0, right = 0, x = 0, y = 0, z = .5, angle = 0): number[] =>
  [left, right, x, y, z, Math.cos(angle / 2), 0, 0, Math.sin(angle / 2)];

const transformFields = [
  "xpos", "xquat", "xmat", "xipos", "ximat", "subtree_com",
  "site_xpos", "site_xmat", "geom_xpos", "geom_xmat",
  "cam_xpos", "cam_xmat", "light_xpos", "light_xdir",
  "ten_length", "ten_wrapadr", "ten_wrapnum", "wrap_xpos", "wrap_obj", "flexvert_xpos",
] as const;

function snapshotScene(model: any, data: any, option: any): unknown {
  const scene = new api.MjvScene(model, 1000);
  const perturb = new api.MjvPerturb();
  const camera = new api.MjvCamera();
  try {
    api.mjv_updateScene(model, data, option, perturb, camera, api.mjtCatBit.mjCAT_ALL.value, scene);
    const vector = scene.geoms;
    const geoms = [];
    try {
      assert.equal(vector.size(), scene.ngeom);
      for (let index = 0; index < scene.ngeom; index++) {
        const geom = vector.get(index)!;
        try {
          geoms.push({
            type: geom.type, objtype: geom.objtype, objid: geom.objid, dataid: geom.dataid,
            category: geom.category, matid: geom.matid,
            size: Array.from(geom.size), pos: Array.from(geom.pos), mat: Array.from(geom.mat),
            rgba: Array.from(geom.rgba), matrix: matrixFromMujocoGeom(geom).elements.slice(),
          });
        } finally { geom.delete(); }
      }
    } finally { vector.delete(); }
    return { geoms, nlight: scene.nlight, nflex: scene.nflex,
      flexvert: Array.from(scene.flexvert), flexface: Array.from(scene.flexface),
      flexnormal: Array.from(scene.flexnormal), flexfaceused: Array.from(scene.flexfaceused) };
  } finally { scene.delete(); perturb.delete(); camera.delete(); }
}

function compareSequence(xml: string, poses: readonly number[][], vfs?: any, contacts?: readonly number[]): void {
  const model = vfs === undefined ? api.MjModel.from_xml_string(xml) : api.MjModel.from_xml_string(xml, vfs);
  const baseline = new api.MjData(model);
  const display = new api.MjData(model);
  const option = createMujocoDisplayOption(api);
  try {
    for (const [index, qpos] of poses.entries()) {
      assert.equal(qpos.length, model.nq);
      baseline.qpos.set(qpos);
      api.mj_forward(model, baseline);
      const baselineTimer = baseline.timer.get(api.mjtTimer.mjTIMER_POS_COLLISION.value)!;
      try { assert.ok(baselineTimer.number > 0); }
      finally { baselineTimer.delete(); }
      if (contacts) assert.equal(baseline.ncon, contacts[index], `contact case ${index}`);
      display.time = 7.25;
      display.qvel.fill(.125);
      display.ctrl.fill(.25);
      const before = { time: display.time, qvel: Array.from(display.qvel), ctrl: Array.from(display.ctrl) };
      applyMujocoDisplayPose(api, model, display, qpos);
      assert.deepEqual(Array.from(display.qpos), qpos);
      assert.deepEqual({ time: display.time, qvel: Array.from(display.qvel), ctrl: Array.from(display.ctrl) }, before);
      for (const field of transformFields) {
        assert.deepEqual(Array.from((display as any)[field]), Array.from((baseline as any)[field]), `${index}: ${field}`);
        assert.deepEqual(Buffer.from((display as any)[field].buffer, (display as any)[field].byteOffset, (display as any)[field].byteLength),
          Buffer.from((baseline as any)[field].buffer, (baseline as any)[field].byteOffset, (baseline as any)[field].byteLength), `${index}: ${field} bytes`);
      }
      assert.deepEqual(snapshotScene(model, display, option), snapshotScene(model, baseline, option), `${index}: scene`);
      assert.equal(display.ncon, 0);
      assert.equal(display.nefc, 0);
      assert.ok(Array.from(display.solver_niter).every(value => value === 0));
      // timer vectorはMjDataが所有するborrowed viewであり、vector自体はdeleteしない。
      const timers = display.timer;
      for (let timerIndex = 0; timerIndex < timers.size(); timerIndex++) {
        const timer = timers.get(timerIndex)!;
        try { assert.equal(timer.number, 0, `display timer ${timerIndex}`); }
        finally { timer.delete(); }
      }
    }
  } finally { option.delete(); baseline.delete(); display.delete(); model.delete(); }
}

describe("installed MuJoCo display pose", () => {
  it("uses the installed single WASM API and checks both binding declarations", () => {
    assert.equal(api.mj_versionString(), "3.9.0");
    assert.equal(typeof api.mj_fwdKinematics, "function");
    for (const path of ["mujoco.d.ts", "mt/mujoco.d.ts"]) {
      assert.match(readFileSync(resolve(process.cwd(), "node_modules/@mujoco/mujoco", path), "utf8"), /mj_fwdKinematics\(_0: MjModel, _1: MjData\): void/);
    }
  });

  it("matches noncontact, one-sided and bilateral contact through repeated holds and return", () => {
    compareSequence(contactModel, [pose(), pose(.11), pose(.11, -.11), ...Array.from({length: 12}, () => pose(.11, -.11)), pose()],
      undefined, [0, 1, 2, ...Array(12).fill(2), 0]);
  });

  it("matches free-object translation and rotation, camera/light, tendon/flex/mesh and return", () => {
    compareSequence(contactModel, [pose(), pose(.02, -.01, .2, .3, .9, .7), pose(.1, -.04, -.3, .1, .7, -1.1), pose()]);
  });

  it("reuses the native robot home and every published native sweep fixture pose", () => {
    const vfs = new api.MjVFS();
    try {
      vfs.addBuffer("arm.xml", new Uint8Array(readFileSync(resolve(core, "arm.xml"))));
      for (const mesh of readdirSync(resolve(core, "meshes"))) {
        vfs.addBuffer(`meshes/${mesh}`, new Uint8Array(readFileSync(resolve(core, "meshes", mesh))));
      }
      const xml = readFileSync(resolve(resources, "mujoco/scene.xml"), "utf8");
      const homeModel = api.MjModel.from_xml_string(xml, vfs);
      let home: number[];
      try {
        const index = api.mj_name2id(homeModel, api.mjtObj.mjOBJ_KEY.value, "home");
        assert.ok(index >= 0);
        home = Array.from(homeModel.key_qpos.slice(index * homeModel.nq, (index + 1) * homeModel.nq));
        assert.ok(homeModel.nmesh > 0);
      } finally { homeModel.delete(); }
      const fixture = JSON.parse(readFileSync(resolve(resources, "fixtures/fast_arm_sweep_x_qpos.json"), "utf8"));
      compareSequence(xml, [home, ...fixture.frames.map((frame: {qpos: number[]}) => frame.qpos), home], vfs);
    } finally { vfs.delete(); }
  });

  it("recreates model/data/options across public fixture model switches", () => {
    const fixtureRoot = resolve(repo, "tests/fixtures/robot_plugins/assets/mujoco/fixture_bot");
    const vfs = new api.MjVFS();
    try {
      vfs.addBuffer("robot.xml", new Uint8Array(readFileSync(resolve(fixtureRoot, "robot.xml"))));
      compareSequence(contactModel, [pose(.11, -.11)]);
      compareSequence(readFileSync(resolve(fixtureRoot, "model.xml"), "utf8"), [[.25], [-.4], [.25]], vfs);
      compareSequence(contactModel, [pose()]);
    } finally { vfs.delete(); }
  });

  it("does not display stale contact/force/sensor geoms after a previously solved pose", () => {
    const model = api.MjModel.from_xml_string(contactModel);
    const data = new api.MjData(model);
    const option = createMujocoDisplayOption(api);
    try {
      data.qpos.set(pose(.11, -.11));
      api.mj_forward(model, data);
      assert.equal(data.ncon, 2);
      const staleContactCount = data.ncon;
      applyMujocoDisplayPose(api, model, data, pose(0, 0, .2, .3, .9));
      assert.equal(data.ncon, staleContactCount);
      const fresh = new api.MjData(model);
      try {
        applyMujocoDisplayPose(api, model, fresh, pose(0, 0, .2, .3, .9));
        assert.deepEqual(snapshotScene(model, data, option), snapshotScene(model, fresh, option));
      } finally { fresh.delete(); }
      assert.equal(option.frame, api.mjtFrame.mjFRAME_NONE.value);
      for (const name of ["mjVIS_CONTACTPOINT", "mjVIS_CONTACTFORCE", "mjVIS_CONTACTSPLIT", "mjVIS_CONSTRAINT",
        "mjVIS_ISLAND", "mjVIS_PERTFORCE", "mjVIS_ACTUATOR", "mjVIS_ACTIVATION", "mjVIS_RANGEFINDER"] as const) {
        assert.equal(option.flags[api.mjtVisFlag[name].value], 0, name);
      }
      assert.equal(option.flags[api.mjtVisFlag.mjVIS_TENDON.value], 1);
    } finally { option.delete(); data.delete(); model.delete(); }
  });

  it("propagates a native update failure without full-forward fallback", () => {
    const model = api.MjModel.from_xml_string(contactModel);
    const data = new api.MjData(model);
    try {
      const failure = new Error("native display failure");
      const failingApi: Pick<MainModule, "mj_fwdKinematics"> = { mj_fwdKinematics() { throw failure; } };
      assert.throws(() => applyMujocoDisplayPose(failingApi, model, data, pose()), error => error === failure);
    } finally { data.delete(); model.delete(); }
  });
});
