/** 既にMuJoCoが算出したbody位置の範囲から、表示カメラだけを配置する。 */
export type CameraView = "operator" | "iso" | "front" | "side" | "top";
export interface CameraPresentation {
  target: [number, number, number];
  position: [number, number, number];
  up: [number, number, number];
}

export function cameraPresentation(positions: ArrayLike<number>, view: CameraView): CameraPresentation | null {
  if (positions.length < 6 || positions.length % 3 !== 0) return null;
  const lower = [Infinity, Infinity, Infinity];
  const upper = [-Infinity, -Infinity, -Infinity];
  // world bodyは除外。joint値やgeometryを推定/変更しない。
  for (let index = 3; index < positions.length; index += 1) {
    const value = positions[index];
    if (!Number.isFinite(value)) return null;
    const axis = index % 3;
    lower[axis] = Math.min(lower[axis], value);
    upper[axis] = Math.max(upper[axis], value);
  }
  const target = lower.map((value, axis) => (value + upper[axis]) / 2) as [number, number, number];
  const span = Math.max(...upper.map((value, axis) => value - lower[axis]));
  // 最小距離と余白は画面framingの値であり、robotの可動域や安全幅ではない。
  const distance = Math.max(0.45, span * 2.4);
  // operatorは胴体+X前方に対して背後(-X)・やや上から見る固定TPS preset。
  // camera-relative commandにはせず、screen rightがworld -Yになる基準viewだけを提供する。
  const direction = {
    operator: [-1, 0, 0.55],
    iso: [1, -1, 0.75],
    front: [0, -1, 0],
    side: [1, 0, 0],
    top: [0, 0, 1],
  }[view];
  const length = Math.hypot(...direction);
  return {
    target,
    position: target.map((value, axis) => value + direction[axis] / length * distance) as [number, number, number],
    up: view === "top" ? [0, 1, 0] : [0, 0, 1],
  };
}
