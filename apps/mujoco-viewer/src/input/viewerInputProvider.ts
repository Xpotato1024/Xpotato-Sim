/**
 * browser input acquisitionをprovider identityごとに管理する。
 * key/gamepadの解釈とcommand生成はbackend Mappingが所有し、ここでは行わない。
 */
import {
  createViewerKeyboardControlSender,
  type ViewerKeyboardCapture,
  type ViewerKeyboardControlSender,
  type ViewerKeyboardControlSocketConstructorLike,
} from "./keyboardInput.js";
import {
  createViewerGamepadControlSender,
  type ViewerGamepadControlSender,
  type ViewerGamepadControlSocketConstructorLike,
  type ViewerGamepadLike,
} from "./gamepadInput.js";
import {
  createViewerGamepadLifecycle,
  type ViewerGamepadLifecycle,
  type ViewerGamepadLifecycleWindowLike,
} from "../app/gamepadLifecycle.js";

export type ViewerInputProviderId = "keyboard/v1" | "gamepad/v1";
export type ViewerInputProviderSchema =
  | "viewer_keyboard_sample/v1"
  | "viewer_gamepad_sample/v1";

export interface ViewerInputProviderWindowLike extends ViewerGamepadLifecycleWindowLike {
  addEventListener(type: "keydown" | "keyup", listener: (event: ViewerKeyboardEventLike) => void): void;
  removeEventListener(type: "keydown" | "keyup", listener: (event: ViewerKeyboardEventLike) => void): void;
  addEventListener(type: "blur" | "focus", listener: () => void): void;
  removeEventListener(type: "blur" | "focus", listener: () => void): void;
}

export interface ViewerInputProviderDocumentLike {
  readonly activeElement?: unknown;
  visibilityState: string;
  hasFocus(): boolean;
  addEventListener(type: "visibilitychange", listener: () => void): void;
  removeEventListener(type: "visibilitychange", listener: () => void): void;
}

export interface ViewerKeyboardEventLike {
  target?: unknown;
  ctrlKey?: boolean;
  altKey?: boolean;
  metaKey?: boolean;
  code: string;
  repeat: boolean;
  preventDefault(): void;
}

export interface ViewerInputProviderOptions {
  gamepadNeutralHeartbeat?: boolean;
  window: ViewerInputProviderWindowLike;
  document: ViewerInputProviderDocumentLike;
  url: string | null;
  keyboardCapture: ViewerKeyboardCapture;
  getGamepads: () => ArrayLike<ViewerGamepadLike | null | undefined> | null;
  keyboardWebSocketCtor?: ViewerKeyboardControlSocketConstructorLike;
  gamepadWebSocketCtor?: ViewerGamepadControlSocketConstructorLike;
  gamepadSetTimeoutFn?: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>;
  gamepadClearTimeoutFn?: (timeoutId: ReturnType<typeof setTimeout>) => void;
}

export interface ViewerInputProvider {
  readonly id: ViewerInputProviderId;
  readonly rawSampleSchema: ViewerInputProviderSchema;
  start(): void;
  dispose(): void;
}

export interface ViewerInputProviderRegistration {
  readonly id: ViewerInputProviderId;
  readonly rawSampleSchema: ViewerInputProviderSchema;
  create(options: ViewerInputProviderOptions): ViewerInputProvider;
}

/** 編集fieldやUI操作中のキーをrobot入力へ渡さない。 */
export function isInputEditingTarget(target: unknown): boolean {
  if (typeof target !== "object" || target === null) return false;
  const element = target as { tagName?: string; isContentEditable?: boolean; closest?: (selector: string) => unknown };
  return element.isContentEditable === true ||
    ["INPUT", "TEXTAREA", "SELECT", "BUTTON", "SUMMARY", "A"].includes(element.tagName?.toUpperCase() ?? "") ||
    Boolean(element.closest?.('input, textarea, select, button, summary, a[href], [contenteditable="true"], [role="textbox"], [role="slider"], [role="tab"]'));
}

