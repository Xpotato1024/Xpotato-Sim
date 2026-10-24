import assert from "node:assert/strict";
import {
  normalizeTransportEndpointMetadata,
  parseTransportPayloadV0Message,
} from "../src/transport/parseTransportPayloadV0Message.js";
import {
  createViewerWebSocketClient,
  type ViewerWebSocketLike,
  type ViewerWebSocketMessageEventLike,
} from "../src/transport/websocketClient.js";
import type { TransportPayloadV0 } from "../src/types/transportPayload.js";
import {
  buildProductViewerInputOverlayState,
  formatInputOverlayText,
} from "../src/wasm-scene/productViewerState.js";

const TRANSPORT_PAYLOAD_FIXTURE: TransportPayloadV0 = {
  version: 0,
  frame_index: 1,
  time_s: 0.0,
  qpos: [],
  qvel: [],
  bodies: [
    {
      name: "base_link",
      position_m: [0.0, 0.0, 0.0],
      quaternion_wxyz: [1.0, 0.0, 0.0, 0.0],
    },
  ],
  sites: [
    {
      name: "tip",
      position_m: [0.1, 0.2, 0.3],
      quaternion_wxyz: [1.0, 0.0, 0.0, 0.0],
    },
  ],
  target_position_m: null,
  endpoint_evaluation: {
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
  },
  metadata: {},
};

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

const INPUT_DRIVEN_PAYLOAD_FIXTURE_WITH_NULL_TARGET: TransportPayloadV0 = {
  version: 0,
  frame_index: 1,
  time_s: 0.0166666667,
  qpos: [0.0, 0.0, 0.0, 0.0],
  qvel: [0.0, 0.0, 0.0, 0.0],
  bodies: [
    {
      name: "base_link",
      position_m: [0.0, 0.0, 0.0],
      quaternion_wxyz: [1.0, 0.0, 0.0, 0.0],
    },
  ],
  sites: [
    {
      name: "tip",
      position_m: [0.1, 0.0, 0.3],
      quaternion_wxyz: [1.0, 0.0, 0.0, 0.0],
    },
  ],
  target_position_m: null,
  metadata: {
    source_kind: "keyboard",
    desired_endpoint_m: [0.11, 0.0, 0.3],
    endpoint_delta_m: [0.01, 0.0, 0.0],
    current_tip_position_m: [0.1, 0.0, 0.3],
  },
};

const INPUT_DRIVEN_PAYLOAD_FIXTURE_WITH_FEEDBACK_TARGET: TransportPayloadV0 = {
  ...INPUT_DRIVEN_PAYLOAD_FIXTURE_WITH_NULL_TARGET,
  target_position_m: [0.24, 0.5, 0.75],
  metadata: {
    ...INPUT_DRIVEN_PAYLOAD_FIXTURE_WITH_NULL_TARGET.metadata,
    target_position_m: [0.24, 0.5, 0.75],
  },
  endpoint_evaluation: VALID_ENDPOINT_EVALUATION,
};

const INPUT_DRIVEN_PAYLOAD_FIXTURE_WITH_REJECTED_TARGET: TransportPayloadV0 = {
  ...INPUT_DRIVEN_PAYLOAD_FIXTURE_WITH_NULL_TARGET,
  target_position_m: [0.24, 0.5, 0.75],
  metadata: {
    source_kind: "keyboard",
    source_active: true,
    command_age_ms: 22,
    stale_reason: null,
    runtime_input_safety_applied: true,
    target_status: "held",
    target_rejected: true,
    target_rejection_reason: "target_unreachable",
    target_rejection_message: "target_position_m is outside the reachable workspace",
    rejected_desired_endpoint_m: [0.95, 0.5, 0.75],
    target_position_m: [0.24, 0.5, 0.75],
    viewer_control_message: {
      viewer_source_kind: "keyboard",
      sequence: 5,
      keyboard: {
        active_key_codes: ["KeyD"],
        key_state: { KeyD: true },
        focus_state: "focused",
        zero_state: false,
      },
    },
  },
};

const VIEWER_OVERLAY_KEYBOARD_PAYLOAD_FIXTURE: TransportPayloadV0 = {
  ...TRANSPORT_PAYLOAD_FIXTURE,
  metadata: {
    source_kind: "viewer_keyboard",
    source_active: true,
    command_age_ms: 18,
    stale_reason: null,
    viewer_control_message: {
      viewer_source_kind: "keyboard",
      sequence: 4,
      keyboard: {
        active_key_codes: ["KeyW", "KeyD"],
        key_state: {
          KeyW: true,
          KeyD: true,
          KeyS: false,
        },
        focus_state: "focused",
        zero_state: false,
      },
    },
  },
};

