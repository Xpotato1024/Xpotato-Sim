import loadMujoco from "@mujoco/mujoco";
import mujocoWasmUrl from "@mujoco/mujoco/mujoco.wasm?url";

let modulePromise: Promise<any> | null = null;
let moduleBuilds = 0;
export function mujocoWasmModuleBuilds(): number { return moduleBuilds; }
export async function loadMujocoWasm(): Promise<any> {
  return modulePromise ??= loadMujoco({
    locateFile: (file: string) => (file === "mujoco.wasm" ? mujocoWasmUrl : file),
  }).then(module => { moduleBuilds += 1; return module; }).catch(error => { modulePromise = null; throw error; });
}
