/**
 * Python/MuJoCo由来qposをloaded model orderingのままviewerへ同期する。
 * qposを推定・並べ替えせず、長さ不一致と欠落をfail closedに扱う。
 */
import type { TransportPayloadV0 } from "../types/transportPayload.js";
import { validateSceneStateValues, decodeSceneStateLayout } from "../robot-profiles/sceneStateLayout.js";
import { parseContactTaskPresentationV1 } from "../contact/contactTaskLog.js";
import {
  getFrameByIndex,
  getNextFrameIndex,
  getPreviousFrameIndex,
  parseQposFixture,
  validateQposFixtureForModel,
  type QposFixture,
  type QposFixtureFrame,
} from "./qposFrameTypes.js";

export type { QposFixture, QposFixtureFrame } from "./qposFrameTypes.js";

import type { ViewerRobotProfile } from "../robot-profiles/types.js";

export interface ModelKeyframeLike {
  readonly qpos: ArrayLike<number>;
  delete(): void;
}

export interface ModelWithNamedKeyframesLike {
  readonly nq: number;
  key(name: string): ModelKeyframeLike;
}

export function formatQpos(values: readonly number[]): string {
  return `[${Array.from(values, (value) => Number(value).toString()).join(", ")}]`;
}

export function ensureQposLength(values: readonly number[], modelNq: number, label = "qpos"): readonly number[] {
  if (!Number.isInteger(modelNq) || modelNq < 1) {
    throw new Error("model.nq must be a positive integer");
  }

  if (values.length !== modelNq) {
    throw new Error(`${label} length mismatch: expected ${modelNq}, got ${values.length}`);
  }

  return values;
}

/** plugin declarationが指定したkeyframeだけを初期qposとして解決する。 */
export function resolveInitialKeyframeQpos(
  values: ArrayLike<number>,
  modelNq: number,
  keyframeName: string,
): readonly number[] {
  const qpos = Array.from(values);
  if (!qpos.every((value) => Number.isFinite(value))) {
    throw new Error(`${keyframeName} keyframe qpos must contain only finite values`);
  }
  return ensureQposLength(qpos, modelNq, `${keyframeName} keyframe qpos`);
}

export function resolveNamedInitialKeyframe(
  model: ModelWithNamedKeyframesLike,
  profile: ViewerRobotProfile,
): { qpos: readonly number[]; sourceLabel: string } {
  let keyframe: ModelKeyframeLike;
  try {
    keyframe = model.key(profile.initialKeyframeName);
  } catch (error) {
    throw new Error(`missing MuJoCo ${profile.initialKeyframeName} keyframe`, { cause: error });
  }
  try {
    return {
      qpos: resolveInitialKeyframeQpos(keyframe.qpos, model.nq, profile.initialKeyframeName),
      sourceLabel: profile.initialPoseSourceLabel,
    };
  } finally {
    keyframe.delete();
  }
}

export function loadQposFixtureFromUrl(fixtureUrl: string, modelNq: number): Promise<QposFixture> {
  return fetch(fixtureUrl).then(async (response) => {
    if (!response.ok) {
      throw new Error(`failed to fetch ${fixtureUrl}: ${response.status} ${response.statusText}`);
    }

    const raw = (await response.json()) as unknown;
    return validateQposFixtureForModel(parseQposFixture(raw), modelNq);
  });
}

export function getCurrentFrame(fixture: QposFixture, frameIndex: number): QposFixtureFrame {
  return getFrameByIndex(fixture, frameIndex);
}

export function stepNextFrameIndex(currentFrameIndex: number, frameCount: number): number {
  return getNextFrameIndex(currentFrameIndex, frameCount);
}

export function stepPreviousFrameIndex(currentFrameIndex: number): number {
  return getPreviousFrameIndex(currentFrameIndex);
}

