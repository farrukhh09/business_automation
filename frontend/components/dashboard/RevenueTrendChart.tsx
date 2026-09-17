"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";
import type { NameType, ValueType } from "recharts/types/component/DefaultTooltipContent";

import { formatDate, formatMoney, formatNumber } from "@/lib/format";
import type { TimeseriesPoint } from "@/types/api";

const REVENUE_COLOR = "#c2410c";
const GRID_COLOR = "#e2e8f0";
const AXIS_TICK_COLOR = "#64748b";

export interface RevenueTrendChartProps {
  data: readonly TimeseriesPoint[];
  className?: string;
}

function shortDate(value: string): string {
  return formatDate(value).slice(0, 5);
}

function ChartTooltip({ active, payload, label }: TooltipContentProps<ValueType, NameType>) {
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
        Заказов: <span className="font-medium text-slate-900">{formatNumber(point.orders_count, 0)}</span>
      </p>
    </div>
  );
}

/** Area chart of daily revenue (e.g. last 14 days) for the dashboard. Single series — no legend needed. */
export function RevenueTrendChart({ data, className }: RevenueTrendChartProps) {
  return (
    <div className={className ?? "h-56 w-full"}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data as TimeseriesPoint[]} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="dashboardRevenueFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={REVENUE_COLOR} stopOpacity={0.25} />
              <stop offset="100%" stopColor={REVENUE_COLOR} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke={GRID_COLOR} strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            tickFormatter={shortDate}
            tick={{ fontSize: 11, fill: AXIS_TICK_COLOR }}
            axisLine={{ stroke: GRID_COLOR }}
            tickLine={false}
            minTickGap={20}
          />
          <YAxis
            tickFormatter={(value: number) => formatNumber(value, 0)}
            tick={{ fontSize: 11, fill: AXIS_TICK_COLOR }}
            axisLine={false}
            tickLine={false}
            width={56}
          />
          <Tooltip content={ChartTooltip} />
          <Area
            type="monotone"
            dataKey="revenue"
            name="Выручка"
            stroke={REVENUE_COLOR}
            strokeWidth={2}
            fill="url(#dashboardRevenueFill)"
            activeDot={{ r: 4 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
