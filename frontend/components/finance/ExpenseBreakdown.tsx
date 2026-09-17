import { MoneyText, SectionCard } from "@/components/shared";
import { formatNumber } from "@/lib/format";
import { EXPENSE_CATEGORY_LABELS } from "@/lib/labels";
import type { ProfitAndLossStats } from "@/types/api";

export interface ExpenseBreakdownProps {
  pnl: ProfitAndLossStats | undefined;
  loading?: boolean;
  className?: string;
}

/** Share of every expense category in the period total, largest first (the API already sorts). */
export function ExpenseBreakdown({ pnl, loading = false, className }: ExpenseBreakdownProps) {
  const total = pnl?.expenses ?? 0;
  const items = pnl?.expenses_by_category ?? [];

  return (
    <SectionCard
      title="Расходы по категориям"
      loading={loading}
      empty={!loading && items.length === 0}
      emptyTitle="Расходов за период нет"
      className={className}
    >
      <ul className="flex flex-col gap-3">
        {items.map((item) => {
          const share = total > 0 ? (item.amount / total) * 100 : 0;
          return (
            <li key={item.category} className="flex flex-col gap-1.5">
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="min-w-0 truncate text-slate-700">{EXPENSE_CATEGORY_LABELS[item.category]}</span>
                <span className="flex shrink-0 items-baseline gap-2">
                  <MoneyText value={item.amount} strong />
                  <span className="w-12 text-right text-xs text-slate-500 tabular-nums">{formatNumber(share, 0)} %</span>
                </span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-100" aria-hidden>
                <div className="h-full rounded-full bg-slate-400" style={{ width: `${Math.max(share, 1)}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
    </SectionCard>
  );
}
