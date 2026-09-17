"use client";

import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip, type TooltipContentProps } from "recharts";
import type { NameType, ValueType } from "recharts/types/component/DefaultTooltipContent";

import { formatNumber } from "@/lib/format";

export interface DonutSlice {
  key: string;
  label: string;
  value: number;
  color: string;
}

export interface DonutChartProps {
  data: readonly DonutSlice[];
  /** Label under the total in the centre (e.g. "заказов"). */
  totalLabel?: string;
  className?: string;
}

function SliceTooltip({ active, payload }: TooltipContentProps<ValueType, NameType>) {
  if (!active || !payload || payload.length === 0) return null;
  const slice = payload[0]?.payload as DonutSlice | undefined;
  if (!slice) return null;
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-sm">
      <p className="font-semibold text-slate-700">{slice.label}</p>
      <p className="text-slate-600">{formatNumber(slice.value, 0)}</p>
    </div>
  );
}

/**
 * Two-(or more)-slice donut with a centred total and a legend listing each slice's
 * value and share (identity is never colour-alone).
 */
export function DonutChart({ data, totalLabel = "всего", className }: DonutChartProps) {
  const total = data.reduce((sum, slice) => sum + slice.value, 0);

  if (total === 0) {
    return <p className="py-8 text-center text-sm text-slate-500">Нет данных за период</p>;
  }

  return (
    <div className={className ?? "flex flex-col items-center gap-4 sm:flex-row sm:items-center sm:justify-around"}>
      <div className="relative h-48 w-48 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data as DonutSlice[]}
              dataKey="value"
              nameKey="label"
              innerRadius={58}
              outerRadius={84}
              paddingAngle={data.length > 1 ? 2 : 0}
              cornerRadius={4}
              stroke="none"
            >
              {data.map((slice) => (
                <Cell key={slice.key} fill={slice.color} />
              ))}
            </Pie>
            <Tooltip content={SliceTooltip} />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-2xl font-semibold tabular-nums text-slate-900">{formatNumber(total, 0)}</span>
          <span className="text-xs text-slate-500">{totalLabel}</span>
        </div>
      </div>
      <ul className="flex flex-col gap-2">
        {data.map((slice) => {
          const share = total > 0 ? Math.round((slice.value / total) * 100) : 0;
          return (
            <li key={slice.key} className="flex items-center gap-2 text-sm">
              <span aria-hidden className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: slice.color }} />
              <span className="text-slate-700">{slice.label}</span>
              <span className="tabular-nums font-medium text-slate-900">{formatNumber(slice.value, 0)}</span>
              <span className="text-slate-400">({share}%)</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
