"use client";

/**
 * Filter / pagination state stored in the URL search params, so views are shareable and survive reload.
 *
 * ```tsx
 * // module level (stable identities!)
 * const ORDER_FILTERS = defineUrlState({
 *   status: urlParam.enumArray(ORDER_STATUSES),     // ?status=NEW&status=CONFIRMED
 *   payment_status: urlParam.enum(PAYMENT_STATUSES), // PaymentStatus | ""
 *   date_from: urlParam.date(),                     // "YYYY-MM-DD" | ""
 *   search: urlParam.string(),
 *   page: urlParam.page(),
 * });
 *
 * // inside a client component rendered under <Suspense> (useSearchParams requirement)
 * const { state, setState, setPage, reset, activeCount } = useUrlState(ORDER_FILTERS.defaults, ORDER_FILTERS);
 * setState({ payment_status: "PAID" }); // → page is reset to 1
 * ```
 *
 * - Updates use `router.replace(url, { scroll: false })` (no history spam, no scroll jump).
 * - Values equal to the defaults are omitted from the URL; params not managed by the hook are preserved.
 * - The new state is visible immediately (optimistic), even before the router commits the navigation,
 *   and several hooks on one page (e.g. `usePeriodParams` + page filters) compose their updates.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";

import { addDays, businessToday } from "@/lib/format";
import type { ISODate, StatisticsPeriod } from "@/types/api";

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

type ParamScalar = string | number | boolean | null | undefined;

/** A serialized URL value: scalar or array (→ repeated params). `null`/`undefined`/`""`/`[]` = omitted. */
export type UrlParamValue = ParamScalar | readonly ParamScalar[];

/** Result of `serialize`: URL key → value. */
export type UrlParamRecord = Record<string, UrlParamValue>;

export interface UrlStateCodec<T> {
  /** URL → state. Missing/invalid values must fall back to `defaults`. */
  parse: (params: URLSearchParams, defaults: T) => T;
  /**
   * State → URL params. Return every key you manage; values equal to the defaults should be
   * `undefined` so they are removed from the URL.
   */
  serialize: (state: T, defaults: T) => UrlParamRecord;
}

export interface UrlStateOptions<T> extends Partial<UrlStateCodec<T>> {
  /**
   * State key holding the page number. It is reset to its default when any other key changes.
   * Default: `"page"` if `defaults` has it; `null` disables the behaviour.
   */
  pageKey?: Extract<keyof T, string> | null;
  /** Extra URL params (not managed by this hook) deleted whenever a non-page value changes, e.g. `["page"]`. */
  resetKeys?: readonly string[];
  /** State keys that are not "filters": excluded from `activeCount` / `isDirty` (e.g. `sort`, `page_size`). */
  nonFilterKeys?: readonly Extract<keyof T, string>[];
  /** `"replace"` (default) or `"push"` a history entry. */
  history?: "replace" | "push";
}

export type UrlStatePatch<T> = Partial<T> | ((current: T) => Partial<T>);

export interface UrlStateApi<T> {
  /** Current state parsed from the URL (optimistically updated). */
  state: T;
  /** Merges a patch. Changing any non-page key resets the page (unless the patch sets the page itself). */
  setState: (patch: UrlStatePatch<T>) => void;
  /** Sets the page number (no-op without a page key). */
  setPage: (page: number) => void;
  /** Resets every managed key to its default (removes them from the URL). */
  reset: () => void;
  /** Number of filter keys that differ from defaults (page and `nonFilterKeys` excluded). */
  activeCount: number;
  /** `activeCount > 0` */
  isDirty: boolean;
  /** Effective query string without `?` (managed and unmanaged params). */
  queryString: string;
}

/* ------------------------------------------------------------------ */
/* Pending navigation store (optimistic URL while router.replace runs) */
/* ------------------------------------------------------------------ */

interface PendingNavigation {
  pathname: string;
  /** Query string the navigation started from. */
  from: string;
  /** Query string being navigated to. */
  search: string;
}

let pendingNavigation: PendingNavigation | null = null;
const pendingListeners = new Set<() => void>();

function setPendingNavigation(next: PendingNavigation | null): void {
  pendingNavigation = next;
  pendingListeners.forEach((listener) => listener());
}

