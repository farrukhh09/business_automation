"use client";

import clsx from "clsx";

import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { IconReports } from "@/components/ui/icons";
import { Skeleton } from "@/components/ui/Spinner";
import { formatDateLong, formatDateTime } from "@/lib/format";
import type { DailyReportHistoryItem } from "@/types/api";

export interface ReportHistoryListProps {
  items: DailyReportHistoryItem[] | undefined;
  /** Currently displayed report date (`"YYYY-MM-DD"`), highlighted in the list. */
  activeDate?: string;
  onSelect: (date: string) => void;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
}

/** Clickable list of previously generated daily reports (newest first). */
export function ReportHistoryList({ items, activeDate, onSelect, loading, error, onRetry }: ReportHistoryListProps) {
  if (error) return <ErrorState error={error} onRetry={onRetry} className="py-8" />;

  if (loading && !items?.length) {
    return (
      <ul className="flex flex-col gap-2" aria-hidden>
        {Array.from({ length: 6 }, (_, index) => (
          <li key={index}>
            <Skeleton className="h-10 w-full rounded-lg" />
          </li>
        ))}
      </ul>
    );
  }

  if (!items || items.length === 0) {
    return (
      <EmptyState
        icon={<IconReports className="size-6" />}
        title="История пуста"
        description="Отчёты появятся здесь после первой генерации."
        className="py-8"
      />
    );
  }

  return (
    <ul className="flex max-h-96 flex-col gap-1 overflow-y-auto" aria-label="История отчётов">
      {items.map((item) => {
        const active = item.date === activeDate;
        return (
          <li key={item.date}>
            <button
              type="button"
              onClick={() => onSelect(item.date)}
              aria-current={active || undefined}
              className={clsx(
                "flex w-full flex-col gap-0.5 rounded-lg px-3 py-2 text-left transition-colors",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600",
                active ? "bg-brand-50 text-brand-900" : "text-slate-700 hover:bg-slate-50",
              )}
            >
              <span className="text-sm font-medium capitalize">{formatDateLong(item.date)}</span>
              <span className="text-xs text-slate-500">Сформирован {formatDateTime(item.generated_at)}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
