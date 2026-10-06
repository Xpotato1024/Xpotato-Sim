import type { MainModule, MjData, MjModel, MjvOption } from "@mujoco/mujoco";

/** 検証済みqposを描画用native変換へ反映する。積分・衝突・制約・sensor評価は行わない。 */
export function applyMujocoDisplayPose(
  api: Pick<MainModule, "mj_fwdKinematics">,
  model: MjModel,
  data: MjData,
  qpos: readonly number[],
): void {
  data.qpos.set(qpos);
  api.mj_fwdKinematics(model, data);
}

/** 接触・力はbackend overlayが所有するため、未評価のWASM診断値をsceneへ混ぜない。 */
export function createMujocoDisplayOption(api: MainModule): MjvOption {
  const option = new api.MjvOption();
  for (const flag of [
    api.mjtVisFlag.mjVIS_CONTACTPOINT,
    api.mjtVisFlag.mjVIS_CONTACTFORCE,
    api.mjtVisFlag.mjVIS_CONTACTSPLIT,
    api.mjtVisFlag.mjVIS_CONSTRAINT,
    api.mjtVisFlag.mjVIS_ISLAND,
    api.mjtVisFlag.mjVIS_PERTFORCE,
    api.mjtVisFlag.mjVIS_ACTUATOR,
    api.mjtVisFlag.mjVIS_ACTIVATION,
    api.mjtVisFlag.mjVIS_RANGEFINDER,
  ]) {
    option.flags[flag.value] = 0;
  }
  option.frame = api.mjtFrame.mjFRAME_NONE.value;
  return option;
}
