/** async初期化中の資源を、操作完了前に破棄しないための最小lifecycle。 */
export interface AsyncResourceLifetime {
  readonly disposeRequested: boolean;
  run<T>(operation: () => Promise<T>): Promise<T>;
  requestDispose(): void;
}

export function createAsyncResourceLifetime(release: () => void): AsyncResourceLifetime {
  if (typeof release !== "function") throw new TypeError("release callback is required");
  let activeOperations = 0;
  let disposeRequested = false;
  let released = false;

  const releaseIfReady = (): void => {
    if (!released && disposeRequested && activeOperations === 0) {
      released = true;
      release();
    }
  };

  return {
    get disposeRequested() { return disposeRequested; },
    async run<T>(operation: () => Promise<T>): Promise<T> {
      if (disposeRequested) throw new Error("resource lifetime is disposing");
      activeOperations += 1;
      try {
        return await operation();
      } finally {
        activeOperations -= 1;
        releaseIfReady();
      }
    },
    requestDispose(): void {
      disposeRequested = true;
      releaseIfReady();
    },
  };
}
