import { decodeSceneStateLayout } from "./sceneStateLayout.js";
import type {
  ViewerAxisVisualStyle,
  ViewerBodyVisualStyle,
  ViewerRobotProfile,
} from "./types.js";
import type { TransportPayloadV0 } from "../types/transportPayload.js";

export const VIEWER_ROBOT_DECLARATION_SCHEMA_VERSION = "viewer-robot-declaration/v1" as const;

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(value: unknown, name: string): JsonRecord {
  if (!isRecord(value)) {
    throw new Error(`${name} must be an object`);
  }
  return value;
}

function requireExactKeys(value: JsonRecord, expected: readonly string[], name: string): void {
  const actual = Object.keys(value).sort();
  const wanted = Array.from(expected).sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    throw new Error(`${name} keys mismatch: expected ${wanted.join(",")}, got ${actual.join(",")}`);
  }
}

function requireString(value: unknown, name: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${name} must be a non-empty string`);
  }
  return value;
}

function requirePositiveInteger(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) {
    throw new Error(`${name} must be a positive integer`);
  }
  return value;
}

function requireLocalUrl(value: unknown, name: string): string {
  const url = requireString(value, name);
  if (!url.startsWith("/") || url.startsWith("//") || url.split("/").includes("..")) {
    throw new Error(`${name} must be a local absolute-path URL`);
  }
  return url;
}

function requireResourcePath(value: unknown, name: string): string {
  const path = requireString(value, name);
  if (path.startsWith("/") || path.includes("\\") || path.split("/").includes("..")) {
    throw new Error(`${name} must be a repository-relative POSIX path`);
  }
  return path;
}

export function repositoryResourcePublicUrl(resourcePath: string): string {
  const path = requireResourcePath(resourcePath, "repository resource path");
  const parts = path.split("/");
  if (
    parts[0] !== "assets" ||
    parts.length < 2 ||
    parts.some((part) => !/^[A-Za-z0-9._~-]+$/.test(part))
  ) {
    throw new Error("viewer repository resource path must be below the assets root");
  }
  return `/${parts.slice(1).map(encodeURIComponent).join("/")}`;
}

function requireResourceUrlPair(
  resourcePathValue: unknown,
  urlValue: unknown,
  name: string,
): { resourcePath: string; url: string } {
  const resourcePath = requireResourcePath(resourcePathValue, `${name} resource path`);
  const url = requireLocalUrl(urlValue, `${name} URL`);
  const expectedUrl = repositoryResourcePublicUrl(resourcePath);
  if (url !== expectedUrl) {
    throw new Error(
      `${name} resource path/URL mismatch: expected ${expectedUrl}, got ${url}`,
    );
  }
  return { resourcePath, url };
}

function requireArray(value: unknown, name: string): readonly unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${name} must be an array`);
  }
  return value;
}

function requireUnique(values: readonly string[], name: string): void {
  if (new Set(values).size !== values.length) {
    throw new Error(`${name} must be unique`);
  }
}

const ROOT_KEYS = [
  "schemaVersion",
  "profileId",
  "profileContractVersion",
  "modelContractVersion",
  "modelUrl",
  "modelResourcePath",
  "initialKeyframeName",
  "initialPoseSourceLabel",
  "fixtureUrl",
  "fixtureResourcePath",
  "vfsAssets",
  "visualStyleSelection",
  "bodyVisualStyles",
  "axisVisualStyles",
  "jointNames",
  "qposDimension",
] as const;