function subscribePending(listener: () => void): () => void {
  pendingListeners.add(listener);
  return () => {
    pendingListeners.delete(listener);
  };
}

const getPendingNavigation = () => pendingNavigation;
const getServerPendingNavigation = () => null;

function normalizeSearch(search: string): string {
  return new URLSearchParams(search).toString();
}

/** Latest query string including a navigation that has not been committed yet. */
function readCurrentSearch(pathname: string): { actual: string; current: string } {
  const actual = normalizeSearch(window.location.search);
  const pending = pendingNavigation;
  if (pending && pending.pathname === pathname && pending.from === actual) {
    return { actual, current: pending.search };
  }
  return { actual, current: actual };
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

function isEmptyParam(value: ParamScalar): boolean {
  return value === null || value === undefined || value === "";
}

function appendParam(params: URLSearchParams, key: string, value: UrlParamValue): void {
  if (Array.isArray(value)) {
    for (const item of value as readonly ParamScalar[]) {
      if (!isEmptyParam(item)) params.append(key, String(item));
    }
    return;
  }
  if (!isEmptyParam(value as ParamScalar)) params.append(key, String(value));
}

function sameValue(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  return JSON.stringify(a) === JSON.stringify(b);
}

function parseBooleanParam(value: string): boolean | undefined {
  const normalized = value.trim().toLowerCase();
  if (normalized === "true" || normalized === "1") return true;
  if (normalized === "false" || normalized === "0") return false;
  return undefined;
}

/** Codec inferred from the default values: string, number, boolean, string[]. */
function inferredParse<T extends object>(params: URLSearchParams, defaults: T): T {
  const result: Record<string, unknown> = { ...(defaults as Record<string, unknown>) };
  for (const [key, fallback] of Object.entries(defaults)) {
    const values = params.getAll(key).filter((value) => value !== "");
    if (values.length === 0) continue;
    if (Array.isArray(fallback)) {
      result[key] = Array.from(new Set(values));
    } else if (typeof fallback === "number") {
      const num = Number(values[0]);
      if (Number.isFinite(num)) result[key] = key === "page" ? Math.max(1, Math.trunc(num)) : num;
    } else if (typeof fallback === "boolean") {
      const bool = parseBooleanParam(values[0]);
      if (bool !== undefined) result[key] = bool;
    } else {
      result[key] = values[0];
    }
  }
  return result as T;
}

function inferredSerialize<T extends object>(state: T, defaults: T): UrlParamRecord {
  const result: UrlParamRecord = {};
  const source = state as Record<string, unknown>;
  for (const [key, fallback] of Object.entries(defaults)) {
    const value = source[key];
    if (sameValue(value, fallback)) {
      result[key] = undefined;
    } else if (Array.isArray(value)) {
      result[key] = value.map((item) => String(item));
    } else if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      result[key] = value;
    } else {
      result[key] = undefined;
    }
  }
  return result;
}

/* ------------------------------------------------------------------ */
/* Hook                                                                */
/* ------------------------------------------------------------------ */

/**
 * Syncs state with URL search params. `defaults` and the codec must have stable identities
 * (define them at module level, e.g. with {@link defineUrlState}).
 *
 * Requires `useSearchParams()`: render the component under `<Suspense>` in statically rendered pages.
 */
