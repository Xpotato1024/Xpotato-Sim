/**
 * MuJoCo/WASM sceneをThree.jsへ描画投影するrenderer。
 * Python/MuJoCo physical stateをSoTとし、viewer-side FK/IKや独立physics stepを行わない。
 */
import {
  AmbientLight,
  ArrowHelper,
  AxesHelper,
  BoxGeometry,
  BufferGeometry,
  Color,
  CylinderGeometry,
  DoubleSide,
  DirectionalLight,
  Float32BufferAttribute,
  Group,
  HemisphereLight,
  CanvasTexture,
  Mesh,
  MeshPhongMaterial,
  PerspectiveCamera,
  PlaneGeometry,
  Scene,
  SphereGeometry,
  SRGBColorSpace,
  Vector3 as ThreeVector3,
  NearestFilter,
  RepeatWrapping,
  Uint32BufferAttribute,
  WebGLRenderer,
} from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { TransportPayloadV0 } from "../types/transportPayload.js";
import {
  parseContactTaskPresentationV1,
  unavailableContactTaskPresentation,
  type ContactTaskInputSource,
  type ContactTaskPresentationV1,
} from "../contact/contactTaskLog.js";
import { SceneContactStream, unavailableSceneContact, type SceneContactPresentation } from "../contact/sceneContactPresentation.js";
import { SceneContactOverlay } from "./sceneContactOverlay.js";
import type { ViewerRobotProfile } from "../robot-profiles/types.js";
import {
  loadViewerRobotProfileFromPayload,
  validateViewerRobotProfileCompatibility,
  validateViewerRobotProfileFrameReference,
  viewerRobotDeclarationReferenceFromPayload,
  viewerRobotProfileDigest,
  type ViewerRobotDeclarationReference,
} from "../robot-profiles/declaration.js";
import {
  createViewerWebSocketClient,
  type ViewerWebSocketClient,
  type ViewerWebSocketPayloadObservation,
} from "../transport/websocketClient.js";
import { loadMujocoWasm } from "./mujocoWasmLoader.js";
import { matrixFromMujocoGeom } from "./mujocoSceneTransforms.js";
import {
  formatQpos,
  resolveNamedInitialKeyframe,
  resolveTransportQpos,
} from "./mujocoQposSync.js";
import { resolveBodyVisualStyle, resolveGeomDisplayColor } from "./visualStyles.js";
import type { BodyVisualStyle } from "./visualStyles.js";
import {
  applyProductViewerRendererStatePatch,
  createInitialProductViewerState,
  buildProductViewerInputOverlayState,
  formatViewerStatusText,
  type ProductViewerConnectionStatus,
  type ProductViewerRendererStatePatch,
  type ProductViewerState,
} from "./productViewerState.js";
import {
  createViewerFrameTiming,
  type ViewerPayloadCandidate,
} from "./viewerFrameTiming.js";

import { decodeJointDisplayLayout } from "./jointPresentation.js";
import { cameraPresentation, type CameraView } from "./cameraPresentation.js";

export interface MujocoSceneRendererOptions {
  canvas: HTMLCanvasElement;
  profile: ViewerRobotProfile | null;
  expectedProfileId?: string | null;
  websocketUrl?: string | null;
  initialCameraView?: CameraView;
  onStateChange: (state: ProductViewerState) => void;
  onProfileResolved?: (profile: ViewerRobotProfile) => void;
  onError?: (error: Error) => void;
}

/** render resource lifecycle。dispose後はcanvas/scene resourceを再利用しない。 */
export interface MujocoSceneRenderer {
  applyOfflinePayload(payload: TransportPayloadV0): void;
  setCameraView(view: CameraView | "fit"): void;
  start(): Promise<void>;
  setContactTaskPresentation(
    presentation: ContactTaskPresentationV1,
    inputSource: ContactTaskInputSource,
  ): void;
  dispose(): void;
}

const DEFAULT_WIDTH = 1280;
const DEFAULT_HEIGHT = 800;
const MAX_GEOMS = 2 ** 15;
function createCheckerFloorTexture(): CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 512;
  const context = canvas.getContext("2d");
  if (context === null) {
    return new CanvasTexture(canvas);
  }

  const tiles = 4;
  const tileSize = canvas.width / tiles;
  context.fillStyle = "#303d43";
  context.fillRect(0, 0, canvas.width, canvas.height);
  for (let y = 0; y < tiles; y += 1) {
    for (let x = 0; x < tiles; x += 1) {
      if ((x + y) % 2 === 0) {
        context.fillStyle = "#27343a";
      } else {
        context.fillStyle = "#303d43";
      }
      context.fillRect(x * tileSize, y * tileSize, tileSize, tileSize);
    }
  }

  const texture = new CanvasTexture(canvas);
  texture.wrapS = RepeatWrapping;
  texture.wrapT = RepeatWrapping;
  texture.repeat.set(4.0, 4.0);
  texture.magFilter = NearestFilter;
  texture.minFilter = NearestFilter;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  return texture;
}

