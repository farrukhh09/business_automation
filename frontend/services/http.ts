/**
 * fetch wrapper for the backend API (07-frontend.md §2–3).
 *
 * - All requests go to the relative `/api/...` (proxied by Next.js rewrites).
 * - Access token lives in memory only; refresh token in localStorage (`bakery.refresh`).
 * - On 401: one de-duplicated refresh (`POST /auth/refresh`, token rotation) and a single retry.
 *   If refresh is rejected → tokens are cleared and the auth-failure handler runs (logout → /login).
 * - Errors are normalised to `ApiError { status, code, detail }`.
 */

import type { ApiErrorBody, TokenPair, ValidationErrorItem } from "@/types/api";

export const API_PREFIX = "/api";
export const REFRESH_TOKEN_STORAGE_KEY = "bakery.refresh";

/* ------------------------------------------------------------------ */
/* ApiError                                                            */
/* ------------------------------------------------------------------ */

/**
 * Fallback codes when the body carries no `code`. 5xx codes such as `integration_error` /
 * `integration_not_configured` are only taken from the backend body — a bare 502/503 may come
 * from a reverse proxy and must not be mistaken for an integration problem.
 */
const DEFAULT_CODES: Record<number, string> = {
  400: "bad_request",
  401: "not_authenticated",
  403: "forbidden",
  404: "not_found",
  409: "conflict",
  410: "gone",
  422: "validation_error",
  429: "rate_limited",
};

const DEFAULT_MESSAGES: Record<number, string> = {
  0: "Не удалось связаться с сервером. Проверьте подключение и повторите попытку.",
  400: "Некорректный запрос.",
  401: "Требуется вход в систему.",
  403: "Недостаточно прав для этого действия.",
  404: "Не найдено.",
  409: "Конфликт данных. Обновите страницу и повторите.",
  410: "Срок действия ссылки истёк.",
  422: "Проверьте правильность заполнения полей.",
  429: "Слишком много запросов. Подождите немного и повторите.",
  500: "Внутренняя ошибка сервера.",
  502: "Сервер временно недоступен. Повторите попытку позже.",
  503: "Сервис временно недоступен. Повторите попытку позже.",
  504: "Сервер не ответил вовремя. Повторите попытку позже.",
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;
  /** Raw FastAPI 422 items, if any. */
  readonly validationErrors: ValidationErrorItem[];
  /** Raw error body, if it was JSON. */
  readonly body: ApiErrorBody | null;

  constructor(
    status: number,
    code: string,
    detail: string,
    options: { validationErrors?: ValidationErrorItem[]; body?: ApiErrorBody | null } = {},
  ) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.validationErrors = options.validationErrors ?? [];
    this.body = options.body ?? null;
  }

  /** Map of field name → message built from 422 validation errors. */
  get fieldErrors(): Record<string, string> {
    const result: Record<string, string> = {};
    for (const item of this.validationErrors) {
      const path = item.loc.filter((part) => part !== "body" && part !== "query" && part !== "path");
      const key = path.join(".");
      if (key && !(key in result)) result[key] = item.msg;
    }
    return result;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Human-readable (Russian) message for any thrown value. */
export function getErrorMessage(error: unknown, fallback = "Произошла ошибка. Повторите попытку."): string {
  if (error instanceof ApiError) return error.detail || fallback;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

function isValidationItems(value: unknown): value is ValidationErrorItem[] {
  return (
    Array.isArray(value) &&
    value.every((item) => typeof item === "object" && item !== null && "msg" in item && "loc" in item)
  );
}

function formatValidationItems(items: ValidationErrorItem[]): string {
  const messages = items.map((item) => {
    const field = item.loc.filter((part) => part !== "body" && part !== "query" && part !== "path").join(".");
    return field ? `${field}: ${item.msg}` : item.msg;
  });
  return messages.join("; ");
}

function buildApiError(status: number, payload: unknown): ApiError {
  const body = typeof payload === "object" && payload !== null ? (payload as ApiErrorBody) : null;
  const rawDetail = body?.detail;
  const validationErrors = isValidationItems(rawDetail) ? rawDetail : [];
  let detail: string;
  if (typeof rawDetail === "string" && rawDetail.trim()) {
    detail = rawDetail;
  } else if (validationErrors.length > 0) {
    detail = formatValidationItems(validationErrors);
  } else {
    // Non-JSON bodies (e.g. the Next.js proxy's "Internal Server Error" when the backend is down,
    // an HTML page from a reverse proxy) are not shown: use a Russian message instead.
    detail = DEFAULT_MESSAGES[status] ?? (status >= 500 ? DEFAULT_MESSAGES[500] : "Ошибка запроса.");
  }
  const code = (typeof body?.code === "string" && body.code) || DEFAULT_CODES[status] || (status >= 500 ? "server_error" : "error");
  return new ApiError(status, code, detail, { validationErrors, body });
}

/* ------------------------------------------------------------------ */
/* Token store                                                         */
/* ------------------------------------------------------------------ */

let accessToken: string | null = null;

function readStoredRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredRefreshToken(token: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (token) window.localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, token);
    else window.localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
  } catch {
    /* storage unavailable (private mode) — keep the in-memory session only */
  }
}