export function useUrlState<T extends object>(defaults: T, options: UrlStateOptions<T> = {}): UrlStateApi<T> {
  const router = useRouter();
  const pathname = usePathname() ?? "/";
  const searchParams = useSearchParams();
  const urlSearch = searchParams.toString();

  const parse = options.parse ?? inferredParse;
  const serialize = options.serialize ?? inferredSerialize;
  const pageKey: string | null =
    options.pageKey === undefined ? ("page" in defaults ? "page" : null) : options.pageKey;
  const history = options.history ?? "replace";
  const resetKeys = options.resetKeys;
  const nonFilterKeys = options.nonFilterKeys;

  const pending = useSyncExternalStore(subscribePending, getPendingNavigation, getServerPendingNavigation);
  const effectiveSearch =
    pending && pending.pathname === pathname && pending.from === urlSearch ? pending.search : urlSearch;

  // The router committed the navigation (or the user navigated elsewhere): drop the optimistic override.
  useEffect(() => {
    const current = getPendingNavigation();
    if (current && (current.pathname !== pathname || current.from !== urlSearch)) {
      setPendingNavigation(null);
    }
  }, [pathname, urlSearch]);

  const state = useMemo(() => parse(new URLSearchParams(effectiveSearch), defaults), [parse, effectiveSearch, defaults]);

  const commit = useCallback(
    (compute: (current: T) => { next: T; filtersChanged: boolean }) => {
      const { actual, current: currentSearch } = readCurrentSearch(pathname);
      const params = new URLSearchParams(currentSearch);
      const current = parse(params, defaults);
      const { next, filtersChanged } = compute(current);

      const serializedNext = serialize(next, defaults);
      const managedKeys = new Set<string>([
        ...Object.keys(defaults),
        ...Object.keys(serialize(defaults, defaults)),
        ...Object.keys(serialize(current, defaults)),
        ...Object.keys(serializedNext),
      ]);
      managedKeys.forEach((key) => params.delete(key));
      if (filtersChanged && resetKeys) resetKeys.forEach((key) => params.delete(key));
      for (const [key, value] of Object.entries(serializedNext)) appendParam(params, key, value);
      params.sort();

      const nextSearch = params.toString();
      if (nextSearch === currentSearch) return;
      setPendingNavigation({ pathname, from: actual, search: nextSearch });
      const href = nextSearch ? `${pathname}?${nextSearch}` : pathname;
      if (history === "push") router.push(href, { scroll: false });
      else router.replace(href, { scroll: false });
    },
    [pathname, parse, serialize, defaults, resetKeys, history, router],
  );

  const setState = useCallback(
    (patch: UrlStatePatch<T>) => {
      commit((current) => {
        const partial = (typeof patch === "function" ? patch(current) : patch) as Record<string, unknown>;
        const currentRecord = current as Record<string, unknown>;
        const next: Record<string, unknown> = { ...currentRecord, ...partial };
        const filtersChanged = Object.keys(partial).some(
          (key) => key !== pageKey && !sameValue(partial[key], currentRecord[key]),
        );
        if (pageKey && filtersChanged && !(pageKey in partial)) {
          next[pageKey] = (defaults as Record<string, unknown>)[pageKey];
        }
        return { next: next as T, filtersChanged };
      });
    },
    [commit, pageKey, defaults],
  );

  const setPage = useCallback(
    (page: number) => {
      if (!pageKey) return;
      setState({ [pageKey]: Math.max(1, Math.trunc(page)) } as Partial<T>);
    },
    [pageKey, setState],
  );

  const reset = useCallback(() => {
    commit(() => ({ next: defaults, filtersChanged: true }));
  }, [commit, defaults]);

  const activeCount = useMemo(() => {
    const stateRecord = state as Record<string, unknown>;
    const defaultsRecord = defaults as Record<string, unknown>;
    return Object.keys(defaultsRecord).filter(
      (key) =>
        key !== pageKey &&
        !(nonFilterKeys as readonly string[] | undefined)?.includes(key) &&
        !sameValue(stateRecord[key], defaultsRecord[key]),
    ).length;
  }, [state, defaults, pageKey, nonFilterKeys]);

  return {
    state,
    setState,
    setPage,
    reset,
    activeCount,
    isDirty: activeCount > 0,
    queryString: effectiveSearch,
  };
}

/* ------------------------------------------------------------------ */
/* Schema-based codecs                                                 */
/* ------------------------------------------------------------------ */

