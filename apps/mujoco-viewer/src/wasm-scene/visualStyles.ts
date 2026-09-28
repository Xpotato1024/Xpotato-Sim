import type {
  ViewerBodyVisualStyle,
  ViewerRobotProfile,
} from "../robot-profiles/types.js";

export type BodyVisualStyle = ViewerBodyVisualStyle;

export function resolveBodyVisualStyle(
  profile: ViewerRobotProfile,
  bodyName: string,
  meshName: string,
  geomName: string,
): BodyVisualStyle | null {
  for (const candidate of [bodyName, meshName, geomName]) {
    const normalized = candidate.replace(/[^a-z0-9]/gi, "").toLowerCase();
    if (normalized === "") {
      continue;
    }
    const styleKey = profile.visualStyleSelection.get(normalized);
    if (styleKey !== undefined) {
      return profile.bodyVisualStyles[styleKey] ?? null;
    }
  }
  return null;
}

export function viewerVisualLegend(profile: ViewerRobotProfile): readonly ViewerBodyVisualStyle[] {
  return Object.freeze([
    ...Object.values(profile.bodyVisualStyles),
    ...profile.axisVisualStyles,
  ]);
}


/** meshの既存Robot配色を維持し、primitiveの外観は生成MJCFのRGBAを正とする。 */
export function resolveGeomDisplayColor(style: BodyVisualStyle | null, rgba: readonly number[], mesh: boolean): string | [number,number,number] {
  if (rgba.length !== 4 || !rgba.every(v => Number.isFinite(v) && v >= 0 && v <= 1)) throw new Error("invalid native geom RGBA");
  if (mesh && style !== null) return style.color;
  return [rgba[0],rgba[1],rgba[2]];
}
