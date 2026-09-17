"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, useState, type ReactNode } from "react";

import { ApiError } from "@/services/http";

/** Never retry client errors (4xx); retry network/server errors up to 2 times. */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 2;
}

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        retry: shouldRetry,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(makeQueryClient);
  return createElement(QueryClientProvider, { client }, children);
}

/**
 * Shared query keys, so feature pages invalidate consistently
 * (e.g. `queryClient.invalidateQueries({ queryKey: queryKeys.orders.all })`).
 *
 * Conventions:
 * - `all` — prefix of every key of a resource (invalidate everything after a mutation);
 * - `lists` / `details` — prefixes of all list / detail variants;
 * - `list(params)` — pass the exact params object sent to the API (React Query hashes it deeply,
 *   `undefined` fields are ignored);
 * - prefix matching is partial for objects too: `detail(id)` also matches `detail(id, { limit: 50 })`.
 * Aliases (`routes`, `get`, top-level `dashboard`, …) return the very same arrays as the original keys.
 */

type Id = number | string;

const statisticsDashboardKey = ["statistics", "dashboard"] as const;
const statisticsTimeseries = (params: object = {}) => ["statistics", "timeseries", params] as const;
const settingsBusinessKey = ["settings", "business"] as const;
const settingsIntegrationsKey = ["settings", "integrations"] as const;
const publicLocationByToken = (token: string) => ["public-location", token] as const;

export const queryKeys = {
  /** GET /auth/me */
  auth: {
    all: ["auth"] as const,
    me: ["auth", "me"] as const,
  },
  /** GET /users (ADMIN) */
  users: {
    all: ["users"] as const,
    list: () => ["users", "list"] as const,
  },
  /** GET /customers, GET /customers/{id} */
  customers: {
    all: ["customers"] as const,
    lists: ["customers", "list"] as const,
    list: (params: object = {}) => ["customers", "list", params] as const,
    details: ["customers", "detail"] as const,
    detail: (id: Id) => ["customers", "detail", String(id)] as const,
  },
  /** GET /products, GET /products/{id} */
  products: {
    all: ["products"] as const,
    lists: ["products", "list"] as const,
    list: (params: object = {}) => ["products", "list", params] as const,
    details: ["products", "detail"] as const,
    detail: (id: Id) => ["products", "detail", String(id)] as const,
  },
  /** GET /orders, GET /orders/{id}, GET /orders/{id}/events */
  orders: {
    all: ["orders"] as const,
    lists: ["orders", "list"] as const,
    list: (params: object = {}) => ["orders", "list", params] as const,
    details: ["orders", "detail"] as const,
    detail: (id: Id) => ["orders", "detail", String(id)] as const,
    eventsAll: ["orders", "events"] as const,
    events: (id: Id) => ["orders", "events", String(id)] as const,
  },
  /** GET /production?date= */
  production: {
    all: ["production"] as const,
    /** `undefined` → server default (today). */
    byDate: (date?: string) => ["production", date ?? "today"] as const,
    /** Alias of `byDate`. */
    get: (date?: string) => ["production", date ?? "today"] as const,
  },
  /** GET /statistics, /statistics/dashboard, /statistics/timeseries */
  statistics: {
    all: ["statistics"] as const,
    /** GET /statistics (StatisticsParams) */
    summary: (params: object = {}) => ["statistics", "summary", params] as const,
    /** Alias of `summary`. */
    get: (params: object = {}) => ["statistics", "summary", params] as const,
    dashboard: statisticsDashboardKey,
    timeseries: statisticsTimeseries,
  },
  /** Alias of `statistics.dashboard` (same key). */
  dashboard: statisticsDashboardKey,
  /** Alias of `statistics.timeseries` (same key). */
  timeseries: statisticsTimeseries,
  /** GET /reports/daily, /reports/daily/history */
  reports: {
    all: ["reports"] as const,
    dailyAll: ["reports", "daily"] as const,
    /** `undefined` → server default date. */
    daily: (date?: string) => ["reports", "daily", date ?? "default"] as const,
    historyAll: ["reports", "history"] as const,
    history: (limit?: number) => ["reports", "history", limit ?? 30] as const,
  },
  /** GET /deliveries, /deliveries/{id}, /deliveries/routes, /deliveries/dispatch-sheet */
  deliveries: {
    all: ["deliveries"] as const,
    lists: ["deliveries", "list"] as const,
    list: (params: object = {}) => ["deliveries", "list", params] as const,
    details: ["deliveries", "detail"] as const,
    detail: (id: Id) => ["deliveries", "detail", String(id)] as const,
    routesAll: ["deliveries", "route"] as const,
    route: (date: string) => ["deliveries", "route", date] as const,
    /** Alias of `route` (GET /deliveries/routes?date=). */
    routes: (date: string) => ["deliveries", "route", date] as const,
    dispatchSheetAll: ["deliveries", "dispatch-sheet"] as const,
    dispatchSheet: (date: string) => ["deliveries", "dispatch-sheet", date] as const,
  },
  /** GET /expenses */
  expenses: {
    all: ["expenses"] as const,
    list: (params: object = {}) => ["expenses", "list", params] as const,
  },
  /** GET /faq */
  faq: {
    all: ["faq"] as const,
    list: (params: object = {}) => ["faq", "list", params] as const,
  },
  /** GET /conversations, /conversations/{id} */
  conversations: {
    all: ["conversations"] as const,
    lists: ["conversations", "list"] as const,
    list: (params: object = {}) => ["conversations", "list", params] as const,
    details: ["conversations", "detail"] as const,
    detail: (id: Id, params: object = {}) => ["conversations", "detail", String(id), params] as const,
  },
  /** GET /test-chat/{key} (dev only) */
  testChat: {
    all: ["test-chat"] as const,
    detail: (key: string) => ["test-chat", key] as const,
  },
  /** GET /settings, /settings/integrations */
  settings: {
    all: ["settings"] as const,
    business: settingsBusinessKey,
    /** Alias of `business`. */
    get: settingsBusinessKey,
    integrations: settingsIntegrationsKey,
  },
  /** Alias of `settings.integrations` (same key). */
  integrations: settingsIntegrationsKey,
  /** GET /public/location/{token} */
  publicLocation: {
    all: ["public-location"] as const,
    byToken: publicLocationByToken,
    /** Alias of `byToken`. */
    get: publicLocationByToken,
  },
  /** GET /health, /health/ready */
  health: {
    all: ["health"] as const,
    status: ["health", "status"] as const,
    ready: ["health", "ready"] as const,
  },
};