export const tokenStore = {
  getAccessToken: (): string | null => accessToken,
  getRefreshToken: readStoredRefreshToken,
  /** Stores a fresh token pair (after login or refresh). */
  setTokens(pair: Pick<TokenPair, "access_token" | "refresh_token">): void {
    accessToken = pair.access_token;
    writeStoredRefreshToken(pair.refresh_token);
  },
  clear(): void {
    accessToken = null;
    writeStoredRefreshToken(null);
  },
};

/* ------------------------------------------------------------------ */
/* Auth handlers (registered by AuthProvider)                          */
/* ------------------------------------------------------------------ */

export interface AuthHandlers {
  /** Called after every successful token refresh (user data may have changed). */
  onTokensRefreshed?: (pair: TokenPair) => void;
  /** Called when the session cannot be restored (refresh rejected). */
  onAuthFailure?: () => void;
}

let authHandlers: AuthHandlers = {};

export function setAuthHandlers(handlers: AuthHandlers): () => void {
  authHandlers = handlers;
  return () => {
    if (authHandlers === handlers) authHandlers = {};
  };
}

/* ------------------------------------------------------------------ */
/* Query string                                                        */
/* ------------------------------------------------------------------ */

export type QueryPrimitive = string | number | boolean;
export type QueryValue = QueryPrimitive | null | undefined | readonly (QueryPrimitive | null | undefined)[];
export type QueryParams = Record<string, QueryValue>;

/**
 * Builds `?a=1&status=NEW&status=CONFIRMED`. Arrays become repeated params;
 * `undefined`, `null` and empty strings are skipped.
 */
export function buildQuery(params?: object | null): string {
  if (!params) return "";
  const search = new URLSearchParams();
  const append = (key: string, value: unknown) => {
    if (value === undefined || value === null || value === "") return;
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      search.append(key, String(value));
    }
  };
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) value.forEach((item) => append(key, item));
    else append(key, value);
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

/* ------------------------------------------------------------------ */
/* Refresh (de-duplicated)                                             */
/* ------------------------------------------------------------------ */

let refreshInFlight: Promise<TokenPair | null> | null = null;

/** Max refresh attempts per call: the second one only happens when another tab rotated the token. */
const MAX_REFRESH_ATTEMPTS = 2;