function requireRecord(value: unknown, name: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${name} must be an object`);
  }
  return value as Record<string, unknown>;
}

function requireExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
  name: string,
): void {
  const actual = Object.keys(value).sort();
  const sortedExpected = [...expected].sort();
  if (
    actual.length !== sortedExpected.length ||
    actual.some((key, index) => key !== sortedExpected[index])
  ) {
    throw new Error(`${name} fields do not match the versioned contract`);
  }
}

function requireNonEmptyString(value: unknown, name: string): string {
  if (typeof value !== "string" || value.length === 0 || value !== value.trim()) {
    throw new Error(`${name} must be a non-empty string`);
  }
  return value;
}

function requireSafeInteger(value: unknown, name: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) {
    throw new Error(`${name} must be a safe integer >= ${minimum}`);
  }
  return value as number;
}

function resolveContactSceneRobotQpos(
  payload: TransportPayloadV0,
  profile: ViewerRobotProfile,
): readonly number[] {
  const projection = requireRecord(
    payload.metadata.contact_scene_robot_qpos_v1,
    "contact_scene_robot_qpos_v1",
  );
  requireExactKeys(
    projection,
    [
      "schema_version",
      "scene_identity",
      "manifest_digest",
      "frame_index",
      "time_s",
      "source_qpos_dimension",
      "robot_profile_id",
      "model_contract_version",
      "robot_qpos_dimension",
      "robot_joint_names",
      "qpos_addresses",
    ],
    "contact_scene_robot_qpos_v1",
  );
  if (projection.schema_version !== "contact-scene-robot-qpos/v1") {
    throw new Error("unsupported contact-scene robot qpos schema version");
  }

  const contactPresentation = parseContactTaskPresentationV1(
    payload.metadata.contact_task_v1,
    {
      profileId: profile.profileId,
      profileContractVersion: profile.profileContractVersion,
    },
    { timeS: payload.time_s, frameIndex: payload.frame_index },
  );
  if (contactPresentation.status !== "available") {
    throw new Error("contact_task_v1 binding is missing, stale, or unavailable");
  }
  const contactBinding = contactPresentation.binding;
  const contactSample = contactPresentation.sample;
  if (contactBinding === null || contactSample === null) {
    throw new Error("validated contact_task_v1 binding or sample is missing");
  }
  if (
    contactSample.frameIndex !== payload.frame_index ||
    contactSample.simulationTimeS !== payload.time_s
  ) {
    throw new Error("contact_task_v1 sample was replayed or does not match payload frame/time");
  }

  const scene = requireRecord(projection.scene_identity, "scene_identity");
  requireExactKeys(scene, ["name", "version"], "scene_identity");
  const sceneName = requireNonEmptyString(scene.name, "scene_identity.name");
  const sceneVersion = requireSafeInteger(scene.version, "scene_identity.version", 1);
  const manifestDigest = requireNonEmptyString(
    projection.manifest_digest,
    "manifest_digest",
  );
  if (!/^sha256:[0-9a-f]{64}$/.test(manifestDigest)) {
    throw new Error("manifest_digest must be a lowercase SHA-256 digest");
  }
  if (
    manifestDigest !== contactBinding.manifest_digest ||
    sceneName !== contactBinding.scene_identity.name ||
    sceneVersion !== contactBinding.scene_identity.version
  ) {
    throw new Error("contact-scene qpos mapping does not match contact manifest binding");
  }

  const frameIndex = requireSafeInteger(projection.frame_index, "frame_index");
  const sourceQposDimension = requireSafeInteger(
    projection.source_qpos_dimension,
    "source_qpos_dimension",
    1,
  );
  const robotQposDimension = requireSafeInteger(
    projection.robot_qpos_dimension,
    "robot_qpos_dimension",
    1,
  );
  if (
    frameIndex !== payload.frame_index ||
    typeof projection.time_s !== "number" ||
    !Number.isFinite(projection.time_s) ||
    projection.time_s !== payload.time_s
  ) {
    throw new Error("contact-scene qpos mapping does not match payload frame/time");
  }
  if (
    projection.robot_profile_id !== profile.profileId ||
    projection.model_contract_version !== profile.modelContractVersion ||
    robotQposDimension !== profile.qposDimension
  ) {
    throw new Error("contact-scene qpos mapping does not match the loaded Robot profile");
  }
  if (
    sourceQposDimension !== payload.qpos.length ||
    robotQposDimension !== profile.jointNames.length ||
    !payload.qpos.every((value) => Number.isFinite(value))
  ) {
    throw new Error("contact-scene source qpos dimension or values are invalid");
  }

  const jointNames = projection.robot_joint_names;
  if (
    !Array.isArray(jointNames) ||
    jointNames.length !== robotQposDimension ||
    jointNames.some((name, index) => name !== profile.jointNames[index])
  ) {
    throw new Error("contact-scene canonical Robot joint name/order does not match the profile");
  }
  const addresses = projection.qpos_addresses;
  if (
    !Array.isArray(addresses) ||
    addresses.length !== robotQposDimension ||
    !addresses.every(
      (address) =>
        Number.isSafeInteger(address) &&
        (address as number) >= 0 &&
        (address as number) < sourceQposDimension,
    ) ||
    new Set(addresses).size !== addresses.length
  ) {
    throw new Error("contact-scene qpos addresses are missing, duplicated, or out of range");
  }

  return addresses.map((address) => payload.qpos[address as number]!);
}

/** live payload qposをnq検証して返し、欠落時にfixture/keyframeへfallbackしない。 */
export function resolveTransportQpos(
  payload: TransportPayloadV0 | null,
  modelNq: number,
  profile: ViewerRobotProfile,
): {
  status: "unavailable" | "invalid" | "ready";
  qpos: readonly number[] | null;
  errorMessage: string | null;
  currentFrameIndex: number | null;
  currentTimestampS: number | null;
  sourceLabel: string;
} {
  if (payload === null) {
    return {
      status: "unavailable",
      qpos: null,
      errorMessage: "transport payload unavailable",
      currentFrameIndex: null,
      currentTimestampS: null,
      sourceLabel: "transport payload unavailable",
    };
  }

  if (profile.sceneStateLayout) {
    try {
      const declared=decodeSceneStateLayout(payload.metadata.scene_state_layout_v1,profile.jointNames);
      if(JSON.stringify(declared)!==JSON.stringify(profile.sceneStateLayout))throw new Error("scene state layout mismatch");
      validateSceneStateValues(profile.sceneStateLayout,payload.qpos,payload.qvel);
    } catch(error) {
      return {status:"invalid",qpos:null,errorMessage:error instanceof Error ? error.message : "invalid full scene state",currentFrameIndex:payload.frame_index,currentTimestampS:payload.time_s,sourceLabel:"transport payload incompatible"};
    }
  }
  const backendProfileId = payload.metadata.robot_profile_id;
  if (backendProfileId !== profile.profileId) {
    return {
      status: "invalid",
      qpos: null,
      errorMessage: `backend/viewer robot profile mismatch: expected ${profile.profileId}, got ${String(backendProfileId ?? "missing")}`,
      currentFrameIndex: payload.frame_index,
      currentTimestampS: payload.time_s,
      sourceLabel: "transport payload incompatible",
    };
  }
  const backendModelContractVersion = payload.metadata.model_contract_version;
  if (backendModelContractVersion !== profile.modelContractVersion) {
    return {
      status: "invalid",
      qpos: null,
      errorMessage: `backend/viewer model contract mismatch: expected ${profile.modelContractVersion}, got ${String(backendModelContractVersion ?? "missing")}`,
      currentFrameIndex: payload.frame_index,
      currentTimestampS: payload.time_s,
      sourceLabel: "transport payload incompatible",
    };
  }
  if (modelNq !== (profile.sceneStateLayout?.qpos_dimension ?? profile.qposDimension)) {
    return {
      status: "invalid",
      qpos: null,
      errorMessage: `viewer model/profile qpos dimension mismatch: expected ${profile.qposDimension}, got ${modelNq}`,
      currentFrameIndex: payload.frame_index,
      currentTimestampS: payload.time_s,
      sourceLabel: "viewer model incompatible",
    };
  }
  const backendJointNames = payload.metadata.robot_joint_names;
  const backendQposDimension = payload.metadata.robot_qpos_dimension;
  if (backendQposDimension !== profile.qposDimension) {
    return {
      status: "invalid",
      qpos: null,
      errorMessage: `backend/viewer qpos dimension mismatch: expected ${profile.qposDimension}, got ${String(backendQposDimension ?? "missing")}`,
      currentFrameIndex: payload.frame_index,
      currentTimestampS: payload.time_s,
      sourceLabel: "transport payload incompatible",
    };
  }
  if (
    !Array.isArray(backendJointNames) ||
    backendJointNames.length !== profile.jointNames.length ||
    backendJointNames.some((name, index) => name !== profile.jointNames[index])
  ) {
    return {
      status: "invalid",
      qpos: null,
      errorMessage: "backend/viewer joint name/order mismatch",
      currentFrameIndex: payload.frame_index,
      currentTimestampS: payload.time_s,
      sourceLabel: "transport payload incompatible",
    };
  }

  if (profile.sceneStateLayout && Object.prototype.hasOwnProperty.call(payload.metadata,"contact_scene_robot_qpos_v1")) {
    return {status:"invalid",qpos:null,errorMessage:"legacy Robot-only projection cannot drive a full scene",currentFrameIndex:payload.frame_index,currentTimestampS:payload.time_s,sourceLabel:"transport payload incompatible"};
  }
  if (Object.prototype.hasOwnProperty.call(payload.metadata, "contact_scene_robot_qpos_v1")) {
    try {
      return {
        status: "ready",
        qpos: resolveContactSceneRobotQpos(payload, profile),
        errorMessage: null,
        currentFrameIndex: payload.frame_index,
        currentTimestampS: payload.time_s,
        sourceLabel: "transport payloadのcontact-scene Robot qpos投影",
      };
    } catch (error) {
      return {
        status: "invalid",
        qpos: null,
        errorMessage: error instanceof Error ? error.message : "contact-scene qpos mapping is invalid",
        currentFrameIndex: payload.frame_index,
        currentTimestampS: payload.time_s,
        sourceLabel: "transport payload incompatible",
      };
    }
  }

  if (payload.qpos.length !== modelNq) {
    return {
      status: "invalid",
      qpos: null,
      errorMessage: `transport qpos length mismatch: expected ${modelNq}, got ${payload.qpos.length}`,
      currentFrameIndex: payload.frame_index,
      currentTimestampS: payload.time_s,
      sourceLabel: "transport payload invalid",
    };
  }

  return {
    status: "ready",
    qpos: ensureQposLength(payload.qpos, modelNq, "transport qpos"),
    errorMessage: null,
    currentFrameIndex: payload.frame_index,
    currentTimestampS: payload.time_s,
    sourceLabel: "transport payload",
  };
}
