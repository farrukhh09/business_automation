"use client";

import clsx from "clsx";

import { ORDER_STATUS_LABELS } from "@/lib/labels";
import { ORDER_STATUSES, type OrderStatus } from "@/types/api";

export interface OrderStatusMultiSelectProps {
  value: readonly OrderStatus[];
  onChange: (value: OrderStatus[]) => void;
  disabled?: boolean;
  className?: string;
}

/** Toggle chips for the order-list status filter (all 8 `OrderStatus` values, Russian labels). */
export function OrderStatusMultiSelect({ value, onChange, disabled = false, className }: OrderStatusMultiSelectProps) {
  const toggle = (status: OrderStatus) => {
    onChange(value.includes(status) ? value.filter((item) => item !== status) : [...value, status]);
  };

  return (
    <div role="group" aria-label="Статус заказа" className={clsx("flex flex-wrap gap-1.5", className)}>
      {ORDER_STATUSES.map((status) => {
        const active = value.includes(status);
        return (
          <button
            key={status}
            type="button"
            aria-pressed={active}
            disabled={disabled}
            onClick={() => toggle(status)}
            className={clsx(
              "rounded-full border px-2.5 py-1 text-xs font-medium whitespace-nowrap transition-colors",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600",
              "disabled:cursor-not-allowed disabled:opacity-50",
              active
                ? "border-brand-600 bg-brand-50 text-brand-700"
                : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50",
            )}
          >
            {ORDER_STATUS_LABELS[status]}
          </button>
        );
      })}
    </div>
  );
}
