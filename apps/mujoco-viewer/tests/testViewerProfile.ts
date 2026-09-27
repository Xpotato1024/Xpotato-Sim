import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { decodeViewerRobotDeclaration } from "../src/robot-profiles/declaration.js";

export const FAST_ARM_VIEWER_DECLARATION_DOCUMENT = JSON.parse(
  readFileSync(
    resolve(
      process.cwd(),
      "..",
      "..",
      "src",
      "xpotato_sim",
      "plugins",
      "robots",
      "fast_arm",
      "adapter",
      "resources",
      "viewer-profile.json",
    ),
    "utf8",
  ),
) as Record<string, unknown>;

export const FAST_ARM_VIEWER_PROFILE = decodeViewerRobotDeclaration(
  FAST_ARM_VIEWER_DECLARATION_DOCUMENT,
);