export function decodeViewerRobotDeclaration(value: unknown): ViewerRobotProfile {
  const root = requireRecord(value, "viewer robot declaration");
  requireExactKeys(root, root.schemaVersion==="viewer-robot-declaration/v2" ? [...ROOT_KEYS,"sceneStateLayout"] : ROOT_KEYS, "viewer robot declaration");
  const schemaVersion = requireString(root.schemaVersion, "schemaVersion");
  if (schemaVersion !== VIEWER_ROBOT_DECLARATION_SCHEMA_VERSION && schemaVersion!=="viewer-robot-declaration/v2") {
    throw new Error(`unsupported viewer robot declaration schema version: ${schemaVersion}`);
  }

  const vfsAssets = new Map<string, string>();
  const vfsResourcePaths = new Map<string, string>();
  for (const [index, raw] of requireArray(root.vfsAssets, "vfsAssets").entries()) {
    const item = requireRecord(raw, `vfsAssets[${index}]`);
    requireExactKeys(item, ["vfsPath", "resourcePath", "url"], `vfsAssets[${index}]`);
    const vfsPath = requireString(item.vfsPath, `vfsAssets[${index}].vfsPath`);
    if (vfsPath.startsWith("/") || vfsPath.split("/").includes("..")) {
      throw new Error(`vfsAssets[${index}].vfsPath must not escape the virtual root`);
    }
    if (vfsAssets.has(vfsPath)) {
      throw new Error(`duplicate viewer VFS path: ${vfsPath}`);
    }
    const resource = requireResourceUrlPair(
      item.resourcePath,
      item.url,
      `vfsAssets[${index}]`,
    );
    vfsResourcePaths.set(vfsPath, resource.resourcePath);
    vfsAssets.set(vfsPath, resource.url);
  }

  const bodyVisualStyles: Record<string, ViewerBodyVisualStyle> = {};
  for (const [index, raw] of requireArray(root.bodyVisualStyles, "bodyVisualStyles").entries()) {
    const item = requireRecord(raw, `bodyVisualStyles[${index}]`);
    requireExactKeys(item, ["key", "color", "label", "detail"], `bodyVisualStyles[${index}]`);
    const key = requireString(item.key, `bodyVisualStyles[${index}].key`);
    if (bodyVisualStyles[key] !== undefined) {
      throw new Error(`duplicate viewer body style key: ${key}`);
    }
    bodyVisualStyles[key] = Object.freeze({
      color: requireString(item.color, `bodyVisualStyles[${index}].color`),
      label: requireString(item.label, `bodyVisualStyles[${index}].label`),
      detail: requireString(item.detail, `bodyVisualStyles[${index}].detail`),
    });
  }

  const visualStyleSelection = new Map<string, string>();
  for (const [index, raw] of requireArray(root.visualStyleSelection, "visualStyleSelection").entries()) {
    const item = requireRecord(raw, `visualStyleSelection[${index}]`);
    requireExactKeys(item, ["match", "styleKey"], `visualStyleSelection[${index}]`);
    const match = requireString(item.match, `visualStyleSelection[${index}].match`);
    const styleKey = requireString(item.styleKey, `visualStyleSelection[${index}].styleKey`);
    if (visualStyleSelection.has(match)) {
      throw new Error(`duplicate viewer visual style match: ${match}`);
    }
    if (bodyVisualStyles[styleKey] === undefined) {
      throw new Error(`viewer visual style selection references unknown style key: ${styleKey}`);
    }
    visualStyleSelection.set(match, styleKey);
  }

  const axisVisualStyles: ViewerAxisVisualStyle[] = requireArray(
    root.axisVisualStyles,
    "axisVisualStyles",
  ).map((raw, index) => {
    const item = requireRecord(raw, `axisVisualStyles[${index}]`);
    requireExactKeys(item, ["color", "label", "detail"], `axisVisualStyles[${index}]`);
    return Object.freeze({
      color: requireString(item.color, `axisVisualStyles[${index}].color`),
      label: requireString(item.label, `axisVisualStyles[${index}].label`),
      detail: requireString(item.detail, `axisVisualStyles[${index}].detail`),
    });
  });

  const jointNames = requireArray(root.jointNames, "jointNames").map((value, index) =>
    requireString(value, `jointNames[${index}]`),
  );
  if (jointNames.length === 0) {
    throw new Error("jointNames must not be empty");
  }
  requireUnique(jointNames, "jointNames");

  const model = requireResourceUrlPair(root.modelResourcePath, root.modelUrl, "model");
  const fixture = requireResourceUrlPair(
    root.fixtureResourcePath,
    root.fixtureUrl,
    "fixture",
  );

  return Object.freeze({
    schemaVersion,
    ...(schemaVersion==="viewer-robot-declaration/v2" ? {sceneStateLayout:decodeSceneStateLayout(root.sceneStateLayout,jointNames)} : {}),
    profileId: requireString(root.profileId, "profileId"),
    profileContractVersion: requirePositiveInteger(root.profileContractVersion, "profileContractVersion"),
    modelContractVersion: requireString(root.modelContractVersion, "modelContractVersion"),
    modelUrl: model.url,
    modelResourcePath: model.resourcePath,
    initialKeyframeName: requireString(root.initialKeyframeName, "initialKeyframeName"),
    initialPoseSourceLabel: requireString(root.initialPoseSourceLabel, "initialPoseSourceLabel"),
    fixtureUrl: fixture.url,
    fixtureResourcePath: fixture.resourcePath,
    vfsAssets,
    vfsResourcePaths,
    visualStyleSelection,
    bodyVisualStyles: Object.freeze(bodyVisualStyles),
    axisVisualStyles: Object.freeze(axisVisualStyles),
    jointNames: Object.freeze(jointNames),
    qposDimension: requirePositiveInteger(root.qposDimension, "qposDimension"),
  });
}

