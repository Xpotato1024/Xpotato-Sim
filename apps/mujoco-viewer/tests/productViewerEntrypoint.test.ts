import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { resolve } from "node:path";

describe("product viewer entrypoint", () => {
  it("boots the wasm-scene app without importing the old renderer stack", () => {
    const mainPath = resolve(process.cwd(), "src", "main.tsx");
    const source = readFileSync(mainPath, "utf8");

    assert.match(source, /ProductViewerApp/);
    assert.doesNotMatch(source, /viewerRuntime|browserSceneRenderer|fastArmMeshes|threeSceneObjects/);
  });
});

describe("retired connection query", () => {
  it("rejects before static profile loading and disables live input", () => {
    const source = readFileSync(resolve(process.cwd(), "src/app/ProductViewerApp.tsx"), "utf8");
    const start = source.indexOf("const start = async");
    const rejection = source.indexOf("if (endpointConfig.error !== undefined) throw new Error(endpointConfig.error)", start);
    const staticLoad = source.indexOf("await loadDefaultViewerRobotProfile()", start);
    const renderer = source.indexOf("renderer = createMujocoSceneRenderer", start);
    assert.ok(start >= 0 && rejection > start && staticLoad > rejection && renderer > staticLoad);
    assert.match(source, /const liveInputEnabled[^;]*endpointConfig\.error === undefined/);
  });
});
