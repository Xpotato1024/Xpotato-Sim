import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { resolve } from "node:path";

describe("vite config", () => {
  it("keeps manual dev opening and delegates app browser ownership", () => {
    const source = readFileSync(resolve(process.cwd(), "vite.config.ts"), "utf8");
    assert.ok(source.includes('open: process.env.XPOTATO_SIM_LAUNCHER === "1" ? false : "/apps/mujoco-viewer/"'));
    assert.ok(source.includes('cacheDir: resolve(appRoot, "node_modules/.vite")'));
    assert.match(source, /publicDir:\s*false/);
    assert.match(source, /createViewerPackageResourcePlugin\(repoRoot\)/);
    assert.doesNotMatch(source, /fastArmPackageResources/);
  });
});