function createKeyboardProvider(options: ViewerInputProviderOptions): ViewerInputProvider {
  let active = false;
  let animationFrameId: number | null = null;
  let sender: ViewerKeyboardControlSender | null = null;
  let editingSuppressed = false;

  const publish = (): void => {
    sender?.publish(options.keyboardCapture.snapshot(), undefined, {
      metadata: {
        intent_kind: "local_endpoint_velocity",
        input_continuity: "continuous",
        source_kind: "viewer_keyboard",
        control_frame: "world",
        local_endpoint_speed_m_s: 0.1,
        local_endpoint_max_delta_m: 0.03,
      },
    });
  };
  const onKeyDown = (event: ViewerKeyboardEventLike): void => {
    if (isInputEditingTarget(event.target) || event.ctrlKey || event.altKey || event.metaKey) {
      editingSuppressed = true;
      if (options.keyboardCapture.handleBlur()) publish();
      return;
    }
    if (!options.keyboardCapture.isBoundKey(event.code)) return;
    if (editingSuppressed && options.document.hasFocus()) {
      editingSuppressed = false;
      options.keyboardCapture.handleFocus();
    }
    event.preventDefault();
    if (options.keyboardCapture.handleKeyDown(event.code, event.repeat)) publish();
  };
  const onKeyUp = (event: ViewerKeyboardEventLike): void => {
    if (!options.keyboardCapture.isBoundKey(event.code)) return;
    if (!options.keyboardCapture.handleKeyUp(event.code)) return;
    event.preventDefault();
    publish();
  };
  const onBlur = (): void => {
    if (options.keyboardCapture.handleBlur()) publish();
  };
  const onFocus = (): void => {
    if (options.keyboardCapture.handleFocus()) publish();
  };
  const onVisibilityChange = (): void => {
    if (options.keyboardCapture.handleVisibilityChange(options.document.visibilityState === "visible")) publish();
  };
  const schedule = (): void => {
    if (!active) return;
    if (isInputEditingTarget(options.document.activeElement)) {
      editingSuppressed = true;
      options.keyboardCapture.handleBlur();
    } else if (editingSuppressed && options.document.hasFocus() && options.document.visibilityState === "visible") {
      editingSuppressed = false;
      options.keyboardCapture.handleFocus();
    }
    publish();
    animationFrameId = options.window.requestAnimationFrame(schedule);
  };
  const dispose = (): void => {
    if (!active) {
      options.keyboardCapture.reset();
      return;
    }
    active = false;
    if (animationFrameId !== null) {
      options.window.cancelAnimationFrame(animationFrameId);
      animationFrameId = null;
    }
    options.window.removeEventListener("keydown", onKeyDown);
    options.window.removeEventListener("keyup", onKeyUp);
    options.window.removeEventListener("blur", onBlur);
    options.window.removeEventListener("focus", onFocus);
    options.document.removeEventListener("visibilitychange", onVisibilityChange);
    sender?.dispose();
    sender = null;
    options.keyboardCapture.reset();
  };

  return {
    id: "keyboard/v1",
    rawSampleSchema: "viewer_keyboard_sample/v1",
    start(): void {
      if (active) return;
      active = true;
      options.keyboardCapture.reset();
      sender = createViewerKeyboardControlSender({
        url: options.url,
        WebSocketCtor: options.keyboardWebSocketCtor,
      });
      publish();
      animationFrameId = options.window.requestAnimationFrame(schedule);
      options.window.addEventListener("keydown", onKeyDown);
      options.window.addEventListener("keyup", onKeyUp);
      options.window.addEventListener("blur", onBlur);
      options.window.addEventListener("focus", onFocus);
      options.document.addEventListener("visibilitychange", onVisibilityChange);
    },
    dispose,
  };
}

function createGamepadProvider(options: ViewerInputProviderOptions): ViewerInputProvider {
  let sender: ViewerGamepadControlSender | null = null;
  let lifecycle: ViewerGamepadLifecycle | null = null;

  return {
    id: "gamepad/v1",
    rawSampleSchema: "viewer_gamepad_sample/v1",
    start(): void {
      if (lifecycle !== null) return;
      sender = createViewerGamepadControlSender({
        url: options.url,
        WebSocketCtor: options.gamepadWebSocketCtor,
        providerSessionId: globalThis.crypto.randomUUID(),
      });
      lifecycle = createViewerGamepadLifecycle({
        window: options.window,
        document: {
          get visibilityState(): "visible" | "hidden" {
            return options.document.visibilityState === "visible" ? "visible" : "hidden";
          },
          hasFocus: () => options.document.hasFocus(),
          addEventListener: (type, listener) => options.document.addEventListener(type, listener),
          removeEventListener: (type, listener) => options.document.removeEventListener(type, listener),
        },
        getGamepads: options.getGamepads,
        neutralHeartbeat: options.gamepadNeutralHeartbeat,
        publish(snapshot) {
          sender?.publish(snapshot);
        },
        setTimeoutFn: options.gamepadSetTimeoutFn,
        clearTimeoutFn: options.gamepadClearTimeoutFn,
      });
      lifecycle.start();
    },
    dispose(): void {
      lifecycle?.dispose();
      lifecycle = null;
      sender?.dispose();
      sender = null;
    },
  };
}

const DEFAULT_PROVIDER_REGISTRATIONS: readonly ViewerInputProviderRegistration[] = [
  {
    id: "keyboard/v1",
    rawSampleSchema: "viewer_keyboard_sample/v1",
    create: createKeyboardProvider,
  },
  {
    id: "gamepad/v1",
    rawSampleSchema: "viewer_gamepad_sample/v1",
    create: createGamepadProvider,
  },
];

/** duplicate provider ID/schemaを拒否するbrowser-side declaration registry。 */
export class ViewerInputProviderRegistry {
  private readonly registrations: ReadonlyMap<ViewerInputProviderId, ViewerInputProviderRegistration>;

  constructor(registrations: readonly ViewerInputProviderRegistration[]) {
    const byId = new Map<ViewerInputProviderId, ViewerInputProviderRegistration>();
    for (const registration of registrations) {
      if (byId.has(registration.id)) {
        throw new Error(`duplicate viewer input provider id: ${registration.id}`);
      }
      byId.set(registration.id, registration);
    }
    this.registrations = byId;
  }

  resolve(id: ViewerInputProviderId): ViewerInputProviderRegistration {
    const registration = this.registrations.get(id);
    if (registration === undefined) {
      throw new Error(`unknown viewer input provider id: ${String(id)}`);
    }
    return registration;
  }

  create(id: ViewerInputProviderId, options: ViewerInputProviderOptions): ViewerInputProvider {
    const provider = this.resolve(id).create(options);
    if (provider.id !== id || provider.rawSampleSchema !== this.resolve(id).rawSampleSchema) {
      throw new Error(`viewer input provider identity mismatch: ${id}`);
    }
    return provider;
  }

  ids(): readonly ViewerInputProviderId[] {
    return [...this.registrations.keys()];
  }
}

/** keyboard/gamepad v1だけを登録し、利用不能providerへのfallbackは追加しない。 */
export function createDefaultViewerInputProviderRegistry(): ViewerInputProviderRegistry {
  return new ViewerInputProviderRegistry(DEFAULT_PROVIDER_REGISTRATIONS);
}
