export type ViewerEndpointConfigSource = "query" | "disabled" | "rejected";

export interface ViewerEndpointConfig {
  websocketUrl: string | null;
  source: ViewerEndpointConfigSource;
  error?: string;
}

function normalizeWebSocketUrl(value: string): string | null {
  const trimmed = value.trim();
  if (trimmed === "") {
    return null;
  }

  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "ws:" && parsed.protocol !== "wss:") {
      return null;
    }
    return trimmed;
  } catch {
    return null;
  }
}

/** 退役queryは未指定と区別し、静的Viewerへの暗黙fallbackを許可しない。 */
export function readViewerEndpointConfig(
  locationLike: Pick<Location, "search">,
): ViewerEndpointConfig {
  const query = new URLSearchParams(locationLike.search);
  if (query.has("ws")) {
    return {
      websocketUrl: null,
      source: "rejected",
      error: "旧接続指定 ws は廃止されました。websocketUrl を指定してください。",
    };
  }
  const queryValue = query.get("websocketUrl");
  if (queryValue === null) {
    return { websocketUrl: null, source: "disabled" };
  }
  const websocketUrl = normalizeWebSocketUrl(queryValue);
  if (websocketUrl === null) {
    return { websocketUrl: null, source: "disabled" };
  }
  return { websocketUrl, source: "query" };
}
