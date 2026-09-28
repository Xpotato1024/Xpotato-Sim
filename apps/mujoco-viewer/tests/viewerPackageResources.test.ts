import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, it } from "node:test";

import {
  createViewerPackageResourcePlugin,
  decodeViewerPackageResourceManifest,
  loadDynamicViewerResourcesFromEnvironment,
  loadViewerPackageResources,
  validateViewerProfileResourceBindings,
  type ViewerPackageResourceBinding,
} from "../tooling/viewerPackageResources.js";


const appRoot = process.cwd();
const repoRoot = resolve(appRoot, "../..");

function loadCurrent() {
  return loadViewerPackageResources(repoRoot);
}

describe("viewer package resources", () => {
  it("decodes package-owned manifests and resolves every declared source", () => {
    const decoded = loadCurrent();
    assert.ok(decoded.manifests.length > 0);
    assert.ok(decoded.resources.length > 0);
    for (const manifest of decoded.manifests) {
      const normalized = decodeViewerPackageResourceManifest(
        JSON.parse(JSON.stringify(manifest)),
      );
      assert.deepEqual(normalized, manifest);
    }
    for (const resource of decoded.resources) {
      assert.ok(readFileSync(resource.sourcePath).length > 0);
    }
  });

  it("rejects unknown fields and duplicate logical IDs, URLs, and bundle paths", () => {
    const source = JSON.parse(JSON.stringify(loadCurrent().manifests[0]));
    assert.throws(
      () => decodeViewerPackageResourceManifest({ ...source, unexpected: true }),
      /keys mismatch/,
    );
    const dependencies = source.resources.filter(
      (resource: ViewerPackageResourceBinding) => resource.role === "model_dependency",
    );
    const duplicateIdentity = JSON.parse(JSON.stringify(source));
    const duplicateDependencies = duplicateIdentity.resources.filter(
      (resource: ViewerPackageResourceBinding) => resource.role === "model_dependency",
    );
    duplicateDependencies[1].logicalIdentifier = duplicateDependencies[0].logicalIdentifier;
    duplicateDependencies[1].url = duplicateDependencies[0].url;
    assert.throws(() => decodeViewerPackageResourceManifest(duplicateIdentity), /must be unique/);

    const duplicateBundle = JSON.parse(JSON.stringify(source));
    const duplicateBundleDependencies = duplicateBundle.resources.filter(
      (resource: ViewerPackageResourceBinding) => resource.role === "model_dependency",
    );
    duplicateBundleDependencies[1].bundlePath = dependencies[0].bundlePath;
    assert.throws(() => decodeViewerPackageResourceManifest(duplicateBundle), /bundle paths must be unique/);
  });

  it("rejects invalid package paths, logical paths, and URLs", () => {
    const source = JSON.parse(JSON.stringify(loadCurrent().manifests[0]));
    for (const [field, value] of [
      ["package", "not-a-package"],
      ["package", "fast_é"],
      ["packageResourcePath", "../scene.xml"],
      ["packageResourcePath", "resources//scene.xml"],
      ["logicalIdentifier", "assets/../scene.xml"],
      ["logicalIdentifier", "assets//fast_arm/scene.xml"],
      ["url", "https://example.invalid/scene.xml"],
      ["bundlePath", "bundle//scene.xml"],
    ] as const) {
      const invalid = JSON.parse(JSON.stringify(source));
      invalid.resources[0][field] = value;
      assert.throws(() => decodeViewerPackageResourceManifest(invalid));
    }
  });

  it("fails closed when a viewer profile and manifest binding differ", () => {
    const decoded = loadCurrent();
    const manifest = decoded.manifests[0]!;
    const model = manifest.resources.find((item) => item.role === "model_entrypoint")!;
    const changedModel: ViewerPackageResourceBinding = {
      ...model,
      logicalIdentifier: "assets/mujoco/changed/scene.xml",
      url: "/mujoco/changed/scene.xml",
    };
    const changedManifest = {
      ...manifest,
      resources: manifest.resources.map((item) => item === model ? changedModel : item),
    };
    assert.throws(
      () => validateViewerProfileResourceBindings(changedManifest, decoded.resources),
      /viewer model resource/,
    );

    const fixture = manifest.resources.find((item) => item.role === "fixture")!;
    const changedFixture: ViewerPackageResourceBinding = {
      ...fixture,
      logicalIdentifier: "assets/mujoco/changed/fixture.json",
      url: "/mujoco/changed/fixture.json",
    };
    assert.throws(
      () => validateViewerProfileResourceBindings(
        {
          ...manifest,
          resources: manifest.resources.map((item) => item === fixture ? changedFixture : item),
        },
        decoded.resources,
      ),
      /viewer fixture resource/,
    );

    assert.throws(
      () => validateViewerProfileResourceBindings(
        {
          ...manifest,
          resources: manifest.resources.filter((item) => item.role !== "model_dependency"),
        },
        decoded.resources,
      ),
      /viewer VFS resources/,
    );
  });

  it("contains no hard-coded robot resource inventory in generic Vite config", () => {
    const source = readFileSync(resolve(appRoot, "vite.config.ts"), "utf8");
    for (const forbidden of [
      "BaseLink.stl",
      "SholderLink1.stl",
      "fast_arm_core/resources/model",
      "adapter/resources",
      "fastArmPackageResources",
    ]) {
      assert.equal(source.includes(forbidden), false, forbidden);
    }
    assert.match(source, /createViewerPackageResourcePlugin\(repoRoot\)/);
  });

  it("loads bounded dynamic application resources only from an explicit absolute root", () => {
    assert.deepEqual([...loadDynamicViewerResourcesFromEnvironment({})], []);
    const root = mkdtempSync(join(tmpdir(), "xpotato-viewer-resources-"));
    try {
      mkdirSync(join(root, "mujoco", "assembly"), { recursive: true });
      const model = join(root, "mujoco", "assembly", "model.xml");
      writeFileSync(model, "<mujoco/>", "utf8");
      const loaded = loadDynamicViewerResourcesFromEnvironment({
        XPOTATO_SIM_DYNAMIC_VIEWER_RESOURCE_ROOT: root,
      });
      assert.equal(loaded.get("/mujoco/assembly/model.xml"), model);
      assert.equal(loaded.size, 1);
      assert.throws(
        () => loadDynamicViewerResourcesFromEnvironment({
          XPOTATO_SIM_DYNAMIC_VIEWER_RESOURCE_ROOT: "relative/path",
        }),
        /must be absolute/,
      );
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("does not expose URLs absent from decoded manifests", () => {
    const publicUrls = new Set(
      loadCurrent().resources.flatMap((resource) => resource.url === null ? [] : [resource.url]),
    );
    assert.equal(publicUrls.has("/mujoco/fast_arm/not-declared.bin"), false);
  });

  it("serves only decoded public resources and delegates unknown URLs", () => {
    const decoded = loadCurrent();
    const known = decoded.resources.find((resource) => resource.url !== null)!;
    const plugin = createViewerPackageResourcePlugin(repoRoot);
    let middleware: ((
      request: { url?: string },
      response: { statusCode: number; end(body: unknown): void },
      next: () => void,
    ) => void) | undefined;
    assert.equal(typeof plugin.configureServer, "function");
    (plugin.configureServer as (server: unknown) => void)({
      middlewares: {
        use(value: typeof middleware) {
          middleware = value;
        },
      },
    });
    assert.ok(middleware);

    let served: unknown;
    let delegated = false;
    const response = {
      statusCode: 0,
      end(body: unknown) {
        served = body;
      },
    };
    middleware({ url: known.url! }, response, () => { delegated = true; });
    assert.equal(response.statusCode, 200);
    assert.deepEqual(served, readFileSync(known.sourcePath));
    assert.equal(delegated, false);

    served = undefined;
    middleware(
      { url: "/mujoco/fast_arm/not-declared.bin" },
      response,
      () => { delegated = true; },
    );
    assert.equal(served, undefined);
    assert.equal(delegated, true);
  });

  it("emits every and only decoded public manifest resource", () => {
    const decoded = loadCurrent();
    const expected = new Map(
      decoded.resources.flatMap((resource) =>
        resource.url === null
          ? []
          : [[resource.url.slice(1), readFileSync(resource.sourcePath)] as const],
      ),
    );
    const emitted = new Map<string, unknown>();
    const plugin = createViewerPackageResourcePlugin(repoRoot);
    assert.equal(typeof plugin.generateBundle, "function");
    (plugin.generateBundle as (...args: unknown[]) => unknown).call(
      {
        emitFile(asset: { fileName: string; source: unknown }) {
          emitted.set(asset.fileName, asset.source);
          return asset.fileName;
        },
      },
      {},
      {},
      false,
    );
    assert.deepEqual([...emitted.keys()].sort(), [...expected.keys()].sort());
    for (const [fileName, source] of expected) {
      assert.deepEqual(emitted.get(fileName), source);
    }
  });
});