/** Codec of one URL key. Methods are declared as methods so codecs of different types fit one schema. */
export interface UrlParamCodec<V> {
  defaultValue: V;
  /** All values of the key (repeated params) → value; `undefined` when missing/invalid (→ default). */
  decode(values: string[]): V | undefined;
  /** Value → URL values; `[]` omits the key. */
  encode(value: V): string[];
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- schema accepts codecs of any value type
export type UrlStateSchema = Record<string, UrlParamCodec<any>>;

export type UrlStateOf<S extends UrlStateSchema> = {
  [K in keyof S]: S[K] extends UrlParamCodec<infer V> ? V : never;
};

export interface UrlStateDefinition<S extends UrlStateSchema> extends UrlStateCodec<UrlStateOf<S>> {
  schema: S;
  defaults: UrlStateOf<S>;
}

/** Builds `{ defaults, parse, serialize }` for {@link useUrlState} from per-key codecs ({@link urlParam}). */
export function defineUrlState<S extends UrlStateSchema>(schema: S): UrlStateDefinition<S> {
  type State = UrlStateOf<S>;
  const defaults = Object.fromEntries(
    Object.entries(schema).map(([key, codec]) => [key, codec.defaultValue]),
  ) as State;

  const parse = (params: URLSearchParams, fallback: State): State => {
    const result: Record<string, unknown> = {};
    for (const [key, codec] of Object.entries(schema)) {
      const values = params.getAll(key).filter((value) => value !== "");
      const decoded = values.length > 0 ? codec.decode(values) : undefined;
      result[key] = decoded === undefined ? (fallback as Record<string, unknown>)[key] : decoded;
    }
    return result as State;
  };

  const serialize = (state: State, fallback: State): UrlParamRecord => {
    const result: UrlParamRecord = {};
    for (const [key, codec] of Object.entries(schema)) {
      const encoded = codec.encode((state as Record<string, unknown>)[key]);
      const encodedDefault = codec.encode((fallback as Record<string, unknown>)[key]);
      result[key] = encoded.length === 0 || sameValue(encoded, encodedDefault) ? undefined : encoded;
    }
    return result;
  };

  return { schema, defaults, parse, serialize };
}

function stringParam(defaultValue = ""): UrlParamCodec<string> {
  return {
    defaultValue,
    decode: (values) => values[0],
    encode: (value) => (value ? [value] : []),
  };
}

function numberParam(
  defaultValue: number,
  { min, max, integer = true }: { min?: number; max?: number; integer?: boolean } = {},
): UrlParamCodec<number> {
  return {
    defaultValue,
    decode: (values) => {
      const num = Number(values[0]);
      if (!Number.isFinite(num)) return undefined;
      const value = integer ? Math.trunc(num) : num;
      if ((min !== undefined && value < min) || (max !== undefined && value > max)) return undefined;
      return value;
    },
    encode: (value) => (Number.isFinite(value) ? [String(value)] : []),
  };
}

/** Positive integer id or `null` (e.g. `customer_id`). */
function idParam(): UrlParamCodec<number | null> {
  return {
    defaultValue: null,
    decode: (values) => {
      const num = Number(values[0]);
      return Number.isInteger(num) && num > 0 ? num : undefined;
    },
    encode: (value) => (value === null || value === undefined ? [] : [String(value)]),
  };
}

function booleanParam(defaultValue = false): UrlParamCodec<boolean> {
  return {
    defaultValue,
    decode: (values) => parseBooleanParam(values[0]),
    encode: (value) => [value ? "true" : "false"],
  };
}

/** Tri-state boolean: `true` / `false` / `null` (not set), e.g. `needs_attention`. */
function optionalBooleanParam(): UrlParamCodec<boolean | null> {
  return {
    defaultValue: null,
    decode: (values) => parseBooleanParam(values[0]),
    encode: (value) => (value === null || value === undefined ? [] : [value ? "true" : "false"]),
  };
}

function enumParam<T extends string>(values: readonly T[]): UrlParamCodec<T | "">;
function enumParam<T extends string>(values: readonly T[], defaultValue: T): UrlParamCodec<T>;
function enumParam<T extends string>(values: readonly T[], defaultValue: T | "" = ""): UrlParamCodec<T | ""> {
  return {
    defaultValue,
    decode: (raw) => ((values as readonly string[]).includes(raw[0]) ? (raw[0] as T) : undefined),
    encode: (value) => (value ? [value] : []),
  };
}

function enumArrayParam<T extends string>(values: readonly T[], defaultValue: T[] = []): UrlParamCodec<T[]> {
  return {
    defaultValue,
    decode: (raw) => {
      const valid = Array.from(new Set(raw)).filter((item): item is T => (values as readonly string[]).includes(item));
      return valid.length > 0 ? valid : undefined;
    },
    encode: (value) => (Array.isArray(value) ? [...value] : []),
  };
}

function stringArrayParam(defaultValue: string[] = []): UrlParamCodec<string[]> {
  return {
    defaultValue,
    decode: (raw) => Array.from(new Set(raw)),
    encode: (value) => (Array.isArray(value) ? [...value] : []),
  };
}

function dateParam(defaultValue: ISODate | "" = ""): UrlParamCodec<ISODate | ""> {
  return {
    defaultValue,
    decode: (values) => (isIsoDate(values[0]) ? values[0] : undefined),
    encode: (value) => (value ? [value] : []),
  };
}

/** Per-key codecs for {@link defineUrlState}. */
export const urlParam = {
  /** Free text; default `""`. */
  string: stringParam,
  /** Number with optional bounds; `integer` default true. */
  number: numberParam,
  /** Page number ≥ 1, default 1. */
  page: () => numberParam(1, { min: 1 }),
  /** Page size 1..100 (04-api §0), default 20. */
  pageSize: (defaultValue = 20) => numberParam(defaultValue, { min: 1, max: 100 }),
  /** Positive integer id or `null`. */
  id: idParam,
  /** `true`/`false`; default `false`. */
  boolean: booleanParam,
  /** `true`/`false`/`null` (not set). */
  optionalBoolean: optionalBooleanParam,
  /** One of `values`; without a default the state type is `T | ""`. */
  enum: enumParam,
  /** Repeated params filtered to `values` (`?status=A&status=B`); default `[]`. */
  enumArray: enumArrayParam,
  /** Repeated free-text params; default `[]`. */
  stringArray: stringArrayParam,
  /** `"YYYY-MM-DD"` (validated) or `""`. */
  date: dateParam,
};

/* ------------------------------------------------------------------ */
/* Date helpers                                                        */
/* ------------------------------------------------------------------ */

const ISO_DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** `true` for a real calendar date in `YYYY-MM-DD` form. */
export function isIsoDate(value: unknown): value is ISODate {
  if (typeof value !== "string") return false;
  const match = ISO_DATE_RE.exec(value);
  if (!match) return false;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return date.toISOString().slice(0, 10) === value;
}

/** Returns the value when it is a valid ISO date, otherwise `undefined` (handy for API params). */
export function isoDateOrUndefined(value: string | null | undefined): ISODate | undefined {
  return isIsoDate(value) ? value : undefined;
}

/** `true` when both dates are valid and `from ≤ to`. */
export function isValidDateRange(from: string | null | undefined, to: string | null | undefined): boolean {
  return isIsoDate(from) && isIsoDate(to) && from <= to;
}

/** Swaps the bounds when `from > to` (either may be empty). */
export function orderedDateRange<A extends string, B extends string>(from: A, to: B): [A | B, A | B] {
  return from && to && (from as string) > (to as string) ? [to, from] : [from, to];
}

/** `"2026-09-15"` → `"2026-09-01"` */
export function startOfMonth(isoDate: ISODate): ISODate {
  return isIsoDate(isoDate) ? `${isoDate.slice(0, 8)}01` : isoDate;
}

export interface DateRange {
  date_from: ISODate;
  date_to: ISODate;
}

/**
 * Concrete inclusive range of a statistics period (03-business-rules §4):
 * today; yesterday; week = last 7 days including today; month = from the 1st to today;
 * custom = `date_from..date_to` (`null` when incomplete or `from > to`).
 */
export function resolvePeriodRange(
  period: StatisticsPeriod,
  custom: { date_from?: string | null; date_to?: string | null } = {},
  today: ISODate = businessToday(),
): DateRange | null {
  switch (period) {
    case "today":
      return { date_from: today, date_to: today };
    case "yesterday": {
      const yesterday = addDays(today, -1);
      return { date_from: yesterday, date_to: yesterday };
    }
    case "week":
      return { date_from: addDays(today, -6), date_to: today };
    case "month":
      return { date_from: startOfMonth(today), date_to: today };
    case "custom":
      return isValidDateRange(custom.date_from, custom.date_to)
        ? { date_from: custom.date_from as ISODate, date_to: custom.date_to as ISODate }
        : null;
    default:
      return null;
  }
}
