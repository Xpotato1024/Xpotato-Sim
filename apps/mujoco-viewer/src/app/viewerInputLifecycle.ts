import {
  createDefaultViewerInputProviderRegistry,
  type ViewerInputProvider,
  type ViewerInputProviderDocumentLike,
  type ViewerInputProviderId,
  type ViewerInputProviderOptions,
  type ViewerInputProviderRegistry,
  type ViewerInputProviderWindowLike,
} from "../input/viewerInputProvider.js";

export type ViewerKeyboardEventLike = import("../input/viewerInputProvider.js").ViewerKeyboardEventLike;
export type ViewerInputLifecycleWindowLike = ViewerInputProviderWindowLike;
export type ViewerInputLifecycleDocumentLike = ViewerInputProviderDocumentLike;

export interface ViewerInputLifecycleOptions extends ViewerInputProviderOptions {
  providerRegistry?: ViewerInputProviderRegistry;
  providerIds?: readonly ViewerInputProviderId[];
}

export interface ViewerInputLifecycle {
  setLiveInputEnabled(enabled: boolean): void;
  dispose(): void;
}

const DEFAULT_PROVIDER_IDS: readonly ViewerInputProviderId[] = ["gamepad/v1", "keyboard/v1"];

/** URLは入力取得の選択だけを渡す。未知/重複指定では取得を開始しない。 */
export function readViewerInputSelection(search: string): {
  providerIds: readonly ViewerInputProviderId[];
  error: string | null;
} {
  const values = new URLSearchParams(search).getAll("inputProvider");
  if (values.length === 0) return { providerIds: DEFAULT_PROVIDER_IDS, error: null };
  if (values.length === 1 && values[0] === "none") return { providerIds: [], error: null };
  if (values.length === 1 && (values[0] === "keyboard/v1" || values[0] === "gamepad/v1")) {
    return { providerIds: [values[0]], error: null };
  }
  return { providerIds: [], error: "入力providerの指定が不正です。取得を開始しません。" };
}

/** 起動直後の入力開始条件。Robot名から制御方式を推測しない。 */
export function readViewerInputStartup(search: string): "connection" | "scene" | "invalid" {
  const values = new URLSearchParams(search).getAll("inputStartup");
  if (values.length === 0) return "connection";
  return values.length === 1 && values[0] === "scene" ? "scene" : "invalid";
}

export function createViewerInputLifecycle(options: ViewerInputLifecycleOptions): ViewerInputLifecycle {
  const registry = options.providerRegistry ?? createDefaultViewerInputProviderRegistry();
  const providerIds = options.providerIds ?? DEFAULT_PROVIDER_IDS;
  const providers: ViewerInputProvider[] = [];
  let liveInputEnabled = false;
  let active = false;

  const disposeActiveInputs = (): void => {
    if (!active) return;
    active = false;
    for (const provider of [...providers].reverse()) provider.dispose();
    providers.length = 0;
  };

  const activateInputs = (): void => {
    if (active || !liveInputEnabled) return;
    if (new Set(providerIds).size !== providerIds.length) {
      throw new Error("duplicate viewer input provider selection");
    }

    const created: ViewerInputProvider[] = [];
    try {
      for (const id of providerIds) created.push(registry.create(id, options));
      for (const provider of created) provider.start();
    } catch (error) {
      for (const provider of [...created].reverse()) provider.dispose();
      throw error;
    }
    providers.push(...created);
    active = true;
  };

  return {
    setLiveInputEnabled(enabled): void {
      liveInputEnabled = enabled;
      if (enabled) activateInputs();
      else disposeActiveInputs();
    },
    dispose(): void {
      liveInputEnabled = false;
      disposeActiveInputs();
    },
  };
}
