import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { Matrix4, Quaternion, Vector3 } from "three";
import { matrixFromMujocoGeom, matrixFromMujocoMeshTransform } from "../src/wasm-scene/mujocoSceneTransforms.js";

describe("mujoco scene transforms", () => {
  it("maps MuJoCo mesh asset transforms into three.js matrices", () => {
    const matrix = matrixFromMujocoMeshTransform({
      pos: [1, 2, 3],
      quat: [1, 0, 0, 0],
      scale: [2, 3, 4],
    });

    const expected = new Matrix4();
    expected.compose(new Vector3(1, 2, 3), new Quaternion(0, 0, 0, 1), new Vector3(2, 3, 4));

    assert.deepEqual(matrix.elements, expected.elements);
  });

  it("maps MuJoCo geom transforms into three.js matrices", () => {
    const matrix = matrixFromMujocoGeom({
      pos: [4, 5, 6],
      mat: [1, 0, 0, 0, 1, 0, 0, 0, 1],
    });

    const expected = new Matrix4();
    expected.compose(new Vector3(4, 5, 6), new Quaternion(0, 0, 0, 1), new Vector3(1, 1, 1));

    assert.deepEqual(matrix.elements, expected.elements);
  });

  it("90度回転と平行移動を独立した座標期待値で検証する", () => {
    const matrix = matrixFromMujocoGeom({
      pos: [4, 5, 6],
      mat: [0, -1, 0, 1, 0, 0, 0, 0, 1],
    });
    const cases = [
      { point: [0, 0, 0], expected: [4, 5, 6] },
      { point: [1, 0, 0], expected: [4, 6, 6] },
      { point: [0, 1, 0], expected: [3, 5, 6] },
      { point: [0, 0, 1], expected: [4, 5, 7] },
    ];

    for (const { point, expected } of cases) {
      const [x, y, z] = point;
      const e = matrix.elements;
      const actual = [
        e[0] * x + e[4] * y + e[8] * z + e[12],
        e[1] * x + e[5] * y + e[9] * z + e[13],
        e[2] * x + e[6] * y + e[10] * z + e[14],
      ];
      assert.deepEqual(actual, expected, `local point ${point}`);
    }
  });
});
