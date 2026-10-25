import type { components } from "./api-types";

export type ProjectRead = components["schemas"]["ProjectRead"];
export type EpisodeRead = components["schemas"]["EpisodeRead"];
export type Character = components["schemas"]["Character"];
export type ProjectCreate = components["schemas"]["ProjectCreate"];
export type EpisodeCreate = components["schemas"]["EpisodeCreate"];

let sessionPromise: Promise<string> | null = null;

async function sessionToken(): Promise<string> {
  sessionPromise ??= fetch("/api/v1/bootstrap", { cache: "no-store" })
    .then(async (response) => {
      if (!response.ok) throw new Error("无法连接本机服务，请检查后重试");
      const value =
        (await response.json()) as components["schemas"]["BootstrapRead"];
      return value.token;
    })
    .catch((error: unknown) => {
      sessionPromise = null;
      throw error;
    });
  return sessionPromise;
}

export async function apiRequest<T>(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};
  const write = method !== "GET" && method !== "HEAD";
  if (write) {
    headers["Content-Type"] = "application/json";
    headers["X-Local-Token"] = await sessionToken();
  }
  options.signal?.throwIfAborted();
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers,
    signal: options.signal,
    cache: "no-store",
    ...(write ? { body: JSON.stringify(options.body ?? {}) } : {}),
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 403) sessionPromise = null;
    let message = "请求未完成，请检查本机服务后重试";
    if (payload && typeof payload === "object" && "error" in payload) {
      const detail = payload.error;
      if (
        detail &&
        typeof detail === "object" &&
        "message" in detail &&
        typeof detail.message === "string"
      ) {
        message = detail.message;
      }
    }
    throw new Error(message);
  }
  return payload as T;
}