function buildPrimitiveGeometry(type: number, size: ArrayLike<number>): BufferGeometry {
  if (type === 0) {
    return new PlaneGeometry(20, 20);
  }
  if (type === 2) {
    return new SphereGeometry(Number(size[0] ?? 1));
  }
  if (type === 3) {
    const radius = Number(size[0] ?? 1);
    const length = 2 * Number(size[2] ?? 1);
    const geom = new CylinderGeometry(radius, radius, length, 32);
    geom.rotateX(Math.PI / 2);
    return geom;
  }
  if (type === 4) {
    const geom = new SphereGeometry(1);
    geom.scale(Number(size[0] ?? 1), Number(size[1] ?? 1), Number(size[2] ?? 1));
    return geom;
  }
  if (type === 5) {
    const radiusTop = Number(size[0] ?? 1);
    const radiusBottom = Number(size[1] ?? 1);
    const length = 2 * Number(size[2] ?? 1);
    const geom = new CylinderGeometry(radiusTop, radiusBottom, length, 32);
    geom.rotateX(Math.PI / 2);
    return geom;
  }
  if (type === 6) {
    return new BoxGeometry(2 * Number(size[0] ?? 1), 2 * Number(size[1] ?? 1), 2 * Number(size[2] ?? 1));
  }
  return new BufferGeometry();
}