export interface ViewerRobotDeclarationReference {
  readonly resourcePath: string;
  readonly url: string;
  readonly digest: string;
}

export function viewerRobotDeclarationReferenceFromPayload(
  payload: TransportPayloadV0,
): ViewerRobotDeclarationReference | null {
  const metadata = payload.metadata;
  const values = [
    metadata.viewer_robot_declaration_resource_path,
    metadata.viewer_robot_declaration_url,
    metadata.viewer_robot_declaration_digest,
  ];
  if (values.every((value) => value === undefined)) {
    return null;
  }
  if (values.some((value) => value === undefined)) {
    throw new Error("viewer declaration frame reference must be complete");
  }
  const resource = requireResourceUrlPair(
    metadata.viewer_robot_declaration_resource_path,
    metadata.viewer_robot_declaration_url,
    "viewer declaration",
  );
  const digest = requireString(
    metadata.viewer_robot_declaration_digest,
    "viewer declaration digest",
  );
  if (!/^sha256:[0-9a-f]{64}$/.test(digest)) {
    throw new Error("viewer declaration digest must be a canonical sha256 digest");
  }
  return Object.freeze({
    resourcePath: resource.resourcePath,
    url: resource.url,
    digest,
  });
}

export function validateViewerRobotProfileCompatibility(
  payload: TransportPayloadV0,
  profile: ViewerRobotProfile,
): void {
  const metadata = payload.metadata;
  if (metadata.robot_profile_id !== profile.profileId) {
    throw new Error("backend/viewer declaration robot profile mismatch");
  }
  if (metadata.model_contract_version !== profile.modelContractVersion) {
    throw new Error("backend/viewer declaration model contract mismatch");
  }
  if (profile.sceneStateLayout && canonicalJson(metadata.scene_state_layout_v1)!==canonicalJson(profile.sceneStateLayout)) {
    throw new Error("backend/viewer scene layout mismatch");
  }
  if (metadata.robot_qpos_dimension !== profile.qposDimension) {
    throw new Error("backend/viewer declaration qpos dimension mismatch");
  }
  if (
    !Array.isArray(metadata.robot_joint_names) ||
    metadata.robot_joint_names.length !== profile.jointNames.length ||
    metadata.robot_joint_names.some((name, index) => name !== profile.jointNames[index])
  ) {
    throw new Error("backend/viewer declaration joint name/order mismatch");
  }
}

