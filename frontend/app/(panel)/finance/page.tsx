"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";
import type { NameType, ValueType } from "recharts/types/component/DefaultTooltipContent";

import { MoneyText, PageSuspense, PeriodPicker, SectionCard, usePeriodParams } from "@/components/shared";
import { Card, ErrorState, PageHeader, StatCard } from "@/components/ui";
import { formatDate, formatMoney, formatNumber } from "@/lib/format";
import { DATE_BASIS_LABELS, ORDER_STATUS_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { statisticsApi } from "@/services/api";
import type { OrderStatus, TimeseriesPoint } from "@/types/api";

/** Statuses counted as "valid" for revenue/production (03-business-rules.md §4). */
const VALID_ORDER_STATUSES: OrderStatus[] = [
  "CONFIRMED",
  "PREPARING",
  "READY",
  "HANDED_TO_COURIER",
  "COMPLETED",
];

const REVENUE_COLOR = "#c2410c";
const PAID_COLOR = "#059669";
const GRID_COLOR = "#e2e8f0";
const AXIS_TICK_COLOR = "#64748b";

function shortDate(value: string): string {
  return formatDate(value).slice(0, 5);
}

const SERIES_NAMES: Record<string, string> = {
  revenue: "Выручка",
  paid_amount: "Оплачено",
};

function FinanceChartTooltip({ active, payload, label }: TooltipContentProps<ValueType, NameType>) {
  if (!active || !payload || payload.length === 0 || typeof label !== "string") return null;
  const point = payload[0]?.payload as TimeseriesPoint | undefined;
  if (!point) return null;
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-sm">
      <p className="font-semibold text-slate-700">{formatDate(label)}</p>
      <p className="mt-1 text-slate-600">
        Выручка: <span className="font-medium text-slate-900">{formatMoney(point.revenue)}</span>
      </p>
      <p className="text-slate-600">
        Оплачено: <span className="font-medium text-slate-900">{formatMoney(point.paid_amount)}</span>
      </p>
      <p className="text-slate-600">
        Заказов: <span className="font-medium text-slate-900">{formatNumber(point.orders_count, 0)}</span>
      </p>
    </div>
  );
}

function FinanceTrendChart({ data }: { data: readonly TimeseriesPoint[] }) {
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data as TimeseriesPoint[]} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke={GRID_COLOR} strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            tickFormatter={shortDate}
            tick={{ fontSize: 11, fill: AXIS_TICK_COLOR }}
            axisLine={{ stroke: GRID_COLOR }}
            tickLine={false}
            minTickGap={16}
          />
          <YAxis
            tickFormatter={(value: number) => formatNumber(value, 0)}
            tick={{ fontSize: 11, fill: AXIS_TICK_COLOR }}
            axisLine={false}
            tickLine={false}
            width={64}
          />
          <Tooltip content={FinanceChartTooltip} cursor={{ fill: "#f1f5f9" }} />
          <Legend
            formatter={(value: string) => <span className="text-xs text-slate-600">{SERIES_NAMES[value] ?? value}</span>}
            iconType="circle"
            iconSize={8}
          />
          <Bar dataKey="revenue" name="revenue" fill={REVENUE_COLOR} radius={[4, 4, 0, 0]} maxBarSize={36} />
          <Bar dataKey="paid_amount" name="paid_amount" fill={PAID_COLOR} radius={[4, 4, 0, 0]} maxBarSize={36} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function FinancePageContent() {
  const period = usePeriodParams();

  const statsQuery = useQuery({
    queryKey: queryKeys.statistics.summary(period.params),
    queryFn: () => statisticsApi.get(period.params),
    enabled: period.ready,
  });

  const timeseriesQuery = useQuery({
    queryKey: queryKeys.statistics.timeseries(period.timeseriesParams ?? {}),
    queryFn: () => statisticsApi.timeseries(period.timeseriesParams ?? undefined),
    enabled: period.ready && period.timeseriesParams !== null,
  });

  const finance = statsQuery.data?.finance;
  const loading = statsQuery.isLoading;

  return (
    <>
      <PageHeader title="Финансы" description="Выручка, оплаченные и неоплаченные заказы, средний чек" />
      <div className="flex flex-col gap-6">
        <Card>
          <PeriodPicker value={period.value} onChange={period.setValue} />
        </Card>

        {statsQuery.isError && !finance ? (
          <Card>
            <ErrorState error={statsQuery.error} onRetry={() => statsQuery.refetch()} />
          </Card>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                label="Общая выручка"
                value={<MoneyText value={finance?.revenue ?? 0} strong />}
                tone="brand"
                loading={loading}
              />
              <StatCard label="Количество заказов" value={formatNumber(finance?.orders_count ?? 0, 0)} loading={loading} />
              <StatCard
                label="Оплаченные заказы"
                value={formatNumber(finance?.paid_orders_count ?? 0, 0)}
                tone="green"
                loading={loading}
              />
              <StatCard
                label="Частично оплаченные"
                value={formatNumber(finance?.partially_paid_orders_count ?? 0, 0)}
                tone="amber"
                loading={loading}
              />
              <StatCard
                label="Неоплаченные"
                value={formatNumber(finance?.unpaid_orders_count ?? 0, 0)}
                tone="red"
                loading={loading}
              />
              <StatCard
                label="Оплачено"
                value={<MoneyText value={finance?.paid_amount ?? 0} strong tone="success" />}
                tone="green"
                loading={loading}
              />
              <StatCard
                label="Не оплачено"
                value={<MoneyText value={finance?.unpaid_amount ?? 0} strong tone="danger" />}
                tone="red"
                loading={loading}
              />
              <StatCard
                label="Средний чек"
                value={<MoneyText value={finance?.average_check ?? 0} strong />}
                tone="blue"
                loading={loading}
              />
            </div>

            <SectionCard
              title="Выручка и оплата по дням"
              loading={timeseriesQuery.isLoading}
              error={timeseriesQuery.error}
              onRetry={() => timeseriesQuery.refetch()}
              retrying={timeseriesQuery.isFetching}
              empty={!timeseriesQuery.isLoading && (timeseriesQuery.data?.length ?? 0) === 0}
              emptyTitle="Нет данных за период"
            >
              <FinanceTrendChart data={timeseriesQuery.data ?? []} />
            </SectionCard>
          </>
        )}

        <p className="text-sm text-slate-500">
          Учитываются заказы в статусах: {VALID_ORDER_STATUSES.map((status) => ORDER_STATUS_LABELS[status]).join(", ")}.
          {" "}
          База даты периода: <span className="font-medium text-slate-700">{DATE_BASIS_LABELS[period.value.date_basis]}</span>.
        </p>
      </div>
    </>
  );
}

export default function FinancePage() {
  return (
    <PageSuspense>
      <FinancePageContent />
    </PageSuspense>
  );
}
