/**
 * MuJoCo/WASM sceneをThree.jsへ描画投影するrenderer。
 * Python/MuJoCo physical stateをSoTとし、viewer-side FK/IKや独立physics stepを行わない。
 */
import {exportedHeapBytes} from "./exportedHeap.js";
import {
  AmbientLight,
  ArrowHelper,
  AxesHelper,
  BoxGeometry,
  Box3,
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
  OrthographicCamera,
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
import { DynamicsStream } from "./dynamicsPresentation.js";
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
import { loadMujocoWasm, mujocoWasmModuleBuilds } from "./mujocoWasmLoader.js";
import { matrixFromMujocoGeom } from "./mujocoSceneTransforms.js";
import { applyMujocoDisplayPose, createMujocoDisplayOption } from "./mujocoDisplayPose.js";
import {
  resolveNamedInitialKeyframe,
  resolveTransportQpos,
} from "./mujocoQposSync.js";
import { resolveBodyVisualStyle, resolveGeomDisplayColor, geomMaterialCacheKey } from "./visualStyles.js";
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
import { createAsyncResourceLifetime } from "./asyncResourceLifetime.js";

import { validateCompiledSceneLayout } from "../robot-profiles/sceneStateLayout.js";
import { decodeJointDisplayLayout } from "./jointPresentation.js";
import { boundsCenter, perspectiveBoundsFit, orthographicHalfWidth, type DisplayBounds } from "./sceneFraming.js";
import { cameraPresentation, type CameraView } from "./cameraPresentation.js";
import { assistPose, scissorRect, type ScenePane } from "./viewportPresentation.js";

export interface MujocoSceneRendererOptions {
  canvas: HTMLCanvasElement;
  interactionElement?: HTMLElement;
  getScenePanes?: () => readonly ScenePane[];
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
  prepareWorkbench(payload: TransportPayloadV0, epoch: string): Promise<void>;
  applyWorkbench(payload: TransportPayloadV0, epoch: string): void;
  invalidateWorkbench(): void;
  counters(): Record<string, number|null>;
  applyOfflinePayload(payload: TransportPayloadV0): void;
  setCameraView(view: CameraView | "fit" | "focus"): void;
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
  let frameTiming = createViewerFrameTiming();
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
  let geometryStream = new SceneContactStream();
  let dynamicsStream = new DynamicsStream();
  scene.add(geometryOverlay.group);

  const renderer = new WebGLRenderer({ canvas: options.canvas, antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setSize(DEFAULT_WIDTH, DEFAULT_HEIGHT, false);
  renderer.outputColorSpace = SRGBColorSpace;

  const camera = new PerspectiveCamera(45, DEFAULT_WIDTH / DEFAULT_HEIGHT, 0.01, 100);
  camera.position.set(1.8, -1.8, 1.5);
  camera.up.set(0, 0, 1);

  const controls = new OrbitControls(camera, options.interactionElement ?? options.canvas);
  const topCamera = new OrthographicCamera(-1,1,1,-1,0.01,100);
  const frontCamera = new OrthographicCamera(-1,1,1,-1,0.01,100);
  let assistExtent = 1;
  let assistBounds: DisplayBounds | null = null;
  const fitAssist = (): void => {
    assistBounds = displayBounds(true);
    const center = assistBounds && boundsCenter(assistBounds);
    if (!center) return;
    assistExtent = Math.max(0.05,...assistBounds!.max.map((v,i)=>v-assistBounds!.min[i]));
    for (const [view,cam] of [["assist-top",topCamera],["assist-front",frontCamera]] as const) {
      const pose=assistPose(center,assistExtent,view);
      cam.position.set(...pose.position as [number,number,number]);
      cam.up.set(...pose.up as [number,number,number]);
      cam.lookAt(...pose.target as [number,number,number]);
    }
  };
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
  // native由来のmesh変換だけを使用。床面/軸/接触矢印をboundsへ入れず、model原点へ引き寄せない。
  const displayBounds = (focus: boolean): DisplayBounds | null => {
    const all = new Box3(), working = new Box3();
    for (const mesh of objectByGeomIndex.values()) {
      if (mesh.userData.worldPlane === true) continue;
      if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
      if (!mesh.geometry.boundingBox) continue;
      const box = new Box3().copy(mesh.geometry.boundingBox).applyMatrix4(mesh.matrix);
      if (![box.min.x,box.min.y,box.min.z,box.max.x,box.max.y,box.max.z].every(Number.isFinite)) continue;
      all.union(box);
      // bodyに属するRobotと物体を優先。world直下の固定支持台は「全体」で含める。
      if (typeof mesh.userData.nativeBodyId === "number" && mesh.userData.nativeBodyId > 0) working.union(box);
    }
    const box = focus && !working.isEmpty() ? working : all;
    return box.isEmpty() ? null : {min:[box.min.x,box.min.y,box.min.z],max:[box.max.x,box.max.y,box.max.z]};
  };

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
  let workbenchEpoch: string | null = null;
  let loadGeneration = 0;
  let modelBuilds = 0;
  let modelDeletes = 0;
  let liveVfs = 0;
  let workbenchChain: Promise<void> = Promise.resolve();
  const lifetimeAbort = new AbortController();
  let prepareAbort: AbortController | null = null;
  let listening = false;
  let lastWorkbenchFrame = -1;
  let lastWorkbenchTime = -1;

  const websocketUrl =
    options.websocketUrl === undefined || options.websocketUrl === null || options.websocketUrl.trim() === ""
      ? null
      : options.websocketUrl;

  const updateRendererStatus = (patch: ProductViewerRendererStatePatch): void => {
    emitState(applyProductViewerRendererStatePatch(state, patch));
  };

  const setSceneContactPresentation = (value: SceneContactPresentation): void => {
    geometryOverlay.update(value);
    updateRendererStatus({sceneContactPresentation: value,
      ...(value.status==="unavailable" ? {dynamicsPresentation:{status:"unavailable" as const,reason:value.reason}} : {})});
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

  let renderResourcesReleased = false;
  const releaseModel = (): void => {
    hasLoaded = false;
    const geometries = new Set<BufferGeometry>();
    const materials = new Set<MeshPhongMaterial>();
    for (const object of objectByGeomIndex.values()) {
      geometries.add(object.geometry);
      for (const material of (Array.isArray(object.material) ? object.material : [object.material])) {
        if (material !== floorMaterial) materials.add(material as MeshPhongMaterial);
      }
      scene.remove(object);
    }
    for (const geometry of meshGeometryCache.values()) geometries.add(geometry);
    for (const material of materialByKey.values()) if (material !== floorMaterial) materials.add(material);
    const textures = new Set<any>();
    for (const material of materials) {
      for (const value of Object.values(material)) if (value && (value as any).isTexture && value !== floorTexture) textures.add(value);
      material.dispose();
    }
    for (const texture of textures) texture.dispose();
    for (const geometry of geometries) geometry.dispose();
    for (const resource of [mjvScene, mjvOption, mjvPerturb, mjvCamera, data]) resource?.delete();
    if (model) { model.delete(); modelDeletes += 1; }
    model = data = mjvScene = mjvOption = mjvPerturb = mjvCamera = undefined;
    meshGeometryCache.clear(); objectByGeomIndex.clear(); materialByKey.clear(); modelMeshNameById.clear();
    renderer.renderLists.dispose();
  };
  const releaseRenderResources = (): void => {
    if (renderResourcesReleased) return;
    renderResourcesReleased = true;
    releaseModel();
    controls.dispose();
    axesHelper.geometry.dispose();
    for (const material of (Array.isArray(axesHelper.material) ? axesHelper.material : [axesHelper.material])) material.dispose();
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
  };
  const renderResourceLifetime = createAsyncResourceLifetime(releaseRenderResources);

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
      next.dynamicsPresentation = {status:"unavailable",reason:value.reason ?? "接続無効"};
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
    const meshName = meshId >= 0 && meshId < model.nmesh ? (modelMeshNameById.get(meshId) ?? "") : "";
    const cacheKey = geomMaterialCacheKey(bodyName, geomName, meshName, geom.type, Array.from(geom.rgba));
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
        mesh.userData.worldPlane = geom.type === mujocoApi.mjtGeom.mjGEOM_PLANE.value;
        mesh.userData.nativeBodyId = geom.objtype === mujocoApi.mjtObj.mjOBJ_GEOM.value
          ? Number(model.geom_bodyid[geom.objid]) : null;
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        objectByGeomIndex.set(geomIndex, mesh);
        scene.add(mesh);
        sourceGeom?.delete?.();
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
    applyMujocoDisplayPose(mujocoApi, model, data, qpos);
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
    const sceneContact = geometryStream.apply(payload);
    setSceneContactPresentation(sceneContact);
    updateRendererStatus({dynamicsPresentation:sceneContact.status==="unavailable"
      ? {status:"unavailable",reason:sceneContact.reason} : dynamicsStream.apply(payload)});
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
  const setCameraView = (view: CameraView | "fit" | "focus"): void => {
    if (view !== "fit" && view !== "focus") selectedCameraView = view;
    if (!data) return;
    const legacy = cameraPresentation(data.xpos, selectedCameraView);
    if (!legacy) return;
    const keepDirection = view === "fit" || view === "focus";
    const offset = keepDirection
      ? [camera.position.x-controls.target.x,camera.position.y-controls.target.y,camera.position.z-controls.target.z]
      : legacy.position.map((v,i)=>v-legacy.target[i]);
    const up = keepDirection ? [camera.up.x,camera.up.y,camera.up.z] : legacy.up;
    const pane = options.getScenePanes?.().find(p=>p.id==="main");
    const aspect = pane && pane.width>0 && pane.height>0 ? pane.width/pane.height : camera.aspect;
    const bounds = displayBounds(view !== "fit");
    const fit = bounds && perspectiveBoundsFit(bounds,offset,up,aspect,camera.fov,view==="fit"?1.3:1.18);
    if (!fit) return;
    camera.position.set(...fit.position);
    camera.up.set(...up as [number,number,number]);
    controls.target.set(...fit.target);
    controls.update();
    fitAssist();
  };

  // 描画提出の観測値。GPU完了・表示走査や実device遅延ではない。
  let renderedFrameIndex: number | null = null;
  let renderedInputSequence: number | null = null;
  let renderedAtMs: number | null = null;
  let renderedPaneCount = 0;
  let rendererPixelRatio=window.devicePixelRatio || 1;
  const setCanvasSize = (): void => {
    const width = Math.max(1, options.canvas.clientWidth || DEFAULT_WIDTH);
    const height = Math.max(1, options.canvas.clientHeight || DEFAULT_HEIGHT);
    renderer.setSize(width, height, false);
    rendererPixelRatio=window.devicePixelRatio || 1;
    renderer.setPixelRatio(rendererPixelRatio);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };

  const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(setCanvasSize);

  const animate = (): void => {
    if (disposed) {
      return;
    }

    if (rendererPixelRatio !== (window.devicePixelRatio || 1)) setCanvasSize();
    const candidate = frameTiming.takeLatestCandidate();
    if (candidate !== null) {
      applyTransportPayload(candidate);
    }
    controls.update();
    if (options.canvas.clientWidth && options.canvas.clientHeight && document.visibilityState !== "hidden") {
      const panes=options.getScenePanes?.() ?? [{id:"main",x:0,y:0,width:options.canvas.clientWidth,height:options.canvas.clientHeight}];
      renderer.setScissorTest(false);
      renderer.setViewport(0,0,options.canvas.clientWidth,options.canvas.clientHeight);
      renderer.clear();
      renderer.autoClear=false;
      renderer.setScissorTest(true);
      const topPane=panes.find(p=>p.id==="assist-top"), frontPane=panes.find(p=>p.id==="assist-front");
      if (assistBounds && topPane && frontPane && topPane.height>0 && frontPane.height>0) {
        assistExtent=orthographicHalfWidth(assistBounds,topPane.width/topPane.height,frontPane.width/frontPane.height) ?? assistExtent;
      }
      for (const pane of panes) {
        if (pane.width<=0 || pane.height<=0) continue;
        const rect=scissorRect(pane,options.canvas.clientHeight);
        renderer.setViewport(rect.x,rect.y,rect.width,rect.height);
        renderer.setScissor(rect.x,rect.y,rect.width,rect.height);
        const cam=pane.id==="main"?camera:pane.id==="assist-top"?topCamera:frontCamera;
        if (cam===camera) camera.aspect=pane.width/pane.height;
        else {
          // 上面/正面のY中心とCSS pxあたり縮尺を合わせる。明示fit以外は追従しない。
          const assist=panes.filter(p=>p.id!=="main");
          const scale=assistExtent/Math.max(1,Math.min(...assist.map(p=>p.width)));
          const orthographic=cam as OrthographicCamera;
          orthographic.left=-pane.width*scale;orthographic.right=pane.width*scale;
          orthographic.top=pane.height*scale;orthographic.bottom=-pane.height*scale;
        }
        cam.updateProjectionMatrix();
        renderer.render(scene,cam);
      }
      renderer.setScissorTest(false);
      renderer.autoClear=true;
      renderedFrameIndex = state.currentFrameIndex;
      renderedInputSequence = state.inputOverlay?.sequence ?? null;
      renderedAtMs = performance.now();
      renderedPaneCount = panes.filter(p=>p.width>0&&p.height>0).length;
    }
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
    const sceneContact = geometryStream.apply(payload);
    setSceneContactPresentation(sceneContact);
    updateRendererStatus({dynamicsPresentation:sceneContact.status==="unavailable"
      ? {status:"unavailable",reason:sceneContact.reason} : dynamicsStream.apply(payload)});
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
    if (disposed) return;
    try {
      if (profile === null) {
        const delivered = await loadViewerRobotProfileFromPayload(payload,
          (input, init) => fetch(input, {...init, signal:lifetimeAbort.signal}));
        if (disposed) return;
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
        await renderResourceLifetime.run(initializeModel);
        if (disposed) return;
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
      if (disposed) return;
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

  const initializeModel = async (isCurrent: () => boolean = () => !disposed,
    signal: AbortSignal = lifetimeAbort.signal): Promise<void> => {
    const checkCurrent = () => { if (!isCurrent()) throw new Error("旧scene準備を破棄しました"); };
    const activeProfile = requireProfile();
    updateRendererStatus({ status: "loading" });

    mujocoApi = await loadMujocoWasm();
    checkCurrent();

    const response = await fetch(activeProfile.modelUrl, {signal});
    if (!response.ok) {
      throw new Error(`failed to fetch ${activeProfile.modelUrl}: ${response.status} ${response.statusText}`);
    }

    const xml = await response.text();
    checkCurrent();
    const vfs = new mujocoApi.MjVFS();
    liveVfs += 1;
    try {
      for (const [vfsPath, assetUrl] of activeProfile.vfsAssets.entries()) {
        const assetResponse = await fetch(assetUrl, {signal});
        if (!assetResponse.ok) {
          throw new Error(`failed to fetch ${assetUrl}: ${assetResponse.status} ${assetResponse.statusText}`);
        }
        const bytes = new Uint8Array(await assetResponse.arrayBuffer());
        checkCurrent();
        vfs.addBuffer(vfsPath, bytes);
      }

      model = mujocoApi.MjModel.from_xml_string(xml, vfs);
      modelBuilds += 1;
      data = new mujocoApi.MjData(model);
      if (model.nq !== (activeProfile.sceneStateLayout?.qpos_dimension ?? activeProfile.qposDimension)) {
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
        !activeProfile.sceneStateLayout && (modelJointNames.length !== activeProfile.jointNames.length ||
        modelJointNames.some((name, index) => name !== activeProfile.jointNames[index]))
      ) {
        throw new Error(
          `viewer model/profile joint name/order mismatch: expected ${activeProfile.jointNames.join(",")}, got ${modelJointNames.join(",")}`,
        );
      }
      if(activeProfile.sceneStateLayout)validateCompiledSceneLayout(activeProfile.sceneStateLayout,model.nq,model.nv,modelJointNames,model.jnt_type,model.jnt_qposadr,model.jnt_dofadr);
      const fullJointLayout = decodeJointDisplayLayout(modelJointNames, model.jnt_type, model.jnt_qposadr, model.nq);
      const jointLayout = activeProfile.sceneStateLayout ? {...fullJointLayout,joints:fullJointLayout.joints.filter(j=>activeProfile.jointNames.includes(j.name))} : fullJointLayout;
      const initialKeyframe = resolveNamedInitialKeyframe(model, activeProfile);
      startupQpos = Array.from(initialKeyframe.qpos);
      startupPoseSourceLabel = initialKeyframe.sourceLabel;
      const modelStat = model.stat as any;
      const modelCenter = Array.from(modelStat.center as ArrayLike<number>);
      const modelExtent = Number(modelStat.extent);
      modelStat.delete?.();
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

      applyMujocoDisplayPose(mujocoApi, model, data, startupQpos);
      mjvScene = new mujocoApi.MjvScene(model, MAX_GEOMS);
      mjvOption = createMujocoDisplayOption(mujocoApi);
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

      // 初回接触のshader compileを実行中の入力heartbeat区間へ持ち込まない。
      // 観測pointを作らずhidden poolを準備し、ready通知はGPU準備完了後に限る。
      syncSceneFromCurrentData();
      await geometryOverlay.prepare(objects => renderer.compileAsync(objects, camera, scene));
      checkCurrent();
      await renderer.compileAsync(scene, camera);
      checkCurrent();
      hasLoaded = true;
      syncToLatestSource();
      setCameraView(selectedCameraView);
      updateRendererStatus({
        status: "ready",
        sceneSummaryText: `loaded ${model.ngeom} geoms and ${model.nmesh} compiled meshes`,
      });
    } catch (error) {
      releaseModel();
      throw error;
    } finally {
      vfs.delete();
      liveVfs -= 1;
    }
  };

  return {
    prepareWorkbench(payload, epoch) {
      const generation = ++loadGeneration;
      prepareAbort?.abort();
      const abort = new AbortController();
      prepareAbort = abort;
      workbenchEpoch = null;
      const operation = workbenchChain.catch(() => {}).then(async () => {
        if (disposed || generation !== loadGeneration) throw new Error("旧scene準備を破棄しました");
        const reference = viewerRobotDeclarationReferenceFromPayload(payload);
        const runtime = payload.metadata.coordinated_runtime_v1 as {epoch?: string} | undefined;
        if (!reference || runtime?.epoch !== epoch) throw new Error("初期scene/epochが不一致です");
        await renderResourceLifetime.run(async () => {
          if (!hasLoaded || declarationReference?.digest !== reference.digest) {
            releaseModel();
            const delivered = await loadViewerRobotProfileFromPayload(payload,
              (input, init) => fetch(input, {...init, signal:abort.signal}));
            if (disposed || generation !== loadGeneration) throw new Error("旧scene準備を破棄しました");
            profile = delivered.profile;
            declarationReference = delivered.reference;
            options.onProfileResolved?.(profile);
            try { await initializeModel(() => !disposed && generation === loadGeneration, abort.signal); }
            catch (error) { releaseModel(); throw error; }
          }
          if (disposed || generation !== loadGeneration) throw new Error("旧scene準備を破棄しました");
          validateViewerRobotProfileCompatibility(payload, requireProfile());
          validateViewerRobotProfileFrameReference(payload, declarationReference, requireProfile());
          frameTiming.dispose(); frameTiming = createViewerFrameTiming();
          geometryStream = new SceneContactStream(); dynamicsStream = new DynamicsStream();
          workbenchEpoch = epoch;
          lastWorkbenchFrame = payload.frame_index;
          lastWorkbenchTime = payload.time_s;
          mujocoApi.mj_resetData(model, data);
          applyOfflinePayload(payload);
          if (state.qposStatus !== "ready") throw new Error(state.qposError ?? "初期scene状態を適用できません");
          if (!listening) {
            listening = true; setCanvasSize();
            window.addEventListener("resize", setCanvasSize); resizeObserver?.observe(options.canvas);
            frameHandle = window.requestAnimationFrame(animate);
          }
        });
      });
      workbenchChain = operation.finally(() => { if (prepareAbort === abort) prepareAbort = null; });
      // callerと同じpromiseを返し、拒否されたfinally枝を未処理にしない。
      return workbenchChain;
    },
    invalidateWorkbench() { loadGeneration += 1; workbenchEpoch = null; prepareAbort?.abort(); },
    applyWorkbench(payload, epoch) {
      if (disposed || !hasLoaded || epoch !== workbenchEpoch || !declarationReference) throw new Error("旧epochまたは未準備sceneです");
      const runtime = payload.metadata.coordinated_runtime_v1 as {epoch?: string} | undefined;
      if (runtime?.epoch !== epoch) throw new Error("frameのepochが不一致です");
      validateViewerRobotProfileCompatibility(payload, requireProfile());
      validateViewerRobotProfileFrameReference(payload, declarationReference, requireProfile());
      if (payload.frame_index <= lastWorkbenchFrame) return;
      if (!Number.isFinite(payload.time_s) || payload.time_s < lastWorkbenchTime) throw new Error("同じepoch内の時刻後退は拒否します");
      lastWorkbenchFrame = payload.frame_index;
      lastWorkbenchTime = payload.time_s;
      applyOfflinePayload(payload);
    },
    counters() { return {modelBuilds, modelDeletes, liveVfs, wasmModules: mujocoWasmModuleBuilds(),
      wasmHeapBytes: exportedHeapBytes(mujocoApi), nativeModels: model ? 1 : 0, nativeData: data ? 1 : 0,
      geometries: objectByGeomIndex.size, meshCache: meshGeometryCache.size, materials: materialByKey.size,
      gpuGeometries: renderer.info.memory.geometries, gpuTextures: renderer.info.memory.textures,
      programs: renderer.info.programs?.length ?? 0, windowResizeListeners: Number(listening && !disposed),
      resizeObservers: Number(listening && !disposed && resizeObserver !== null), orbitControls: Number(!renderResourcesReleased),
      raf: frameHandle === null ? 0 : 1, generation: loadGeneration,
      cameraX:camera.position.x,cameraY:camera.position.y,cameraZ:camera.position.z,
      targetX:controls.target.x,targetY:controls.target.y,targetZ:controls.target.z,
      renderedFrameIndex, renderedInputSequence, renderedAtMs, renderedPaneCount,
      sceneAppliedFrames:frameTiming.snapshot().sceneAppliedFrameCount,
      receiveToApplyP50Ms:frameTiming.snapshot().receiveToApplyAgeMsP50,
      receiveToApplyP95Ms:frameTiming.snapshot().receiveToApplyAgeMsP95}; },
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
          await renderResourceLifetime.run(initializeModel);
          if (disposed) return;
          options.onProfileResolved?.(profile);
          startWebSocketClient();
        }
        if (disposed) return;
        setCanvasSize();
        updateRendererStatus({
          statusText: formatViewerStatusText(state),
        });
        window.addEventListener("resize", setCanvasSize);
        listening = true;
        resizeObserver?.observe(options.canvas);
        frameHandle = window.requestAnimationFrame(animate);
      } catch (error) {
        if (disposed) return;
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
      if (disposed) return;
      disposed = true;
      lifetimeAbort.abort(); prepareAbort?.abort();
      loadGeneration += 1;
      workbenchEpoch = null;
      frameTiming.dispose();
      if (frameHandle !== null) {
        window.cancelAnimationFrame(frameHandle);
        frameHandle = null;
      }
      window.removeEventListener("resize", setCanvasSize);
      resizeObserver?.disconnect();
      websocketClient?.stop();
      websocketClient = null;
      pendingBootstrapPayload = null;
      // compileAsyncが内部poll中なら、外部利用だけ即時停止しGPU資源の破棄はsettle後へ遅延する。
      renderResourceLifetime.requestDispose();
    },
  };
}