function viewerRobotProfileDocument(profile: ViewerRobotProfile): JsonRecord {
  return {
    schemaVersion: profile.schemaVersion,
    ...(profile.sceneStateLayout ? {sceneStateLayout:profile.sceneStateLayout} : {}),
    profileId: profile.profileId,
    profileContractVersion: profile.profileContractVersion,
    modelContractVersion: profile.modelContractVersion,
    modelUrl: profile.modelUrl,
    modelResourcePath: profile.modelResourcePath,
    initialKeyframeName: profile.initialKeyframeName,
    initialPoseSourceLabel: profile.initialPoseSourceLabel,
    fixtureUrl: profile.fixtureUrl,
    fixtureResourcePath: profile.fixtureResourcePath,
    vfsAssets: Array.from(profile.vfsAssets.entries()).map(([vfsPath, url]) => ({
      vfsPath,
      resourcePath: profile.vfsResourcePaths.get(vfsPath),
      url,
    })),
    visualStyleSelection: Array.from(profile.visualStyleSelection.entries()).map(
      ([match, styleKey]) => ({ match, styleKey }),
    ),
    bodyVisualStyles: Object.entries(profile.bodyVisualStyles).map(([key, style]) => ({
      key,
      ...style,
    })),
    axisVisualStyles: profile.axisVisualStyles,
    jointNames: profile.jointNames,
    qposDimension: profile.qposDimension,
  };
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  const record = value as JsonRecord;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(",")}}`;
}

export function viewerRobotProfileCanonicalJson(profile: ViewerRobotProfile): string {
  return canonicalJson(viewerRobotProfileDocument(profile));
}

export async function viewerRobotProfileDigest(
  profile: ViewerRobotProfile,
): Promise<string> {
  const bytes = new TextEncoder().encode(viewerRobotProfileCanonicalJson(profile));
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return `sha256:${Array.from(new Uint8Array(digest), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("")}`;
}

export async function loadViewerRobotProfileFromPayload(
  payload: TransportPayloadV0,
  fetcher: typeof fetch = fetch,
): Promise<{
  readonly profile: ViewerRobotProfile;
  readonly reference: ViewerRobotDeclarationReference;
}> {
  const reference = viewerRobotDeclarationReferenceFromPayload(payload);
  if (reference === null) {
    throw new Error("viewer declaration frame reference is required before state");
  }
  const profile = await loadViewerRobotDeclaration(reference.url, fetcher);
  const actualDigest = await viewerRobotProfileDigest(profile);
  if (actualDigest !== reference.digest) {
    throw new Error(
      `viewer declaration digest mismatch: expected ${reference.digest}, got ${actualDigest}`,
    );
  }
  validateViewerRobotProfileCompatibility(payload, profile);
  return Object.freeze({ profile, reference });
}

export function validateViewerRobotProfileFrameReference(
  payload: TransportPayloadV0,
  expected: ViewerRobotDeclarationReference,
  profile: ViewerRobotProfile,
): void {
  const actual = viewerRobotDeclarationReferenceFromPayload(payload);
  if (actual === null) {
    throw new Error("viewer declaration frame reference disappeared during the session");
  }
  if (
    actual.resourcePath !== expected.resourcePath ||
    actual.url !== expected.url ||
    actual.digest !== expected.digest
  ) {
    throw new Error("viewer declaration frame reference changed during the session");
  }
  validateViewerRobotProfileCompatibility(payload, profile);
}

export async function loadViewerRobotDeclaration(
  url: string,
  fetcher: typeof fetch = fetch,
): Promise<ViewerRobotProfile> {
  requireLocalUrl(url, "viewer declaration URL");
  const response = await fetcher(url);
  if (!response.ok) {
    throw new Error(`failed to fetch ${url}: ${response.status} ${response.statusText}`);
  }
  return decodeViewerRobotDeclaration(await response.json());
}
