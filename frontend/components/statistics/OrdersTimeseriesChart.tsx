"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";
import type { NameType, ValueType } from "recharts/types/component/DefaultTooltipContent";

import { formatDate, formatNumber } from "@/lib/format";
import type { TimeseriesPoint } from "@/types/api";

const ORDERS_COLOR = "#0284c7";
const GRID_COLOR = "#e2e8f0";
const AXIS_TICK_COLOR = "#64748b";

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
      <p className="text-slate-600">
        Заказов: <span className="font-medium text-slate-900">{formatNumber(point.orders_count, 0)}</span>
      </p>
    </div>
  );
}

/** Number of orders per day over the selected period. Single series — no legend needed. */
export function OrdersTimeseriesChart({ data }: { data: readonly TimeseriesPoint[] }) {
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
            allowDecimals={false}
            tickFormatter={(value: number) => formatNumber(value, 0)}
            tick={{ fontSize: 11, fill: AXIS_TICK_COLOR }}
            axisLine={false}
            tickLine={false}
            width={40}
          />
          <Tooltip content={ChartTooltip} cursor={{ fill: "#f1f5f9" }} />
          <Bar dataKey="orders_count" name="Заказов" fill={ORDERS_COLOR} radius={[4, 4, 0, 0]} maxBarSize={36} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
