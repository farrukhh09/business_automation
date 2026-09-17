"use client";

import clsx from "clsx";
import { useCallback, useMemo } from "react";

import { DateInput } from "@/components/ui/DateInput";
import { SegmentedControl } from "@/components/shared/SegmentedControl";
import { formatDate } from "@/lib/format";
import { DATE_BASIS_LABELS, STATISTICS_PERIOD_LABELS } from "@/lib/labels";
import {
  defineUrlState,
  isIsoDate,
  resolvePeriodRange,
  urlParam,
  useUrlState,
  type DateRange,
  type UrlParamRecord,
} from "@/lib/url-state";
import {
  DATE_BASES,
  STATISTICS_PERIODS,
  type DateBasis,
  type StatisticsParams,
  type StatisticsPeriod,
  type TimeseriesParams,
} from "@/types/api";

/* ------------------------------------------------------------------ */
/* Value helpers                                                       */
/* ------------------------------------------------------------------ */

export const DEFAULT_PERIOD: StatisticsPeriod = "today";
/**
 * The pages open on "по дате подтверждения": an owner who just confirmed four orders for the weekend
 * expects to see them under "Сегодня". The API default stays `delivery` (03 §4) — the daily report
 * and production are about the day the orders are handed out.
 */
export const DEFAULT_DATE_BASIS: DateBasis = "created";

/** `StatisticsParams` with `period` and `date_basis` always set. */
export type PeriodValue = StatisticsParams & { period: StatisticsPeriod; date_basis: DateBasis };

/** Fills defaults; drops `date_from`/`date_to` for non-custom periods. */
export function normalizePeriodValue(value: StatisticsParams | null | undefined): PeriodValue {
  const period = value?.period ?? DEFAULT_PERIOD;
  const date_basis = value?.date_basis ?? DEFAULT_DATE_BASIS;
  if (period !== "custom") return { period, date_basis };
  return {
    period,
    date_basis,
    date_from: isIsoDate(value?.date_from) ? value.date_from : undefined,
    date_to: isIsoDate(value?.date_to) ? value.date_to : undefined,
  };
}

/** Validation message for a custom range, or `null` when valid / not custom. */
export function periodValueError(value: StatisticsParams): string | null {
  if (value.period !== "custom") return null;
  if (!value.date_from || !value.date_to) return "Выберите начальную и конечную даты";
  if (value.date_from > value.date_to) return "Начальная дата позже конечной";
  return null;
}

/* ------------------------------------------------------------------ */
/* PeriodPicker                                                        */
/* ------------------------------------------------------------------ */

export interface PeriodPickerProps {
  /** Same shape as `StatisticsParams` (types/api.ts). Missing period/basis → today / delivery. */
  value: StatisticsParams;
  onChange: (value: PeriodValue) => void;
  /** Show the "по дате доставки / по дате подтверждения" toggle (default true). */
  showDateBasis?: boolean;
  /** Show the resolved date range under the controls (default true). */
  showRange?: boolean;
  /** Restrict the preset list (default: all STATISTICS_PERIODS). */
  periods?: readonly StatisticsPeriod[];
  disabled?: boolean;
  className?: string;
}

/**
 * Period selector for Finance / Statistics: Сегодня · Вчера · Неделя · Месяц · Произвольный (+ date inputs)
 * and the date basis toggle. Usually bound to the URL via {@link usePeriodParams}.
 */
