import type { TransportEndpointEvaluationPayload } from "../types/transportPayload.js";

function formatNumber(value: number, maximumFractionDigits = 4): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(maximumFractionDigits);
}

function formatNumberList(values: readonly number[], maximumFractionDigits = 4): string {
  return `[${values.map((value) => formatNumber(value, maximumFractionDigits)).join(", ")}]`;
}

export function formatEndpointEvaluationVector(values: readonly number[] | null): string {
  return values === null ? "n/a" : formatNumberList(values, 4);
}

export function formatEndpointEvaluationAngles(values: readonly number[] | null): string {
  return values === null ? "n/a" : formatNumberList(values, 4);
}

export function formatEndpointEvaluationScalar(value: number | null): string {
  return value === null ? "n/a" : formatNumber(value, 4);
}

export interface EndpointDifferencePresentation {
  comparable: boolean;
  valueM: number | null;
  vectorM: readonly number[] | null;
  reason: string | null;
}

function finiteVector3(value: unknown): readonly number[] | null {
  return Array.isArray(value) && value.length === 3 &&
    value.every((item) => typeof item === "number" && Number.isFinite(item))
    ? value as readonly number[] : null;
}

export function endpointDifferencePresentation(
  vectorM: unknown, normM: unknown, fromFrame: unknown, toFrame: unknown, frameMismatchNote: unknown,
): EndpointDifferencePresentation {
  const first = typeof fromFrame === "string" && fromFrame.length > 0 ? fromFrame : null;
  const second = typeof toFrame === "string" && toFrame.length > 0 ? toFrame : null;
  if (first === null || second === null) {
    return { comparable: false, valueM: null, vectorM: null, reason: "比較不能: 座標系情報が不足しています。" };
  }
  if (first !== second) {
    const note = typeof frameMismatchNote === "string" && frameMismatchNote.length > 0 ? " " + frameMismatchNote : "";
    return { comparable: false, valueM: null, vectorM: null,
      reason: ("比較不能: " + first + " と " + second + " は同一座標系と確認できません。" + note).trim() };
  }
  const vector = finiteVector3(vectorM);
  const norm = typeof normM === "number" && Number.isFinite(normM) ? normM : null;
  if (vector === null || norm === null) {
    return { comparable: true, valueM: null, vectorM: null, reason: "位置差分を取得できません。" };
  }
  return { comparable: true, valueM: norm, vectorM: vector, reason: null };
}

export function desiredToSiteErrorPresentation(
  endpointEvaluation: TransportEndpointEvaluationPayload | null,
): EndpointDifferencePresentation {
  if (endpointEvaluation === null) {
    return { comparable: false, valueM: null, vectorM: null, reason: "Endpoint evaluation未取得。" };
  }
  return endpointDifferencePresentation(
    endpointEvaluation.desired_to_site_error_vector_m,
    endpointEvaluation.desired_to_site_error_norm_m,
    endpointEvaluation.desired_endpoint_coordinate_frame,
    endpointEvaluation.site_endpoint_coordinate_frame,
    endpointEvaluation.frame_mismatch_note,
  );
}

function formatDifference(value: EndpointDifferencePresentation, unit: string): string {
  if (!value.comparable || value.valueM === null || value.vectorM === null) return value.reason ?? "unavailable";
  return formatEndpointEvaluationVector(value.vectorM) + " |norm| " + formatEndpointEvaluationScalar(value.valueM) + " " + unit;
}

export function formatEndpointEvaluationSummary(
  endpointEvaluation: TransportEndpointEvaluationPayload | null,
): string {
  if (endpointEvaluation === null) {
    return "Endpoint evaluation: unavailable";
  }

  const unit = endpointEvaluation.unit ?? "n/a";
  const desiredToFk = endpointDifferencePresentation(
    endpointEvaluation.desired_to_fk_error_vector_m, endpointEvaluation.desired_to_fk_error_norm_m,
    endpointEvaluation.desired_endpoint_coordinate_frame, endpointEvaluation.fk_endpoint_coordinate_frame,
    endpointEvaluation.frame_mismatch_note,
  );
  const desiredToSite = desiredToSiteErrorPresentation(endpointEvaluation);
  const fkToSite = endpointDifferencePresentation(
    endpointEvaluation.fk_to_site_error_vector_m, endpointEvaluation.fk_to_site_error_norm_m,
    endpointEvaluation.fk_endpoint_coordinate_frame, endpointEvaluation.site_endpoint_coordinate_frame,
    endpointEvaluation.frame_mismatch_note,
  );
  return [
    "Endpoint evaluation",
    "- desired: " + formatEndpointEvaluationVector(endpointEvaluation.desired_endpoint_m ?? null) + " " + unit,
    "- qpos-like joint angles: " + formatEndpointEvaluationAngles(endpointEvaluation.qpos_like_joint_angles_rad ?? null) + " rad",
    "- FK: " + formatEndpointEvaluationVector(endpointEvaluation.fk_endpoint_m ?? null) + " " + unit,
    "- site: " + formatEndpointEvaluationVector(endpointEvaluation.site_endpoint_m ?? null) + " " + unit,
    "- desired -> FK error: " + formatDifference(desiredToFk, unit),
    "- desired -> site error: " + formatDifference(desiredToSite, unit),
    "- FK -> site error: " + formatDifference(fkToSite, unit),
    "- frames:",
    `  desired: ${endpointEvaluation.desired_endpoint_coordinate_frame ?? "n/a"}`,
    `  FK: ${endpointEvaluation.fk_endpoint_coordinate_frame ?? "n/a"}`,
    `  site: ${endpointEvaluation.site_endpoint_coordinate_frame ?? "n/a"}`,
    `- note: ${endpointEvaluation.frame_mismatch_note ?? "n/a"}`,
  ].join("\n");
}