/** 検証済みmodel/profileからprojection rendererを構築し、Robot fallbackを行わない。 */
export function createMujocoSceneRenderer(options: MujocoSceneRendererOptions): MujocoSceneRenderer {
  const frameTiming = createViewerFrameTiming();
  let profile = options.profile;
  let declarationReference: ViewerRobotDeclarationReference | null = null;
  const state = createInitialProductViewerState(profile ?? undefined);
  const requireProfile = (): ViewerRobotProfile => {
    if (profile === null) {
      throw new Error("viewer robot declaration is unavailable");
    }
    return profile;
  };
  const emitState = (next: ProductViewerState): void => {
    frameTiming.recordUiStateUpdate();
    next.viewerTiming = frameTiming.snapshot();
    Object.assign(state, next);
    options.onStateChange({ ...state });
  };

  const scene = new Scene();
  scene.background = new Color("#08111f");
  const contactOverlay = new Group();
  contactOverlay.name = "read-only contact-task overlay";
  scene.add(contactOverlay);
  const geometryOverlay = new SceneContactOverlay();
  const geometryStream = new SceneContactStream();
  scene.add(geometryOverlay.group);

  const renderer = new WebGLRenderer({ canvas: options.canvas, antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setSize(DEFAULT_WIDTH, DEFAULT_HEIGHT, false);
  renderer.outputColorSpace = SRGBColorSpace;

  const camera = new PerspectiveCamera(45, DEFAULT_WIDTH / DEFAULT_HEIGHT, 0.01, 100);
  camera.position.set(1.8, -1.8, 1.5);
  camera.up.set(0, 0, 1);

  const controls = new OrbitControls(camera, options.canvas);
  controls.target.set(0.15, 0, 0.2);
  controls.update();

  scene.add(new AmbientLight(0xffffff, 1.0));
  scene.add(new HemisphereLight(0xbfd7ff, 0x1e293b, 0.8));
  const axesHelper = new AxesHelper(0.5);
  axesHelper.position.set(0, 0, 0);
  scene.add(axesHelper);

  const keyLight = new DirectionalLight(0xffffff, 1.8);
  keyLight.position.set(2.5, -2.5, 4.0);
  scene.add(keyLight);

  const fillLight = new DirectionalLight(0xdbeafe, 0.6);
  fillLight.position.set(-2.0, 1.5, 2.0);
  scene.add(fillLight);

  const meshGeometryCache = new Map<number, BufferGeometry>();
  const objectByGeomIndex = new Map<number, Mesh>();
  const materialByKey = new Map<string, MeshPhongMaterial>();
  const floorTexture = createCheckerFloorTexture();
  const floorMaterial = new MeshPhongMaterial({
    color: new Color("#d4d4d8"),
    map: floorTexture,
    transparent: false,
    opacity: 1,
    side: DoubleSide,
    shininess: 0,
    specular: new Color("#111827"),
  });
  const modelMeshNameById = new Map<number, string>();

  let mujocoApi: any;
  let model: any;
  let data: any;
  let mjvScene: any;
  let mjvOption: any;
  let mjvPerturb: any;
  let mjvCamera: any;
  let websocketClient: ViewerWebSocketClient | null = null;
  let startupQpos: number[] = [];
  let hasLoaded = false;
  let disposed = false;
  let frameHandle: number | null = null;

  const websocketUrl =
    options.websocketUrl === undefined || options.websocketUrl === null || options.websocketUrl.trim() === ""
      ? null
      : options.websocketUrl;

  const updateRendererStatus = (patch: ProductViewerRendererStatePatch): void => {
    emitState(applyProductViewerRendererStatePatch(state, patch));
  };

  const setSceneContactPresentation = (value: SceneContactPresentation): void => {
    geometryOverlay.update(value);
    updateRendererStatus({sceneContactPresentation: value});
  };

  const clearContactOverlay = (): void => {
    contactOverlay.traverse((object) => {
      const renderObject = object as typeof object & {
        geometry?: { dispose?: () => void };
        material?: { dispose?: () => void } | Array<{ dispose?: () => void }>;
      };
      renderObject.geometry?.dispose?.();
      const materials = Array.isArray(renderObject.material)
        ? renderObject.material
        : renderObject.material === undefined
          ? []
          : [renderObject.material];
      for (const material of materials) {
        material.dispose?.();
      }
    });
    contactOverlay.clear();
  };

  const updateContactOverlay = (presentation: ContactTaskPresentationV1): void => {
    clearContactOverlay();
    if (presentation.status !== "available" || presentation.cube === null) {
      return;
    }

    const cube = presentation.cube;
    const cubeMesh = new Mesh(
      new BoxGeometry(
        cube.halfSizeM[0] * 2,
        cube.halfSizeM[1] * 2,
        cube.halfSizeM[2] * 2,
      ),
      new MeshPhongMaterial({
        color: new Color().setRGB(cube.rgba[0], cube.rgba[1], cube.rgba[2]),
        transparent: cube.rgba[3] < 1,
        opacity: cube.rgba[3],
        depthWrite: cube.rgba[3] >= 1,
        side: DoubleSide,
        shininess: 14,
      }),
    );
    cubeMesh.name = `contact overlay: ${cube.identity.name}/v${cube.identity.version}`;
    cubeMesh.position.set(...cube.positionWorldM);
    cubeMesh.quaternion.set(
      cube.orientationWXYZ[1],
      cube.orientationWXYZ[2],
      cube.orientationWXYZ[3],
      cube.orientationWXYZ[0],
    );
    contactOverlay.add(cubeMesh);

    const arrow = (vector: readonly number[], origin: readonly number[], color: number): void => {
      const direction = new ThreeVector3(vector[0], vector[1], vector[2]);
      const magnitude = direction.length();
      if (!Number.isFinite(magnitude) || magnitude < 1e-8) {
        return;
      }
      direction.normalize();
      const length = Math.min(0.22, Math.max(0.035, Math.log1p(magnitude) * 0.06));
      contactOverlay.add(
        new ArrowHelper(
          direction,
          new ThreeVector3(origin[0], origin[1], origin[2]),
          length,
          color,
          Math.min(0.03, length * 0.28),
          Math.min(0.018, length * 0.16),
        ),
      );
    };
    for (const contact of presentation.contacts) {
      const point = new ThreeVector3(...contact.pointWorldM);
      const radius = Math.max(0.002, Math.min(...cube.halfSizeM) * 0.12);
      const marker = new Mesh(
        new SphereGeometry(radius, 12, 8),
        new MeshPhongMaterial({ color: new Color("#f97316"), shininess: 24 }),
      );
      marker.name = `contact point: ${contact.contactIdentity}`;
      marker.position.set(point.x, point.y, point.z);
      contactOverlay.add(marker);
      arrow(contact.normalWorld, contact.pointWorldM, 0xf97316);
    }

    const forceOrigin = presentation.contacts[0]?.pointWorldM ?? cube.positionWorldM;
    if (presentation.rawEvidence?.forceWorldN !== null && presentation.rawEvidence?.forceWorldN !== undefined) {
      arrow(presentation.rawEvidence.forceWorldN, forceOrigin, 0x22d3ee);
    }
    if (
      presentation.derivedForce?.frame === "mujoco_world" &&
      presentation.derivedForce.forceN !== null
    ) {
      arrow(presentation.derivedForce.forceN, forceOrigin, 0xfacc15);
    }
  };

  const setContactTaskPresentation = (
    presentation: ContactTaskPresentationV1,
    inputSource: ContactTaskInputSource,
  ): void => {
    updateContactOverlay(presentation);
    updateRendererStatus({
      contactTaskPresentation: presentation,
      contactTaskInputSource: inputSource,
    });
  };

  const updateConnectionStatus = (
    connectionStatus: ProductViewerConnectionStatus,
    patch: ProductViewerRendererStatePatch = {},
  ): void => {
    const next = applyProductViewerRendererStatePatch(state, patch);
    next.connectionStatus = connectionStatus;
    if (connectionStatus !== "open" && websocketUrl !== null && next.sceneContactPresentation.status !== "absent") {
      const value = unavailableSceneContact("接続が有効ではないため接触表示を消去しました");
      geometryOverlay.update(value);
      next.sceneContactPresentation = value;
    }
    emitState(next);
  };

  const buildCompiledMeshGeometry = (meshId: number): BufferGeometry => {
    const cached = meshGeometryCache.get(meshId);
    if (cached !== undefined) {
      return cached;
    }

    const vertexStart = model.mesh_vertadr[meshId];
    const vertexCount = model.mesh_vertnum[meshId];
    const faceStart = model.mesh_faceadr[meshId];
    const faceCount = model.mesh_facenum[meshId];
    if (vertexStart === undefined || vertexCount === undefined || faceStart === undefined || faceCount === undefined) {
      throw new Error(`missing compiled mesh data for mesh ${meshId}`);
    }

    const positionAttribute = new Float32Array(vertexCount * 3);
    for (let vertexIndex = 0; vertexIndex < vertexCount; vertexIndex += 1) {
      const sourceIndex = (vertexStart + vertexIndex) * 3;
      const targetIndex = vertexIndex * 3;
      positionAttribute[targetIndex] = Number(model.mesh_vert[sourceIndex] ?? 0);
      positionAttribute[targetIndex + 1] = Number(model.mesh_vert[sourceIndex + 1] ?? 0);
      positionAttribute[targetIndex + 2] = Number(model.mesh_vert[sourceIndex + 2] ?? 0);
    }

    const indexAttribute = new Uint32Array(faceCount * 3);
    for (let faceIndex = 0; faceIndex < faceCount; faceIndex += 1) {
      const sourceIndex = (faceStart + faceIndex) * 3;
      const targetIndex = faceIndex * 3;
      indexAttribute[targetIndex] = Number(model.mesh_face[sourceIndex] ?? 0);
      indexAttribute[targetIndex + 1] = Number(model.mesh_face[sourceIndex + 1] ?? 0);
      indexAttribute[targetIndex + 2] = Number(model.mesh_face[sourceIndex + 2] ?? 0);
    }

    const geometry = new BufferGeometry();
    geometry.setAttribute("position", new Float32BufferAttribute(positionAttribute, 3));
    geometry.setIndex(new Uint32BufferAttribute(indexAttribute, 1));
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
    meshGeometryCache.set(meshId, geometry);
    return geometry;
  };

  const getMaterialForGeom = (geom: any, sourceGeom: any | null = null): MeshPhongMaterial => {
    const sourceBodyId = sourceGeom === null ? Number.NaN : Number(sourceGeom.bodyid);
    const bodyId = Number.isFinite(sourceBodyId) && sourceBodyId >= 0 ? sourceBodyId : Number(geom.bodyid);
    const bodyName =
      Number.isFinite(bodyId) && bodyId >= 0
        ? String(mujocoApi.mj_id2name(model, mujocoApi.mjtObj.mjOBJ_BODY.value, bodyId) ?? "")
        : "";
    const geomName = geom.name === undefined ? "" : String(geom.name);
    const sourceMeshId = sourceGeom === null ? Number.NaN : Number(sourceGeom.dataid);
    const meshId = Number.isFinite(sourceMeshId) && sourceMeshId >= 0 ? sourceMeshId : Number(geom.dataid);
    const meshName = meshId >= 0 && meshId < model.nmesh ? String(model.mesh(meshId).name ?? "") : "";
    const cacheKey = `${bodyName}:${geomName}:${meshName}:${geom.type}`;
    const cached = materialByKey.get(cacheKey);
    if (cached !== undefined) {
      return cached as MeshPhongMaterial;
    }

    if (geomName === "floor" || geom.type === mujocoApi.mjtGeom.mjGEOM_PLANE.value) {
      return floorMaterial;
    }

    const style: BodyVisualStyle | null = resolveBodyVisualStyle(
      requireProfile(),
      bodyName,
      meshName,
      geomName,
    );
    const materialColor = resolveGeomDisplayColor(style, Array.from(geom.rgba), geom.type === mujocoApi.mjtGeom.mjGEOM_MESH.value);
    const material = new MeshPhongMaterial({
      color: typeof materialColor === "string" ? new Color(materialColor) : new Color().setRGB(...materialColor),
      transparent: geom.rgba[3] < 1,
      opacity: geom.rgba[3],
      side: DoubleSide,
      shininess: 18,
      specular: new Color("#111827"),
    });
    materialByKey.set(cacheKey, material);
    return material;
  };

  const syncSceneFromCurrentData = (): void => {
    mujocoApi.mjv_updateScene(
      model,
      data,
      mjvOption,
      mjvPerturb,
      mjvCamera,
      mujocoApi.mjtCatBit.mjCAT_ALL.value,
      mjvScene,
    );

    const geoms = mjvScene.geoms;
    for (let geomIndex = 0; geomIndex < geoms.size(); geomIndex += 1) {
      const geom = geoms.get(geomIndex);
      let mesh = objectByGeomIndex.get(geomIndex);
      if (mesh === undefined) {
        let sourceGeom: any | null = null;
        let geometry: BufferGeometry;
        if (geom.type === mujocoApi.mjtGeom.mjGEOM_MESH.value) {
          sourceGeom = geom.objtype === mujocoApi.mjtObj.mjOBJ_GEOM.value ? model.geom(geom.objid) : null;
          const meshId = sourceGeom === null ? geom.dataid : sourceGeom.dataid;
          geometry = buildCompiledMeshGeometry(meshId);
        } else {
          geometry = buildPrimitiveGeometry(geom.type, geom.size);
        }

        mesh = new Mesh(geometry, getMaterialForGeom(geom, sourceGeom));
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        objectByGeomIndex.set(geomIndex, mesh);
        scene.add(mesh);
      }

      mesh.matrixAutoUpdate = false;
      mesh.matrix.copy(matrixFromMujocoGeom(geom));
      mesh.matrixWorldNeedsUpdate = true;
      geom.delete();
    }
    geoms.delete();
  };

  const applyModelPose = (
    qpos: readonly number[],
    sourceLabel: string,
    frameIndex: number | null,
    timeS: number | null,
    endpointEvaluation: TransportPayloadV0["endpoint_evaluation"] | null,
    inputOverlay: ProductViewerState["inputOverlay"],
    candidate: ViewerPayloadCandidate | null = null,
  ): void => {
    const sceneApplyStartedMs = frameTiming.now();
    data.qpos.set(qpos);
    mujocoApi.mj_forward(model, data);
    syncSceneFromCurrentData();
    if (candidate !== null) {
      frameTiming.recordSceneApplied(candidate, frameTiming.now() - sceneApplyStartedMs);
    }
    updateRendererStatus({
      status: "ready",
      sourceLabel,
      qposStatus: "ready",
      qposError: null,
      currentFrameIndex: frameIndex,
      currentTimestampS: timeS,
      currentQpos: Array.from(qpos),
      endpointEvaluation,
      inputOverlay,
      modelNq: model.nq,
      modelNv: model.nv,
      modelNgeom: model.ngeom,
      modelNmesh: model.nmesh,
    });
  };

  let startupPoseSourceLabel = profile?.initialPoseSourceLabel ?? "viewer declaration pending";
  const applyStartupPose = (): void => {
    applyModelPose(startupQpos, startupPoseSourceLabel, null, null, null, null);
  };

  const applyTransportPayload = (candidate: ViewerPayloadCandidate): void => {
    const payload = candidate.payload;
    const endpointEvaluation = payload.endpoint_evaluation ?? null;
    const inputOverlay = buildProductViewerInputOverlayState(payload);
    const activeProfile = requireProfile();
    const contactPresentation = parseContactTaskPresentationV1(
      payload.metadata.contact_task_v1,
      {
        profileId: activeProfile.profileId,
        profileContractVersion: activeProfile.profileContractVersion,
      },
      { timeS: payload.time_s, frameIndex: payload.frame_index },
    );
    const qposResolution = resolveTransportQpos(
      payload,
      model.nq,
      activeProfile,
    );
    if (qposResolution.status !== "ready" || qposResolution.qpos === null) {
      setSceneContactPresentation(unavailableSceneContact("qposとsceneが一致しないため接触表示を消去しました"));
      if (contactPresentation.status !== "stale") {
        setContactTaskPresentation(
          unavailableContactTaskPresentation(
            qposResolution.errorMessage ?? "payload qpos is incompatible; contact overlay was cleared",
          ),
          "transport_metadata",
        );
      }
      updateRendererStatus({
        status: "warning",
        sourceLabel: qposResolution.sourceLabel,
        qposStatus: qposResolution.status,
        qposError: qposResolution.errorMessage,
        currentFrameIndex: qposResolution.currentFrameIndex,
        currentTimestampS: qposResolution.currentTimestampS,
        currentQpos: null,
        currentQposText: "[]",
        endpointEvaluation,
        inputOverlay,
      });
      return;
    }

    applyModelPose(
      qposResolution.qpos,
      qposResolution.sourceLabel,
      qposResolution.currentFrameIndex,
      qposResolution.currentTimestampS,
      endpointEvaluation,
      inputOverlay,
      candidate,
    );
    setSceneContactPresentation(geometryStream.apply(payload));
  };

  const syncToLatestSource = (): void => {
    const candidate = frameTiming.takeLatestCandidate();
    if (candidate !== null) {
      applyTransportPayload(candidate);
      return;
    }

    applyStartupPose();
  };

  let selectedCameraView: CameraView = options.initialCameraView ?? "iso";
  const setCameraView = (view: CameraView | "fit"): void => {
    if (view !== "fit") selectedCameraView = view;
    if (data === null) return;
    const framing = cameraPresentation(data.xpos, selectedCameraView);
    if (framing === null) return;
    controls.target.set(...framing.target);
    camera.position.set(...framing.position);
    camera.up.set(...framing.up);
    controls.update();
  };

  const setCanvasSize = (): void => {
    const width = Math.max(1, options.canvas.clientWidth || DEFAULT_WIDTH);
    const height = Math.max(1, options.canvas.clientHeight || DEFAULT_HEIGHT);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };

  const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(setCanvasSize);

  const animate = (): void => {
    if (disposed) {
      return;
    }

    const candidate = frameTiming.takeLatestCandidate();
    if (candidate !== null) {
      applyTransportPayload(candidate);
    }
    controls.update();
    renderer.render(scene, camera);
    frameHandle = window.requestAnimationFrame(animate);
  };

  const acceptCompatiblePayload = (
    payload: TransportPayloadV0,
    observation: ViewerWebSocketPayloadObservation,
  ): void => {
    frameTiming.receive(payload, observation);
    const activeProfile = requireProfile();
    const payloadContactPresentation = parseContactTaskPresentationV1(
      payload.metadata.contact_task_v1,
      {
        profileId: activeProfile.profileId,
        profileContractVersion: activeProfile.profileContractVersion,
      },
      { timeS: payload.time_s, frameIndex: payload.frame_index },
    );
    setContactTaskPresentation(payloadContactPresentation, "transport_metadata");
    const qposResolution = resolveTransportQpos(
      payload,
      model.nq,
      activeProfile,
    );
    if (qposResolution.status !== "ready" || qposResolution.qpos === null) {
      setSceneContactPresentation(unavailableSceneContact("qposとsceneが一致しないため接触表示を消去しました"));
      if (payloadContactPresentation.status !== "stale") {
        setContactTaskPresentation(
          unavailableContactTaskPresentation(
            qposResolution.errorMessage ?? "payload qpos is incompatible; contact overlay was cleared",
          ),
          "transport_metadata",
        );
      }
      frameTiming.recordCompatibilityInvalidIngress();
      updateRendererStatus({
        status: "warning",
        sourceLabel: qposResolution.sourceLabel,
        qposStatus: qposResolution.status,
        qposError: qposResolution.errorMessage,
        currentFrameIndex: qposResolution.currentFrameIndex,
        currentTimestampS: qposResolution.currentTimestampS,
        currentQpos: null,
        currentQposText: "[]",
        endpointEvaluation: payload.endpoint_evaluation ?? null,
        inputOverlay: buildProductViewerInputOverlayState(payload),
      });
      return;
    }
    frameTiming.acceptLatestCandidate(payload, observation);
  };

  const applyOfflinePayload = (payload: TransportPayloadV0): void => {
    const activeProfile = requireProfile();
    const contactPresentation = parseContactTaskPresentationV1(
      payload.metadata.contact_task_v1,
      {
        profileId: activeProfile.profileId,
        profileContractVersion: activeProfile.profileContractVersion,
      },
      { timeS: payload.time_s, frameIndex: payload.frame_index },
    );
    setContactTaskPresentation(contactPresentation, "offline_payload");
    const qposResolution = resolveTransportQpos(
      payload,
      model.nq,
      activeProfile,
    );
    if (qposResolution.status !== "ready" || qposResolution.qpos === null) {
      setSceneContactPresentation(unavailableSceneContact("qposとsceneが一致しないため接触表示を消去しました"));
      if (contactPresentation.status !== "stale") {
        setContactTaskPresentation(
          unavailableContactTaskPresentation(
            qposResolution.errorMessage ?? "transport payload cannot be applied",
          ),
          "offline_payload",
        );
      }
      updateRendererStatus({
        status: "warning",
        sourceLabel: "offline payload-v0 file / transport payload incompatible",
        qposStatus: qposResolution.status,
        qposError: qposResolution.errorMessage,
        currentFrameIndex: qposResolution.currentFrameIndex,
        currentTimestampS: qposResolution.currentTimestampS,
        currentQpos: null,
        currentQposText: "[]",
        endpointEvaluation: payload.endpoint_evaluation ?? null,
        inputOverlay: buildProductViewerInputOverlayState(payload),
      });
      return;
    }

    applyModelPose(
      qposResolution.qpos,
      "offline payload-v0 file",
      qposResolution.currentFrameIndex,
      qposResolution.currentTimestampS,
      payload.endpoint_evaluation ?? null,
      buildProductViewerInputOverlayState(payload),
    );
    setSceneContactPresentation(geometryStream.apply(payload));
  };
  const startWebSocketClient = (): void => {
    if (websocketUrl === null) {
      updateConnectionStatus("disabled");
      return;
    }

    websocketClient = createViewerWebSocketClient({
      url: websocketUrl,
      onOpen() {
        updateConnectionStatus("open");
      },
      onClose() {
        updateConnectionStatus("closed");
      },
      onConnectionError(error) {
        updateConnectionStatus("error", {
          status: "warning",
          qposStatus: "unavailable",
          qposError: error instanceof Error ? error.message : "viewer WebSocket connection error",
        });
      },
      onPayload(payload, observation) {
        if (profile === null || !hasLoaded) {
          pendingBootstrapPayload = { payload, observation };
          if (bootstrapPromise === null) {
            bootstrapPromise = bootstrapFromPayload(payload);
          }
          return;
        }

        const deliveredReference = viewerRobotDeclarationReferenceFromPayload(payload);
        if (declarationReference === null && deliveredReference !== null) {
          pendingBootstrapPayload = { payload, observation };
          if (bootstrapPromise === null) {
            bootstrapPromise = bootstrapFromPayload(payload);
          }
          return;
        }
        if (declarationReference === null) {
          validateViewerRobotProfileCompatibility(payload, profile);
        } else {
          validateViewerRobotProfileFrameReference(
            payload,
            declarationReference,
            profile,
          );
        }
        acceptCompatiblePayload(payload, observation);
      },
      onPayloadError(error) {
        frameTiming.recordParseError();
        updateRendererStatus({
          status: "warning",
          qposStatus: "invalid",
          qposError: error.message,
          currentQpos: null,
          currentQposText: "[]",
        });
      },
    });

    updateConnectionStatus("connecting");
    websocketClient.start();
  };

  let bootstrapPromise: Promise<void> | null = null;
  let pendingBootstrapPayload: {
    payload: TransportPayloadV0;
    observation: ViewerWebSocketPayloadObservation;
  } | null = null;

  const bootstrapFromPayload = async (payload: TransportPayloadV0): Promise<void> => {
    try {
      if (profile === null) {
        const delivered = await loadViewerRobotProfileFromPayload(payload);
        const expectedProfileId = options.expectedProfileId?.trim() || null;
        if (expectedProfileId !== null && delivered.profile.profileId !== expectedProfileId) {
          throw new Error(
            `viewer requested robot profile ${expectedProfileId}, backend declared ${delivered.profile.profileId}`,
          );
        }
        profile = delivered.profile;
        declarationReference = delivered.reference;
        startupPoseSourceLabel = delivered.profile.initialPoseSourceLabel;
        options.onProfileResolved?.(delivered.profile);
        await initializeModel();
      } else {
        const deliveredReference = viewerRobotDeclarationReferenceFromPayload(payload);
        if (deliveredReference === null) {
          throw new Error("viewer declaration frame reference is required before state");
        }
        const actualDigest = await viewerRobotProfileDigest(profile);
        if (actualDigest !== deliveredReference.digest) {
          throw new Error(
            `viewer declaration digest mismatch: expected ${deliveredReference.digest}, got ${actualDigest}`,
          );
        }
        validateViewerRobotProfileCompatibility(payload, profile);
        declarationReference = deliveredReference;
      }

      const pending = pendingBootstrapPayload;
      pendingBootstrapPayload = null;
      if (pending !== null) {
        validateViewerRobotProfileFrameReference(
          pending.payload,
          declarationReference,
          requireProfile(),
        );
        acceptCompatiblePayload(pending.payload, pending.observation);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      updateRendererStatus({
        status: "error",
        qposStatus: "unavailable",
        qposError: message,
        sceneSummaryText: message,
      });
      options.onError?.(error instanceof Error ? error : new Error(message));
    }
  };

  const initializeModel = async (): Promise<void> => {
    const activeProfile = requireProfile();
    updateRendererStatus({ status: "loading" });

    mujocoApi = await loadMujocoWasm();

    const response = await fetch(activeProfile.modelUrl);
    if (!response.ok) {
      throw new Error(`failed to fetch ${activeProfile.modelUrl}: ${response.status} ${response.statusText}`);
    }

    const xml = await response.text();
    const vfs = new mujocoApi.MjVFS();
    try {
      for (const [vfsPath, assetUrl] of activeProfile.vfsAssets.entries()) {
        const assetResponse = await fetch(assetUrl);
        if (!assetResponse.ok) {
          throw new Error(`failed to fetch ${assetUrl}: ${assetResponse.status} ${assetResponse.statusText}`);
        }
        vfs.addBuffer(vfsPath, new Uint8Array(await assetResponse.arrayBuffer()));
      }

      model = mujocoApi.MjModel.from_xml_string(xml, vfs);
      data = new mujocoApi.MjData(model);
      if (model.nq !== activeProfile.qposDimension) {
        throw new Error(
          `viewer model/profile qpos dimension mismatch: expected ${activeProfile.qposDimension}, got ${model.nq}`,
        );
      }
      const modelJointNames = Array.from(
        { length: Number(model.njnt) },
        (_, jointIndex) =>
          mujocoApi.mj_id2name(
            model,
            mujocoApi.mjtObj.mjOBJ_JOINT.value,
            jointIndex,
          ) ?? "",
      );
      if (
        modelJointNames.length !== activeProfile.jointNames.length ||
        modelJointNames.some((name, index) => name !== activeProfile.jointNames[index])
      ) {
        throw new Error(
          `viewer model/profile joint name/order mismatch: expected ${activeProfile.jointNames.join(",")}, got ${modelJointNames.join(",")}`,
        );
      }
      const jointLayout = decodeJointDisplayLayout(modelJointNames, model.jnt_type, model.jnt_qposadr, model.nq);
      const initialKeyframe = resolveNamedInitialKeyframe(model, activeProfile);
      startupQpos = Array.from(initialKeyframe.qpos);
      startupPoseSourceLabel = initialKeyframe.sourceLabel;
      const modelStat = model.stat as any;
      const modelCenter = Array.from(modelStat.center as ArrayLike<number>);
      const modelExtent = Number(modelStat.extent);
      controls.target.set(modelCenter[0], modelCenter[1], modelCenter[2]);
      camera.position.set(
        modelCenter[0] + modelExtent * 1.8,
        modelCenter[1] - modelExtent * 1.9,
        modelCenter[2] + modelExtent * 1.3,
      );
      controls.update();

      for (let meshIndex = 0; meshIndex < model.nmesh; meshIndex += 1) {
        const meshName = mujocoApi.mj_id2name(model, mujocoApi.mjtObj.mjOBJ_MESH.value, meshIndex);
        if (meshName !== null) {
          modelMeshNameById.set(meshIndex, meshName);
        }
      }

      data.qpos.set(startupQpos);
      mujocoApi.mj_forward(model, data);
      mjvScene = new mujocoApi.MjvScene(model, MAX_GEOMS);
      mjvOption = new mujocoApi.MjvOption();
      mjvPerturb = new mujocoApi.MjvPerturb();
      mjvCamera = new mujocoApi.MjvCamera();

      updateRendererStatus({
        jointLayout,
        robotProfileId: activeProfile.profileId,
        modelContractVersion: activeProfile.modelContractVersion,
        modelPath: activeProfile.modelUrl,
        fixturePath: activeProfile.fixtureUrl,
        modelNq: model.nq,
        modelNv: model.nv,
        modelNgeom: model.ngeom,
        modelNmesh: model.nmesh,
      });

      hasLoaded = true;
      syncToLatestSource();
      setCameraView(selectedCameraView);
      updateRendererStatus({
        status: "ready",
        sceneSummaryText: `loaded ${model.ngeom} geoms and ${model.nmesh} compiled meshes`,
      });
    } finally {
      vfs.delete();
    }
  };

  return {
    async start() {
      if (hasLoaded || disposed) {
        return;
      }

      try {
        if (profile === null) {
          if (websocketUrl === null) {
            throw new Error("viewer robot declaration requires a WebSocket startup payload");
          }
          startWebSocketClient();
        } else {
          await initializeModel();
          options.onProfileResolved?.(profile);
          startWebSocketClient();
        }
        setCanvasSize();
        updateRendererStatus({
          statusText: formatViewerStatusText(state),
        });
        window.addEventListener("resize", setCanvasSize);
        resizeObserver?.observe(options.canvas);
        frameHandle = window.requestAnimationFrame(animate);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        updateRendererStatus({
          status: "error",
          qposStatus: "unavailable",
          qposError: message,
          sceneSummaryText: message,
          statusText: [
            `renderer mode: wasm-scene`,
            `robot profile: ${profile?.profileId ?? "unresolved"}`,
            `model path: ${profile?.modelUrl ?? "unresolved"}`,
            `fixture path: ${profile?.fixtureUrl ?? "unresolved"}`,
            `error: ${message}`,
          ].join("\n"),
        });
        options.onError?.(error instanceof Error ? error : new Error(message));
      }
    },
    applyOfflinePayload,
    setCameraView,
    setContactTaskPresentation,
    dispose() {
      disposed = true;
      frameTiming.dispose();
      if (frameHandle !== null) {
        window.cancelAnimationFrame(frameHandle);
        frameHandle = null;
      }
      window.removeEventListener("resize", setCanvasSize);
      resizeObserver?.disconnect();
      websocketClient?.stop();
      websocketClient = null;
      meshGeometryCache.clear();
      objectByGeomIndex.clear();
      materialByKey.clear();
      modelMeshNameById.clear();
      clearContactOverlay();
      geometryOverlay.dispose();
      scene.remove(geometryOverlay.group);
      scene.remove(contactOverlay);
      floorTexture.dispose();
      floorMaterial.dispose();
      renderer.dispose();
    },
  };
}