export function PeriodPicker({
  value,
  onChange,
  showDateBasis = true,
  showRange = true,
  periods = STATISTICS_PERIODS,
  disabled = false,
  className,
}: PeriodPickerProps) {
  const current = normalizePeriodValue(value);
  const error = periodValueError(current);
  const range = resolvePeriodRange(current.period, current);

  const periodOptions = useMemo(
    () => periods.map((period) => ({ value: period, label: STATISTICS_PERIOD_LABELS[period] })),
    [periods],
  );
  const basisOptions = useMemo(
    () => DATE_BASES.map((basis) => ({ value: basis, label: DATE_BASIS_LABELS[basis] })),
    [],
  );

  const changePeriod = (period: StatisticsPeriod) => {
    if (period === "custom") {
      // Pre-fill the custom range with the range of the previously selected preset.
      const previous = resolvePeriodRange(current.period, current);
      onChange({
        period,
        date_basis: current.date_basis,
        date_from: current.date_from ?? previous?.date_from,
        date_to: current.date_to ?? previous?.date_to,
      });
      return;
    }
    onChange({ period, date_basis: current.date_basis });
  };

  return (
    <div className={clsx("flex flex-col gap-3", className)}>
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
        <SegmentedControl
          ariaLabel="Период"
          options={periodOptions}
          value={current.period}
          onChange={changePeriod}
          disabled={disabled}
        />
        {showDateBasis ? (
          <SegmentedControl
            ariaLabel="База даты"
            size="sm"
            options={basisOptions}
            value={current.date_basis}
            onChange={(date_basis) => onChange({ ...current, date_basis })}
            disabled={disabled}
          />
        ) : null}
      </div>

      {current.period === "custom" ? (
        <div className="grid grid-cols-1 gap-3 sm:max-w-md sm:grid-cols-2">
          <DateInput
            label="С"
            value={current.date_from ?? ""}
            max={current.date_to}
            onValueChange={(date_from) => onChange({ ...current, date_from: date_from || undefined })}
            error={error && current.date_from && current.date_to ? error : undefined}
            disabled={disabled}
          />
          <DateInput
            label="По"
            value={current.date_to ?? ""}
            min={current.date_from}
            onValueChange={(date_to) => onChange({ ...current, date_to: date_to || undefined })}
            disabled={disabled}
          />
        </div>
      ) : null}

      {showRange ? (
        <p className="text-sm text-slate-500" aria-live="polite">
          {range
            ? range.date_from === range.date_to
              ? `За ${formatDate(range.date_from)}`
              : `С ${formatDate(range.date_from)} по ${formatDate(range.date_to)}`
            : (error ?? "")}
          {range ? ` · ${DATE_BASIS_LABELS[current.date_basis].toLowerCase()}` : ""}
        </p>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* usePeriodParams                                                     */
/* ------------------------------------------------------------------ */

const PERIOD_URL_STATE = defineUrlState({
  period: urlParam.enum(STATISTICS_PERIODS, DEFAULT_PERIOD),
  date_from: urlParam.date(),
  date_to: urlParam.date(),
  date_basis: urlParam.enum(DATE_BASES, DEFAULT_DATE_BASIS),
});

type PeriodUrlState = typeof PERIOD_URL_STATE.defaults;

const PERIOD_URL_OPTIONS = {
  parse: PERIOD_URL_STATE.parse,
  serialize: (state: PeriodUrlState, defaults: PeriodUrlState): UrlParamRecord => {
    const record = PERIOD_URL_STATE.serialize(state, defaults);
    if (state.period !== "custom") {
      record.date_from = undefined;
      record.date_to = undefined;
    }
    return record;
  },
  pageKey: null,
  resetKeys: ["page"],
} as const;

export interface PeriodParamsApi {
  /** Normalized value for `<PeriodPicker value>`. */
  value: PeriodValue;
  /** Pass to `<PeriodPicker onChange>`; writes `period`, `date_from`, `date_to`, `date_basis` to the URL. */
  setValue: (value: StatisticsParams) => void;
  /** Params for `statisticsApi.get` (custom → with dates). */
  params: StatisticsParams;
  /** Concrete inclusive range (presets resolved in Asia/Dushanbe); `null` while a custom range is invalid. */
  range: DateRange | null;
  /** Params for `statisticsApi.timeseries`; `null` while the range is invalid. */
  timeseriesParams: TimeseriesParams | null;
  /** `false` while a custom range is incomplete/invalid — use as React Query `enabled`. */
  ready: boolean;
  /** Validation message for the custom range. */
  error: string | null;
  /** Back to today / delivery. */
  reset: () => void;
}

/**
 * Period state in the URL (`?period=custom&date_from=…&date_to=…&date_basis=created`), shareable and
 * reload-safe. Uses `useSearchParams` → render under `<Suspense>`.
 *
 * ```tsx
 * const period = usePeriodParams();
 * const stats = useQuery({
 *   queryKey: queryKeys.statistics.summary(period.params),
 *   queryFn: () => statisticsApi.get(period.params),
 *   enabled: period.ready,
 * });
 * <PeriodPicker value={period.value} onChange={period.setValue} />
 * ```
 */
export function usePeriodParams(): PeriodParamsApi {
  const { state, setState, reset } = useUrlState(PERIOD_URL_STATE.defaults, PERIOD_URL_OPTIONS);

  const value = useMemo(
    () =>
      normalizePeriodValue({
        period: state.period,
        date_basis: state.date_basis,
        date_from: state.date_from || undefined,
        date_to: state.date_to || undefined,
      }),
    [state.period, state.date_basis, state.date_from, state.date_to],
  );

  const setValue = useCallback(
    (next: StatisticsParams) => {
      const normalized = normalizePeriodValue(next);
      setState({
        period: normalized.period,
        date_basis: normalized.date_basis,
        date_from: normalized.date_from ?? "",
        date_to: normalized.date_to ?? "",
      });
    },
    [setState],
  );

  return useMemo(() => {
    const error = periodValueError(value);
    const range = resolvePeriodRange(value.period, value);
    const ready = error === null && range !== null;
    return {
      value,
      setValue,
      params: value,
      range,
      timeseriesParams: range ? { ...range, date_basis: value.date_basis } : null,
      ready,
      error,
      reset,
    };
  }, [value, setValue, reset]);
}
