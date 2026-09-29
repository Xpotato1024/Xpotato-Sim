import assert from "node:assert/strict";
import { createAsyncResourceLifetime } from "../src/wasm-scene/asyncResourceLifetime.js";

{
  let releases = 0;
  const lifetime = createAsyncResourceLifetime(() => { releases += 1; });
  lifetime.requestDispose();
  lifetime.requestDispose();
  assert.equal(lifetime.disposeRequested, true);
  assert.equal(releases, 1);
  await assert.rejects(() => lifetime.run(async () => 1), /disposing/);
}

{
  let releases = 0;
  let finish: () => void = () => {};
  const gate = new Promise<void>((resolve) => { finish = resolve; });
  const lifetime = createAsyncResourceLifetime(() => { releases += 1; });
  const pending = lifetime.run(async () => { await gate; return 7; });
  lifetime.requestDispose();
  assert.equal(releases, 0, "active async work must retain resources");
  finish();
  assert.equal(await pending, 7);
  assert.equal(releases, 1);
}

{
  let releases = 0;
  let rejectOperation: (reason?: unknown) => void = () => {};
  const gate = new Promise<void>((_, reject) => { rejectOperation = reject; });
  const lifetime = createAsyncResourceLifetime(() => { releases += 1; });
  const pending = lifetime.run(async () => { await gate; });
  lifetime.requestDispose();
  rejectOperation(new Error("compile failed"));
  await assert.rejects(pending, /compile failed/);
  assert.equal(releases, 1, "rejected async work must still release exactly once");
}

console.log("async resource lifetime disposal ordering checks passed");