async function performRefresh(): Promise<TokenPair | null> {
  for (let attempt = 0; attempt < MAX_REFRESH_ATTEMPTS; attempt += 1) {
    const refreshToken = readStoredRefreshToken();
    if (!refreshToken) break;

    let response: Response;
    try {
      response = await fetch(`${API_PREFIX}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
        cache: "no-store",
        credentials: "same-origin",
      });
    } catch {
      // Network problem: keep the stored refresh token, the session may still be valid.
      throw new ApiError(0, "network_error", DEFAULT_MESSAGES[0]);
    }

    const payload = await parseBody(response);

    if (response.ok) {
      const pair = payload as TokenPair;
      tokenStore.setTokens(pair);
      authHandlers.onTokensRefreshed?.(pair);
      return pair;
    }

    if (response.status === 429 || response.status >= 500) {
      // Temporary failure — do not destroy the session.
      throw buildApiError(response.status, payload);
    }

    // Refresh token rejected (revoked / expired / unknown user).
    const current = readStoredRefreshToken();
    if (current && current !== refreshToken) {
      // Another tab rotated the token while this request was in flight: try once with the new one.
      continue;
    }
    if (current === refreshToken) writeStoredRefreshToken(null);
    break;
  }

  accessToken = null;
  return null;
}

/**
 * Restores/extends the session using the stored refresh token.
 * Concurrent callers share one request (important: refresh tokens are rotated).
 * Resolves `null` when there is no valid session; rejects with `ApiError` on
 * network/server errors.
 */
export function refreshSession(): Promise<TokenPair | null> {
  if (!refreshInFlight) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

/* ------------------------------------------------------------------ */
/* Request                                                             */
/* ------------------------------------------------------------------ */

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface RequestOptions {
  /** Query params; arrays → repeated params. */
  query?: object | null;
  /** JSON body (or FormData / Blob / URLSearchParams / string, sent as-is). */
  body?: unknown;
  /** Attach the Bearer token and refresh on 401. Default `true`. */
  auth?: boolean;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204 || response.status === 205 || response.headers.get("content-length") === "0") {
    return undefined;
  }
  const contentType = response.headers.get("content-type") ?? "";
  let text: string;
  try {
    text = await response.text();
  } catch {
    return undefined;
  }
  if (!text) return undefined;
  if (contentType.includes("json")) {
    try {
      return JSON.parse(text) as unknown;
    } catch {
      return text;
    }
  }
  return text;
}

function isRawBody(body: unknown): body is BodyInit {
  return (
    typeof body === "string" ||
    (typeof FormData !== "undefined" && body instanceof FormData) ||
    (typeof Blob !== "undefined" && body instanceof Blob) ||
    (typeof URLSearchParams !== "undefined" && body instanceof URLSearchParams) ||
    body instanceof ArrayBuffer
  );
}

/**
 * Never auto-refresh these. `/auth/logout` is handled by AuthProvider itself: an automatic
 * refresh would rotate the refresh token and the retried logout would revoke the old (already
 * revoked) one, leaving the new token alive on the server.
 */
const NO_REFRESH_PATHS = ["/auth/login", "/auth/refresh", "/auth/logout"];

export async function request<T>(method: HttpMethod, path: string, options: RequestOptions = {}): Promise<T> {
  const { query, body, auth = true, signal, headers: extraHeaders } = options;
  const url = `${API_PREFIX}${path.startsWith("/") ? path : `/${path}`}${buildQuery(query)}`;

  const send = async (token: string | null): Promise<Response> => {
    const headers: Record<string, string> = { Accept: "application/json", ...extraHeaders };
    let payload: BodyInit | undefined;
    if (body !== undefined) {
      if (isRawBody(body)) {
        payload = body;
      } else {
        headers["Content-Type"] = "application/json";
        payload = JSON.stringify(body);
      }
    }
    if (auth && token) headers.Authorization = `Bearer ${token}`;
    try {
      return await fetch(url, {
        method,
        headers,
        body: payload,
        signal,
        cache: "no-store",
        credentials: "same-origin",
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new ApiError(0, "network_error", DEFAULT_MESSAGES[0]);
    }
  };

  const tokenUsed = auth ? accessToken : null;
  let response = await send(tokenUsed);

  if (response.status === 401 && auth && !NO_REFRESH_PATHS.includes(path)) {
    let retryToken: string | null = null;
    if (accessToken && accessToken !== tokenUsed) {
      // Someone already refreshed while this request was in flight.
      retryToken = accessToken;
    } else {
      const pair = await refreshSession();
      retryToken = pair?.access_token ?? null;
    }

    if (retryToken) {
      // Exactly one retry; a second 401 means the session itself is no longer valid.
      response = await send(retryToken);
      if (response.status === 401) {
        tokenStore.clear();
        authHandlers.onAuthFailure?.();
      }
    } else {
      // performRefresh() already dropped the access token and removed the stored refresh token
      // only if it was unchanged — do not wipe a token another tab has just rotated in.
      accessToken = null;
      authHandlers.onAuthFailure?.();
    }
  }

  const payload = await parseBody(response);
  if (!response.ok) throw buildApiError(response.status, payload);
  return payload as T;
}

export const http = {
  get: <T>(path: string, query?: object | null, options?: Omit<RequestOptions, "query" | "body">) =>
    request<T>("GET", path, { ...options, query }),
  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "body">) =>
    request<T>("POST", path, { ...options, body }),
  put: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "body">) =>
    request<T>("PUT", path, { ...options, body }),
  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "body">) =>
    request<T>("PATCH", path, { ...options, body }),
  delete: <T = void>(path: string, options?: Omit<RequestOptions, "body">) => request<T>("DELETE", path, options),
};
