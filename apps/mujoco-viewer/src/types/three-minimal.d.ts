declare module "three" {
  export class Matrix4 {
    elements: number[];
    set(
      n11: number,
      n12: number,
      n13: number,
      n14: number,
      n21: number,
      n22: number,
      n23: number,
      n24: number,
      n31: number,
      n32: number,
      n33: number,
      n34: number,
      n41: number,
      n42: number,
      n43: number,
      n44: number,
    ): this;
    compose(position: Vector3, quaternion: Quaternion, scale: Vector3): this;
    copy(matrix: Matrix4): this;
  }

  export class Quaternion {
    constructor(x?: number, y?: number, z?: number, w?: number);
  }

  export class Vector3 {
    constructor(x?: number, y?: number, z?: number);
    x: number;
    y: number;
    z: number;
    length(): number;
    normalize(): this;
  }

  export class Color {
    constructor(color?: unknown);
    setRGB(r: number, g: number, b: number): this;
  }

  export class CanvasTexture {
    constructor(image: HTMLCanvasElement);
    magFilter: number;
    minFilter: number;
    generateMipmaps: boolean;
    wrapS: number;
    wrapT: number;
    repeat: {
      set(x: number, y: number): void;
    };
    needsUpdate: boolean;
    dispose(): void;
  }

  export const DoubleSide: number;
  export const SRGBColorSpace: string;
  export const NearestFilter: number;
  export const RepeatWrapping: number;

  export class Box3 {
    min: Vector3; max: Vector3;
    isEmpty(): boolean; copy(box: Box3): this; union(box: Box3): this; applyMatrix4(matrix: Matrix4): this;
  }

  export class BufferGeometry {
    boundingBox: Box3 | null;
    setAttribute(name: string, attribute: BufferAttribute): this;
    setIndex(attribute: BufferAttribute): this;
    computeVertexNormals(): this;
    computeBoundingBox(): this;
    computeBoundingSphere(): this;
    rotateX(angle: number): this;
    scale(x: number, y: number, z: number): this;
    setFromPoints(points: Array<{ x: number; y: number; z: number }>): this;
    dispose(): void;
  }

  export class BufferAttribute {
    constructor(array: ArrayLike<number>, itemSize: number);
  }

  export class Float32BufferAttribute extends BufferAttribute {
    constructor(array: ArrayLike<number>, itemSize: number);
  }

  export class BoxGeometry extends BufferGeometry {
    constructor(width?: number, height?: number, depth?: number);
  }

  export class SphereGeometry extends BufferGeometry {
    constructor(radius?: number, widthSegments?: number, heightSegments?: number);
  }

  export class TorusGeometry extends BufferGeometry {
    constructor(
      radius?: number,
      tube?: number,
      radialSegments?: number,
      tubularSegments?: number,
    );
  }

  export class CylinderGeometry extends BufferGeometry {
    constructor(
      radiusTop?: number,
      radiusBottom?: number,
      height?: number,
      radialSegments?: number,
    );
  }

  export class PlaneGeometry extends BufferGeometry {
    constructor(width?: number, height?: number);
  }

  export class AxesHelper extends Object3D {
    constructor(size?: number);
    geometry: BufferGeometry;
    material: {dispose(): void} | {dispose(): void}[];
  }

  export class HemisphereLight extends Object3D {
    constructor(skyColor?: unknown, groundColor?: unknown, intensity?: number);
  }

  export class MeshBasicMaterial {
    constructor(parameters?: Record<string, unknown>);
    dispose(): void;
  }

  export class MeshPhongMaterial {
    constructor(parameters?: Record<string, unknown>);
    dispose(): void;
  }

  export class MeshNormalMaterial {
    constructor(parameters?: Record<string, unknown>);
    dispose(): void;
  }

  export class LineBasicMaterial {
    constructor(parameters?: Record<string, unknown>);
    dispose(): void;
  }

  export class Line extends Object3D {
    constructor(geometry?: BufferGeometry, material?: unknown);
    geometry: BufferGeometry;
    material: unknown;
  }

  export class Group extends Object3D {
    add(...objects: Object3D[]): this;
  }

  export class ArrowHelper extends Object3D {
    constructor(
      direction: Vector3,
      origin: Vector3,
      length: number,
      color?: unknown,
      headLength?: number,
      headWidth?: number,
    );
    setDirection(direction: Vector3): void;
  }

  export class Object3D {
    name: string;
    parent: Object3D | null;
    children: Object3D[];
    userData: Record<string, unknown>;
    visible: boolean;
    renderOrder: number;
    matrix: Matrix4;
    matrixAutoUpdate: boolean;
    matrixWorldNeedsUpdate: boolean;
    castShadow: boolean;
    receiveShadow: boolean;
    rotation: {
      x: number;
      y: number;
      z: number;
    };
    up: {
      x: number;
      y: number;
      z: number;
      set(x: number, y: number, z: number): void;
    };
    scale: {
      x: number;
      y: number;
      z: number;
      set(x: number, y: number, z: number): void;
    };
    position: {
      x: number;
      y: number;
      z: number;
      set(x: number, y: number, z: number): void;
    };
    quaternion: {
      x: number;
      y: number;
      z: number;
      w: number;
      set(x: number, y: number, z: number, w: number): void;
    };
    lookAt(x: number, y: number, z: number): void;
    add(...objects: Object3D[]): this;
    clear(): void;
    traverse(callback: (object: Object3D) => void): void;
  }

  export class PerspectiveCamera extends Object3D {
    constructor(fov?: number, aspect?: number, near?: number, far?: number);
    aspect: number;
    fov: number;
    updateProjectionMatrix(): void;
  }
  export class OrthographicCamera extends Object3D {
    constructor(left:number,right:number,top:number,bottom:number,near:number,far:number);
    left:number;right:number;top:number;bottom:number;
    lookAt(x:number,y:number,z:number):void;
    updateProjectionMatrix():void;
  }

  export class AmbientLight extends Object3D {
    constructor(color?: unknown, intensity?: number);
  }

  export class DirectionalLight extends Object3D {
    constructor(color?: unknown, intensity?: number);
  }

  export class Scene extends Object3D {
    add(...objects: Object3D[]): this;
    remove(...objects: Object3D[]): this;
    background: unknown;
  }

  export class Mesh extends Object3D {
    constructor(geometry?: BufferGeometry, material?: unknown);
    geometry: BufferGeometry;
    material: unknown;
  }

  export class WebGLRenderer {
    constructor(parameters?: { canvas?: HTMLCanvasElement; antialias?: boolean; alpha?: boolean });
    domElement: HTMLCanvasElement;
    outputColorSpace: unknown;
    renderLists: {dispose(): void};
    info: {memory: {geometries: number; textures: number}; programs?: unknown[]};
    setPixelRatio(pixelRatio: number): void;
    setSize(width: number, height: number, updateStyle?: boolean): void;
    autoClear:boolean;
    clear():void;
    setViewport(x:number,y:number,width:number,height:number):void;
    setScissor(x:number,y:number,width:number,height:number):void;
    setScissorTest(enabled:boolean):void;
    setClearColor(color: unknown, alpha?: number): void;
    render(scene: Scene, camera: PerspectiveCamera | OrthographicCamera): void;
    compileAsync(scene: Object3D, camera: PerspectiveCamera, targetScene?: Scene): Promise<unknown>;
    dispose(): void;
  }

  export class Uint32BufferAttribute extends BufferAttribute {
    constructor(array: ArrayLike<number>, itemSize: number);
  }
}

declare module "@mujoco/mujoco/mujoco.wasm?url" {
  const url: string;
  export default url;
}

declare module "*?url" {
  const url: string;
  export default url;
}

declare module "three/examples/jsm/controls/OrbitControls.js" {
  import type { PerspectiveCamera } from "three";

  export class OrbitControls {
    constructor(camera: PerspectiveCamera, domElement: HTMLElement);
    target: {
      x: number;
      y: number;
      z: number;
      set(x: number, y: number, z: number): void;
    };
    update(): void;
    dispose(): void;
  }
}