const VIEWER_OVERLAY_GAMEPAD_PAYLOAD_FIXTURE: TransportPayloadV0 = {
  ...TRANSPORT_PAYLOAD_FIXTURE,
  metadata: {
    source_kind: "viewer_gamepad",
    source_active: false,
    command_age_ms: 287,
    stale_reason: "command_age_ms_exceeded_timeout_250",
    viewer_control_message: {
      viewer_source_kind: "gamepad",
      sequence: 19,
      gamepad: {
        connected: false,
        index: 2,
        id: "Browser Pad",
        axes: [0.3, -1.2, 0.05],
        buttons: [
          { pressed: true, value: 0.75 },
          { pressed: false, value: 0.0 },
        ],
        stale: true,
        zero_state: true,
      },
    },
  },
};

function assertCondition(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

function assertThrows(fn: () => void, expectedMessage: string): void {
  try {
    fn();
  } catch (error) {
    assertCondition(error instanceof Error, "expected an Error instance");
    assertCondition(
      error.message.includes(expectedMessage),
      `expected "${expectedMessage}" in "${error.message}"`,
    );
    return;
  }

  throw new Error("expected function to throw");
}

function testParseTransportPayloadV0Message(): void {
  const parsed = parseTransportPayloadV0Message(JSON.stringify(TRANSPORT_PAYLOAD_FIXTURE));

  assertCondition(parsed.version === 0, "parsed payload should keep version 0");
  assertCondition(parsed.frame_index === TRANSPORT_PAYLOAD_FIXTURE.frame_index, "frame_index should match fixture");
  assertCondition(parsed.time_s === TRANSPORT_PAYLOAD_FIXTURE.time_s, "time_s should match fixture");
  assertCondition(parsed.qpos.length === TRANSPORT_PAYLOAD_FIXTURE.qpos.length, "qpos should be preserved");
  assertCondition(parsed.bodies.length === TRANSPORT_PAYLOAD_FIXTURE.bodies.length, "bodies should be preserved");
  assertCondition(parsed.sites.length === TRANSPORT_PAYLOAD_FIXTURE.sites.length, "sites should be preserved");
}

function testParseTransportPayloadV0MessageRetainsOfflineLoadcellMetadataWithoutEndpointEvaluation(): void {
  const parsed = parseTransportPayloadV0Message(
    JSON.stringify({
      ...TRANSPORT_PAYLOAD_FIXTURE,
      metadata: {
        desired_endpoint_m: [0.2496233, 0.5009906, 0.751376],
        endpoint_delta_m: [-0.0003767, 0.0009906, 0.001376],
        active_channels: [0, 1, 2],
        current_tip_position_m: [0.25, 0.5, 0.75],
      },
      target_position_m: null,
      endpoint_evaluation: undefined,
    }),
  );

  assertCondition(
    JSON.stringify(parsed.metadata.desired_endpoint_m) === JSON.stringify([0.2496233, 0.5009906, 0.751376]),
    "desired_endpoint_m should be preserved in metadata",
  );
  assertCondition(
    JSON.stringify(parsed.metadata.endpoint_delta_m) === JSON.stringify([-0.0003767, 0.0009906, 0.001376]),
    "endpoint_delta_m should be preserved in metadata",
  );
  assertCondition(
    JSON.stringify(parsed.metadata.active_channels) === JSON.stringify([0, 1, 2]),
    "active_channels should be preserved in metadata",
  );
  assertCondition(
    JSON.stringify(parsed.metadata.current_tip_position_m) === JSON.stringify([0.25, 0.5, 0.75]),
    "current_tip_position_m should be preserved in metadata",
  );
  assertCondition(parsed.target_position_m === null, "target_position_m should stay optional feedback");
  assertCondition(parsed.endpoint_evaluation === undefined, "endpoint_evaluation should remain optional");
}

function testParseTransportPayloadV0MessageRejectsInvalidJson(): void {
  assertThrows(() => parseTransportPayloadV0Message("{not json"), "malformed JSON");
}

function testParseTransportPayloadV0MessageRejectsInvalidVersion(): void {
  const message = JSON.stringify({ ...TRANSPORT_PAYLOAD_FIXTURE, version: 1 });
  assertThrows(() => parseTransportPayloadV0Message(message), "version must be 0");
}

function testParseTransportPayloadV0MessageRejectsMissingRequiredFields(): void {
  const missingFields: Array<keyof TransportPayloadV0> = [
    "frame_index",
    "time_s",
    "qpos",
    "qvel",
    "bodies",
    "sites",
  ];

  for (const field of missingFields) {
    const payload = JSON.parse(JSON.stringify(TRANSPORT_PAYLOAD_FIXTURE)) as Record<string, unknown>;
    delete payload[field];

    assertThrows(
      () => parseTransportPayloadV0Message(JSON.stringify(payload)),
      field === "frame_index"
        ? "frame_index must be a number"
        : field === "time_s"
          ? "time_s must be a number"
          : `${field} must be an array`,
    );
  }
}

function testParseTransportPayloadV0MessagePreservesInputDrivenPayloadWithNullTargetPositionAndMissingEndpointEvaluation(): void {
  const parsed = parseTransportPayloadV0Message(JSON.stringify(INPUT_DRIVEN_PAYLOAD_FIXTURE_WITH_NULL_TARGET));

  assertCondition(
    JSON.stringify(parsed.metadata.desired_endpoint_m) === JSON.stringify([0.11, 0.0, 0.3]),
    "desired_endpoint_m should be preserved in metadata",
  );
  assertCondition(
    JSON.stringify(parsed.metadata.endpoint_delta_m) === JSON.stringify([0.01, 0.0, 0.0]),
    "endpoint_delta_m should be preserved in metadata",
  );
  assertCondition(
    JSON.stringify(parsed.metadata.current_tip_position_m) === JSON.stringify([0.1, 0.0, 0.3]),
    "current_tip_position_m should be preserved in metadata",
  );
  assertCondition(parsed.target_position_m === null, "target_position_m should allow null feedback");
  assertCondition(parsed.endpoint_evaluation === undefined, "endpoint_evaluation should remain optional");
}

function testParseTransportPayloadV0MessagePreservesInputDrivenPayloadWithFeedbackTargetAndEndpointEvaluation(): void {
  const parsed = parseTransportPayloadV0Message(JSON.stringify(INPUT_DRIVEN_PAYLOAD_FIXTURE_WITH_FEEDBACK_TARGET));

  assertCondition(
    JSON.stringify(parsed.metadata.desired_endpoint_m) === JSON.stringify([0.11, 0.0, 0.3]),
    "desired_endpoint_m should be preserved in metadata",
  );
  assertCondition(
    JSON.stringify(parsed.metadata.target_position_m) === JSON.stringify([0.24, 0.5, 0.75]),
    "target_position_m should be preserved in metadata as feedback",
  );
  assertCondition(
    JSON.stringify(parsed.target_position_m) === JSON.stringify([0.24, 0.5, 0.75]),
    "target_position_m should stay parseable as feedback",
  );
  assertCondition(parsed.endpoint_evaluation !== null && parsed.endpoint_evaluation !== undefined, "endpoint_evaluation should parse");
  assertCondition(
    JSON.stringify(parsed.endpoint_evaluation) === JSON.stringify(VALID_ENDPOINT_EVALUATION),
    "endpoint_evaluation should preserve the diagnostic payload",
  );
}

function testBuildProductViewerInputOverlayStateFormatsKeyboardPayload(): void {
  const payload = parseTransportPayloadV0Message(JSON.stringify(VIEWER_OVERLAY_KEYBOARD_PAYLOAD_FIXTURE));
  const overlay = buildProductViewerInputOverlayState(payload);

  assertCondition(overlay !== null, "overlay should parse");
  const { endpointPresentation, ...legacyOverlay } = overlay;
  assert.equal(endpointPresentation.requested.desiredEndpointM, null);
  assert.deepEqual(legacyOverlay, {
    sourceKind: "viewer_keyboard",
    rawSignal: null,
    deviceNormalized: null,
    gamepadInstrumentAxes: [],
    gamepadInstrumentPressedButtons: null,
    intentKind: null,
    inputContinuity: null,
    sourceActive: true,
    commandAgeMs: 18,
    staleReason: null,
    viewerSourceKind: "keyboard",
    sequence: 4,
    axisValues: [],
    localEndpointSpeedMS: null,
    localEndpointMaxDeltaM: null,
    endpointVelocityMS: [],
    endpointDeltaM: [],
    runtimeInputSafetyApplied: null,
    targetStatus: null,
    targetRejected: null,
    targetRejectionReason: null,
    targetRejectionMessage: null,
    localMotionPolicy: null,
    motionStatus: null,
    motionRejectionReason: null,
    qposDeltaNormRad: null,
    rejectedDesiredEndpointM: null,
    lastValidTargetPositionM: null,
    endpointEvaluationState: "available",
    endpointEvaluationUnavailableReason: null,
    keyboardActiveKeyCodes: ["KeyW", "KeyD"],
    keyboardFocusState: "focused",
    keyboardZeroState: false,
    keyboardKeyState: {
      KeyW: true,
      KeyD: true,
      KeyS: false,
    },
    gamepadConnected: null,
    gamepadIndex: null,
    gamepadId: null,
    gamepadAxes: [],
    gamepadButtons: [],
    gamepadStale: null,
    gamepadZeroState: null,
  });

  assert.match(formatInputOverlayText(overlay), /input source: viewer_keyboard/);
  assert.match(formatInputOverlayText(overlay), /keyboard active keys: KeyW, KeyD/);
  assert.match(formatInputOverlayText(overlay), /gamepad axes: none/);
}

function testBuildProductViewerInputOverlayStateFormatsGamepadPayloadAndFallsBackSafely(): void {
  const payload = parseTransportPayloadV0Message(JSON.stringify(VIEWER_OVERLAY_GAMEPAD_PAYLOAD_FIXTURE));
  const overlay = buildProductViewerInputOverlayState(payload);

  assertCondition(overlay !== null, "overlay should parse");
  const { endpointPresentation, ...legacyOverlay } = overlay;
  assert.equal(endpointPresentation.status.stale, true);
  assert.deepEqual(legacyOverlay, {
    sourceKind: "viewer_gamepad",
    rawSignal: null,
    deviceNormalized: null,
    gamepadInstrumentAxes: [],
    gamepadInstrumentPressedButtons: [0],
    intentKind: null,
    inputContinuity: null,
    sourceActive: false,
    commandAgeMs: 287,
    staleReason: "command_age_ms_exceeded_timeout_250",
    viewerSourceKind: "gamepad",
    sequence: 19,
    axisValues: [],
    localEndpointSpeedMS: null,
    localEndpointMaxDeltaM: null,
    endpointVelocityMS: [],
    endpointDeltaM: [],
    runtimeInputSafetyApplied: null,
    targetStatus: null,
    targetRejected: null,
    targetRejectionReason: null,
    targetRejectionMessage: null,
    localMotionPolicy: null,
    motionStatus: null,
    motionRejectionReason: null,
    qposDeltaNormRad: null,
    rejectedDesiredEndpointM: null,
    lastValidTargetPositionM: null,
    endpointEvaluationState: "available",
    endpointEvaluationUnavailableReason: null,
    keyboardActiveKeyCodes: [],
    keyboardFocusState: null,
    keyboardZeroState: null,
    keyboardKeyState: {},
    gamepadConnected: false,
    gamepadIndex: 2,
    gamepadId: "Browser Pad",
    gamepadAxes: [0.3, -1.2, 0.05],
    gamepadButtons: [
      { pressed: true, value: 0.75 },
      { pressed: false, value: 0 },
    ],
    gamepadStale: true,
    gamepadZeroState: true,
  });

  const formatted = formatInputOverlayText(overlay);
  assert.match(formatted, /input source: viewer_gamepad/);
  assert.match(formatted, /active: no/);
  assert.match(formatted, /gamepad axes: \[0\.3000, -1\.2000, 0\.0500\]/);
  assert.match(formatted, /gamepad buttons: 0:pressed 0\.75, 1:released 0\.00/);
}

function testBuildProductViewerInputOverlayStateFallsBackSafelyWhenMetadataIsMalformed(): void {
  const payload = parseTransportPayloadV0Message(
    JSON.stringify({
      ...TRANSPORT_PAYLOAD_FIXTURE,
      metadata: {
        source_kind: 123,
        source_active: "no",
        command_age_ms: -5,
        stale_reason: 42,
        viewer_control_message: {
          viewer_source_kind: 99,
          sequence: "bad",
          keyboard: {
            active_key_codes: ["KeyA", 99],
            key_state: {
              KeyA: true,
              KeyB: "no",
            },
            focus_state: 123,
            zero_state: "no",
          },
          gamepad: {
            connected: "no",
            index: "bad",
            id: 42,
            axes: [0.5, "bad", Infinity],
            buttons: [{ pressed: true, value: "bad" }],
            stale: "no",
            zero_state: "no",
          },
        },
      },
    }),
  );
  const overlay = buildProductViewerInputOverlayState(payload);

  assertCondition(overlay !== null, "overlay should still be created");
  assert.equal(overlay.sourceKind, "n/a");
  assert.equal(overlay.sourceActive, false);
  assert.equal(overlay.commandAgeMs, null);
  assert.equal(overlay.staleReason, null);
  assert.equal(overlay.viewerSourceKind, null);
  assert.equal(overlay.sequence, null);
  assert.equal(overlay.runtimeInputSafetyApplied, null);
  assert.equal(overlay.targetStatus, null);
  assert.equal(overlay.targetRejected, null);
  assert.equal(overlay.targetRejectionReason, null);
  assert.equal(overlay.targetRejectionMessage, null);
  assert.equal(overlay.rejectedDesiredEndpointM, null);
  assert.equal(overlay.lastValidTargetPositionM, null);
  assert.equal(overlay.endpointEvaluationState, "available");
  assert.equal(overlay.endpointEvaluationUnavailableReason, null);
  assert.deepEqual(overlay.keyboardActiveKeyCodes, ["KeyA"]);
  assert.deepEqual(overlay.keyboardKeyState, {
    KeyA: true,
  });
  assert.equal(overlay.keyboardFocusState, null);
  assert.equal(overlay.keyboardZeroState, null);
  assert.equal(overlay.gamepadConnected, null);
  assert.equal(overlay.gamepadIndex, null);
  assert.equal(overlay.gamepadId, null);
  assert.equal(overlay.gamepadAxes, null);
  assert.match(formatInputOverlayText(overlay), /gamepad axes: unavailable \/ invalid/);
  assert.deepEqual(overlay.gamepadButtons, [{ pressed: true, value: null }]);
  assert.equal(overlay.gamepadStale, null);
  assert.equal(overlay.gamepadZeroState, null);
}

function testEndpointMetadataParserHandlesNullMalformedAndUnknownFieldsDeterministically(): void {
  const normalized = normalizeTransportEndpointMetadata({
    current_tip_position_m: [0.1, 0.2, 0.3],
    target_position_m: [0.4, 0.5, 0.6],
    desired_endpoint_m: null,
    endpoint_delta_m: [0.1, "bad", 0.3],
    unknown_metadata_field: { preserved: true },
  });

  assert.deepEqual(normalized.current_tip_position_m, [0.1, 0.2, 0.3]);
  assert.deepEqual(normalized.target_position_m, [0.4, 0.5, 0.6]);
  assert.equal(normalized.desired_endpoint_m, undefined);
  assert.equal(normalized.endpoint_delta_m, undefined);
  assert.deepEqual(normalized.unknown_metadata_field, { preserved: true });

  assert.equal(
    normalizeTransportEndpointMetadata({ target_position_m: null }).target_position_m,
    undefined,
  );
  assert.equal(
    normalizeTransportEndpointMetadata({ target_position_m: [0.1, 0.2] }).target_position_m,
    undefined,
  );
  assert.equal(
    normalizeTransportEndpointMetadata({ target_position_m: [0.1, Number.POSITIVE_INFINITY, 0.3] })
      .target_position_m,
    undefined,
  );

  const nullMetadataPayload = parseTransportPayloadV0Message(
    JSON.stringify({ ...TRANSPORT_PAYLOAD_FIXTURE, metadata: null }),
  );
  const nonRecordMetadataPayload = parseTransportPayloadV0Message(
    JSON.stringify({ ...TRANSPORT_PAYLOAD_FIXTURE, metadata: ["legacy"] }),
  );
  assert.deepEqual(nullMetadataPayload.metadata, {});
  assert.deepEqual(nonRecordMetadataPayload.metadata, {});
  assert.equal(nullMetadataPayload.target_position_m, null);
}

function testBuildProductViewerInputOverlayStateHandlesEmptyMetadataAndMissingEndpointEvaluation(): void {
  const payload = parseTransportPayloadV0Message(
    JSON.stringify({
      ...TRANSPORT_PAYLOAD_FIXTURE,
      metadata: {},
      endpoint_evaluation: undefined,
      target_position_m: null,
    }),
  );
  const overlay = buildProductViewerInputOverlayState(payload);

  assertCondition(overlay !== null, "overlay should still be created");
  assert.equal(overlay.sourceKind, "n/a");
  assert.equal(overlay.runtimeInputSafetyApplied, null);
  assert.equal(overlay.targetStatus, null);
  assert.equal(overlay.targetRejected, null);
  assert.equal(overlay.targetRejectionReason, null);
  assert.equal(overlay.targetRejectionMessage, null);
  assert.equal(overlay.rejectedDesiredEndpointM, null);
  assert.equal(overlay.lastValidTargetPositionM, null);
  assert.equal(overlay.endpointEvaluationState, "missing");
  assert.equal(overlay.endpointEvaluationUnavailableReason, "endpoint_evaluation missing from payload");
}

function testBuildProductViewerInputOverlayStateMarksMalformedEndpointEvaluationAsUnavailable(): void {
  const payload = parseTransportPayloadV0Message(
    JSON.stringify({
      ...TRANSPORT_PAYLOAD_FIXTURE,
      endpoint_evaluation: {
        ...VALID_ENDPOINT_EVALUATION,
        desired_to_fk_error_norm_m: "bad",
      },
    }),
  );
  const overlay = buildProductViewerInputOverlayState(payload);

  assertCondition(overlay !== null, "overlay should still be created");
  assert.equal(overlay.endpointEvaluationState, "malformed");
  assert.equal(overlay.endpointEvaluationUnavailableReason, "endpoint_evaluation present but failed validation");
}

function testBuildProductViewerInputOverlayStateShowsRejectedTargetDiagnosticsAndClearsOnAcceptedPayload(): void {
  const rejectedPayload = parseTransportPayloadV0Message(JSON.stringify(INPUT_DRIVEN_PAYLOAD_FIXTURE_WITH_REJECTED_TARGET));
  const rejectedOverlay = buildProductViewerInputOverlayState(rejectedPayload);

  assertCondition(rejectedOverlay !== null, "rejected overlay should parse");
  assert.equal(rejectedOverlay.runtimeInputSafetyApplied, true);
  assert.equal(rejectedOverlay.targetStatus, "held");
  assert.equal(rejectedOverlay.targetRejected, true);
  assert.equal(rejectedOverlay.targetRejectionReason, "target_unreachable");
  assert.equal(
    rejectedOverlay.targetRejectionMessage,
    "target_position_m is outside the reachable workspace",
  );
  assert.deepEqual(rejectedOverlay.rejectedDesiredEndpointM, [0.95, 0.5, 0.75]);
  assert.deepEqual(rejectedOverlay.lastValidTargetPositionM, [0.24, 0.5, 0.75]);
  assert.equal(rejectedOverlay.endpointEvaluationState, "missing");
  assert.equal(
    rejectedOverlay.endpointEvaluationUnavailableReason,
    "endpoint_evaluation withheld on rejected target",
  );
  assert.match(formatInputOverlayText(rejectedOverlay), /target rejection reason: target_unreachable/);
  assert.match(
    formatInputOverlayText(rejectedOverlay),
    /target rejection message: target_position_m is outside the reachable workspace/,
  );
  assert.match(formatInputOverlayText(rejectedOverlay), /last valid target_m: \[0\.2400, 0\.5000, 0\.7500\]/);

  const acceptedPayload = parseTransportPayloadV0Message(JSON.stringify(INPUT_DRIVEN_PAYLOAD_FIXTURE_WITH_FEEDBACK_TARGET));
  const acceptedOverlay = buildProductViewerInputOverlayState(acceptedPayload);

  assertCondition(acceptedOverlay !== null, "accepted overlay should parse");
  assert.equal(acceptedOverlay.runtimeInputSafetyApplied, null);
  assert.equal(acceptedOverlay.targetStatus, "accepted");
  assert.equal(acceptedOverlay.targetRejected, null);
  assert.equal(acceptedOverlay.targetRejectionReason, null);
  assert.equal(acceptedOverlay.targetRejectionMessage, null);
  assert.equal(acceptedOverlay.rejectedDesiredEndpointM, null);
  assert.deepEqual(acceptedOverlay.lastValidTargetPositionM, [0.24, 0.5, 0.75]);
  assert.equal(acceptedOverlay.endpointEvaluationState, "available");
  assert.equal(acceptedOverlay.endpointEvaluationUnavailableReason, null);
  assert.match(formatInputOverlayText(acceptedOverlay), /target rejected: none/);
  assert.match(formatInputOverlayText(acceptedOverlay), /target status: accepted/);
  assert.match(formatInputOverlayText(acceptedOverlay), /endpoint evaluation: available/);
}

class FakeWebSocket implements ViewerWebSocketLike {
  public readonly messageListeners: Array<(event: ViewerWebSocketMessageEventLike) => void> = [];
  public readonly openListeners: Array<(event: Event) => void> = [];
  public readonly closeListeners: Array<(event: Event) => void> = [];
  public readonly errorListeners: Array<(event: Event) => void> = [];
  public closed = false;

  constructor(public readonly url: string) {}

  addEventListener(
    type: "message",
    listener: (event: ViewerWebSocketMessageEventLike) => void,
  ): void;
  addEventListener(type: "open", listener: (event: Event) => void): void;
  addEventListener(type: "close", listener: (event: Event) => void): void;
  addEventListener(type: "error", listener: (event: Event) => void): void;
  addEventListener(
    type: "message" | "open" | "close" | "error",
    listener:
      | ((event: ViewerWebSocketMessageEventLike) => void)
      | ((event: Event) => void),
  ): void {
    if (type === "message") {
      this.messageListeners.push(listener as (event: ViewerWebSocketMessageEventLike) => void);
      return;
    }

    if (type === "open") {
      this.openListeners.push(listener as (event: Event) => void);
      return;
    }

    if (type === "close") {
      this.closeListeners.push(listener as (event: Event) => void);
      return;
    }

    this.errorListeners.push(listener as (event: Event) => void);
  }

  removeEventListener(
    type: "message",
    listener: (event: ViewerWebSocketMessageEventLike) => void,
  ): void;
  removeEventListener(type: "open", listener: (event: Event) => void): void;
  removeEventListener(type: "close", listener: (event: Event) => void): void;
  removeEventListener(type: "error", listener: (event: Event) => void): void;
  removeEventListener(
    type: "message" | "open" | "close" | "error",
    listener:
      | ((event: ViewerWebSocketMessageEventLike) => void)
      | ((event: Event) => void),
  ): void {
    if (type === "message") {
      const index = this.messageListeners.indexOf(listener as (event: ViewerWebSocketMessageEventLike) => void);
      if (index >= 0) {
        this.messageListeners.splice(index, 1);
      }
      return;
    }

    if (type === "open") {
      const index = this.openListeners.indexOf(listener as (event: Event) => void);
      if (index >= 0) {
        this.openListeners.splice(index, 1);
      }
      return;
    }

    if (type === "close") {
      const index = this.closeListeners.indexOf(listener as (event: Event) => void);
      if (index >= 0) {
        this.closeListeners.splice(index, 1);
      }
      return;
    }

    const index = this.errorListeners.indexOf(listener as (event: Event) => void);
    if (index >= 0) {
      this.errorListeners.splice(index, 1);
    }
  }

  close(): void {
    this.closed = true;
  }

  dispatchMessage(data: unknown): void {
    for (const listener of this.messageListeners) {
      listener({ data });
    }
  }

  dispatchOpen(): void {
    for (const listener of this.openListeners) {
      listener(new Event("open"));
    }
  }

  dispatchClose(): void {
    for (const listener of this.closeListeners) {
      listener(new Event("close"));
    }
  }

  dispatchError(): void {
    for (const listener of this.errorListeners) {
      listener(new Event("error"));
    }
  }
}

function testViewerWebSocketClientRoutesMalformedMessageToErrorCallback(): void {
  const payloads: TransportPayloadV0[] = [];
  const errors: Error[] = [];
  let socket: FakeWebSocket | null = null;

  class InjectedFakeWebSocketCtor extends FakeWebSocket {
    constructor(url: string) {
      super(url);
      socket = this;
    }
  }

  const client = createViewerWebSocketClient({
    url: "ws://example.test/payload",
    WebSocketCtor: InjectedFakeWebSocketCtor,
    onPayload(payload) {
      payloads.push(payload);
    },
    onPayloadError(error) {
      errors.push(error);
    },
  });

  client.start();
  assertCondition(socket !== null, "websocket should be created");
  const activeSocket = socket as FakeWebSocket;
  activeSocket.dispatchMessage("{not json");

  assertCondition(payloads.length === 0, "malformed message should not produce payload");
  assertCondition(errors.length === 1, "malformed message should produce one error");
  assertCondition(errors[0].message.includes("malformed JSON"), "error should mention malformed JSON");
  client.stop();
}

function testViewerWebSocketClientDeliversValidPayloadThroughInjectedSocket(): void {
  const payloads: TransportPayloadV0[] = [];
  const errors: Error[] = [];
  let socket: FakeWebSocket | null = null;

  class InjectedFakeWebSocketCtor extends FakeWebSocket {
    constructor(url: string) {
      super(url);
      socket = this;
    }
  }

  const client = createViewerWebSocketClient({
    url: "ws://example.test/payload",
    WebSocketCtor: InjectedFakeWebSocketCtor,
    onPayload(payload) {
      payloads.push(payload);
    },
    onPayloadError(error) {
      errors.push(error);
    },
  });

  client.start();
  assertCondition(socket !== null, "websocket should be created");
  const activeSocket = socket as FakeWebSocket;
  activeSocket.dispatchMessage(JSON.stringify(TRANSPORT_PAYLOAD_FIXTURE));

  assertCondition(payloads.length === 1, "valid payload should be delivered once");
  assertCondition(payloads[0].version === 0, "delivered payload should keep version 0");
  assertCondition(
    payloads[0].frame_index === TRANSPORT_PAYLOAD_FIXTURE.frame_index,
    "delivered payload should preserve frame_index",
  );
  assertCondition(
    client.getLatestPayload()?.frame_index === TRANSPORT_PAYLOAD_FIXTURE.frame_index,
    "client should keep the latest payload in state",
  );
  assertCondition(
    client.getLatestPayload()?.endpoint_evaluation?.desired_endpoint_m?.[0] ===
      TRANSPORT_PAYLOAD_FIXTURE.endpoint_evaluation?.desired_endpoint_m?.[0],
    "client should preserve endpoint evaluation",
  );
  assertCondition(errors.length === 0, "valid payload should not produce errors");

  client.stop();
  assertCondition(socket !== null, "websocket should be created");
  assertCondition(activeSocket.closed, "client.stop should close the socket");
}

function testViewerWebSocketClientReportsMonotonicParseObservation(): void {
  let socket: FakeWebSocket | null = null;
  const observations: Array<{ receivedAtMs: number; parseDurationMs: number }> = [];
  const times = [10, 10.4];

  class InjectedFakeWebSocketCtor extends FakeWebSocket {
    constructor(url: string) {
      super(url);
      socket = this;
    }
  }

  const client = createViewerWebSocketClient({
    url: "ws://example.test/payload",
    WebSocketCtor: InjectedFakeWebSocketCtor,
    monotonicNow() {
      const value = times.shift();
      if (value === undefined) {
        throw new Error("unexpected monotonic clock read");
      }
      return value;
    },
    onPayload(_payload, observation) {
      observations.push(observation);
    },
  });

  client.start();
  assertCondition(socket !== null, "websocket should be created");
  (socket as FakeWebSocket).dispatchMessage(JSON.stringify(TRANSPORT_PAYLOAD_FIXTURE));
  assert.deepEqual(observations, [{ receivedAtMs: 10, parseDurationMs: 0.40000000000000036 }]);
  client.stop();
}

function testViewerWebSocketClientRoutesSocketErrorsToErrorCallback(): void {
  const errors: Error[] = [];
  let socket: FakeWebSocket | null = null;

  class InjectedFakeWebSocketCtor extends FakeWebSocket {
    constructor(url: string) {
      super(url);
      socket = this;
    }
  }

  const client = createViewerWebSocketClient({
    url: "ws://example.test/payload",
    WebSocketCtor: InjectedFakeWebSocketCtor,
    onConnectionError(error) {
      if (error instanceof Error) {
        errors.push(error);
        return;
      }

      errors.push(new Error("connection error event"));
    },
    onOpen() {
      errors.push(new Error("open"));
    },
    onClose() {
      errors.push(new Error("close"));
    },
  });

  client.start();
  assertCondition(socket !== null, "websocket should be created");
  const activeSocket = socket as FakeWebSocket;
  activeSocket.dispatchOpen();
  activeSocket.dispatchError();
  activeSocket.dispatchClose();

  assertCondition(errors.length === 3, "socket lifecycle events should be routed");
  assertCondition(errors[0].message === "open", "open event should be routed");
  assertCondition(errors[1].message.includes("connection error"), "socket error should mention connection error");
  assertCondition(errors[2].message === "close", "close event should be routed");
  client.stop();
}

testParseTransportPayloadV0Message();
testParseTransportPayloadV0MessageRetainsOfflineLoadcellMetadataWithoutEndpointEvaluation();
testParseTransportPayloadV0MessagePreservesInputDrivenPayloadWithNullTargetPositionAndMissingEndpointEvaluation();
testParseTransportPayloadV0MessagePreservesInputDrivenPayloadWithFeedbackTargetAndEndpointEvaluation();
testBuildProductViewerInputOverlayStateFormatsKeyboardPayload();
testBuildProductViewerInputOverlayStateFormatsGamepadPayloadAndFallsBackSafely();
testBuildProductViewerInputOverlayStateFallsBackSafelyWhenMetadataIsMalformed();
testEndpointMetadataParserHandlesNullMalformedAndUnknownFieldsDeterministically();
testBuildProductViewerInputOverlayStateHandlesEmptyMetadataAndMissingEndpointEvaluation();
testBuildProductViewerInputOverlayStateMarksMalformedEndpointEvaluationAsUnavailable();
testBuildProductViewerInputOverlayStateShowsRejectedTargetDiagnosticsAndClearsOnAcceptedPayload();
testParseTransportPayloadV0MessageRejectsInvalidJson();
testParseTransportPayloadV0MessageRejectsInvalidVersion();
testParseTransportPayloadV0MessageRejectsMissingRequiredFields();
testViewerWebSocketClientDeliversValidPayloadThroughInjectedSocket();
testViewerWebSocketClientReportsMonotonicParseObservation();
testViewerWebSocketClientRoutesMalformedMessageToErrorCallback();
testViewerWebSocketClientRoutesSocketErrorsToErrorCallback();

console.log("websocket client tests passed");
