import assert from "node:assert/strict";
import { parseTransportPayloadV0Message } from "../src/transport/parseTransportPayloadV0Message.js";
import {
  desiredToSiteErrorPresentation,
  endpointDifferencePresentation,
  formatEndpointEvaluationAngles,
  formatEndpointEvaluationScalar,
  formatEndpointEvaluationSummary,
  formatEndpointEvaluationVector,
} from "../src/wasm-scene/endpointEvaluationFormat.js";

const VALID_ENDPOINT_EVALUATION = {
  desired_endpoint_m: [0.6, 0.0, 0.1],
  qpos_like_joint_angles_rad: [0.1, -0.2, 0.0, 0.0],
  fk_endpoint_m: [0.55, 0.0, 0.08],
  site_endpoint_m: [0.62, 0.0, 0.7],
  desired_to_fk_error_vector_m: [-0.05, 0.0, -0.02],
  desired_to_site_error_vector_m: [0.02, 0.0, 0.6],
  fk_to_site_error_vector_m: [0.07, 0.0, 0.62],
  desired_to_fk_error_norm_m: 0.05385164807134504,
  desired_to_site_error_norm_m: 0.6003332407921454,
  fk_to_site_error_norm_m: 0.6239447641967053,
  unit: "meter",
  desired_endpoint_coordinate_frame: "command-side endpoint frame",
  fk_endpoint_coordinate_frame: "solver-defined frame",
  site_endpoint_coordinate_frame: "MuJoCo world / scene frame",
  frame_mismatch_note: "diagnostic only; FK and site endpoints are not transformed or auto-aligned",
};

function testEndpointEvaluationParseKeepsValidPayload(): void {
  const parsed = parseTransportPayloadV0Message(
    JSON.stringify({
      version: 0,
      frame_index: 1,
      time_s: 0.0,
      qpos: [],
      qvel: [],
      bodies: [],
      sites: [],
      metadata: {},
      endpoint_evaluation: VALID_ENDPOINT_EVALUATION,
    }),
  );

  assert.deepEqual(parsed.endpoint_evaluation, VALID_ENDPOINT_EVALUATION);
}

function testEndpointEvaluationParseOmittedFieldStaysOptional(): void {
  const parsed = parseTransportPayloadV0Message(
    JSON.stringify({
      version: 0,
      frame_index: 1,
      time_s: 0.0,
      qpos: [],
      qvel: [],
      bodies: [],
      sites: [],
      metadata: {},
    }),
  );

  assert.equal(parsed.endpoint_evaluation, undefined);
}

function testEndpointEvaluationParseTreatsMalformedFieldAsUnavailable(): void {
  const parsed = parseTransportPayloadV0Message(
    JSON.stringify({
      version: 0,
      frame_index: 1,
      time_s: 0.0,
      qpos: [],
      qvel: [],
      bodies: [],
      sites: [],
      metadata: {},
      endpoint_evaluation: {
        ...VALID_ENDPOINT_EVALUATION,
        desired_to_fk_error_norm_m: "bad",
      },
    }),
  );

  assert.equal(parsed.endpoint_evaluation, null);
}

function testEndpointEvaluationFormattingRoundsCompactly(): void {
  assert.equal(formatEndpointEvaluationVector([0.123456, 1.0, -0.5]), "[0.1235, 1, -0.5000]");
  assert.equal(formatEndpointEvaluationAngles([1.5, -0.25]), "[1.5000, -0.2500]");
  assert.equal(formatEndpointEvaluationScalar(0.05385164807134504), "0.0539");
}

testEndpointEvaluationParseKeepsValidPayload();
testEndpointEvaluationParseOmittedFieldStaysOptional();
testEndpointEvaluationParseTreatsMalformedFieldAsUnavailable();
testEndpointEvaluationFormattingRoundsCompactly();
testEndpointComparisonRequiresMatchingFrames();

function testEndpointComparisonRequiresMatchingFrames(): void {
  const mismatch = desiredToSiteErrorPresentation(VALID_ENDPOINT_EVALUATION);
  assert.equal(mismatch.comparable, false);
  assert.equal(mismatch.valueM, null);
  assert.match(mismatch.reason ?? "", /比較不能/);
  assert.doesNotMatch(formatEndpointEvaluationSummary(VALID_ENDPOINT_EVALUATION), /desired -> site error: \[0\.02/);

  const comparable = endpointDifferencePresentation([0.01, 0, 0], 0.01, "mujoco_world", "mujoco_world", null);
  assert.equal(comparable.comparable, true);
  assert.equal(comparable.valueM, 0.01);
  assert.deepEqual(comparable.vectorM, [0.01, 0, 0]);
  assert.equal(endpointDifferencePresentation([0.01, 0, 0], 0.01, null, "mujoco_world", null).valueM, null);
}

console.log("endpoint evaluation tests passed");
