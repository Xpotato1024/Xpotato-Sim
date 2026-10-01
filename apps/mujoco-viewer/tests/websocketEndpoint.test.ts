import assert from "node:assert/strict";
import { readViewerEndpointConfig } from "../src/config/websocketEndpoint.js";

const canonical = readViewerEndpointConfig({ search: "?websocketUrl=ws://127.0.0.1:8766" });
assert.equal(canonical.websocketUrl, "ws://127.0.0.1:8766");
assert.equal(canonical.source, "query");
assert.equal(canonical.error, undefined);

for (const search of [
  "?ws=ws://127.0.0.1:8766", "?ws=", "?ws", "?%77s=ws://127.0.0.1:8766",
  "?websocketUrl=ws://127.0.0.1:8766&ws=ws://127.0.0.1:8767",
  "?ws=&websocketUrl=ws://127.0.0.1:8766", "?ws=&ws=ws://127.0.0.1:8766",
]) {
  const rejected = readViewerEndpointConfig({ search });
  assert.equal(rejected.websocketUrl, null, "旧指定では接続しない");
  assert.equal(rejected.source, "rejected", "旧指定を未指定へ読み替えない");
  assert.match(rejected.error ?? "", /廃止.*websocketUrl/, "退役理由と移行先を示す");
}

const missing = readViewerEndpointConfig({ search: "" });
assert.equal(missing.websocketUrl, null);
assert.equal(missing.source, "disabled");
assert.equal(missing.error, undefined, "本当に未指定なら静的Viewerを許可する");

const malformed = readViewerEndpointConfig({ search: "?websocketUrl=not-a-websocket-url" });
assert.equal(malformed.websocketUrl, null);
assert.equal(malformed.source, "disabled");

const secure = readViewerEndpointConfig({ search: "?websocketUrl=wss://example.test/viewer" });
assert.equal(secure.websocketUrl, "wss://example.test/viewer");
assert.equal(secure.error, undefined);
console.log("websocket endpoint and retired-query rejection tests passed");
