"use client";

import { useQuery } from "@tanstack/react-query";

import { SectionCard } from "@/components/shared/SectionCard";
import { Badge } from "@/components/ui/Badge";
import { formatDate, formatDateTime, formatMoney, formatTime } from "@/lib/format";
import {
  ACTOR_TYPE_LABELS,
  ACTOR_TYPE_TONES,
  DELIVERY_TYPE_LABELS,
  labelOf,
  ORDER_EVENT_TYPE_LABELS,
  ORDER_EVENT_TYPE_TONES,
  ORDER_FIELD_LABELS,
  ORDER_STATUS_LABELS,
  PAYMENT_METHOD_LABELS,
  PAYMENT_STATUS_LABELS,
  toneOf,
} from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { ordersApi } from "@/services/api";
import type { OrderEventOut } from "@/types/api";

export interface OrderJournalProps {
  orderId: number;
}

/** Enum fields inside `OrderEventOut.changes` rendered with their Russian labels instead of raw values. */
const ENUM_FIELD_LABELS: Record<string, Record<string, string>> = {
  status: ORDER_STATUS_LABELS,
  payment_status: PAYMENT_STATUS_LABELS,
  delivery_type: DELIVERY_TYPE_LABELS,
  payment_method: PAYMENT_METHOD_LABELS,
};

const MONEY_FIELDS = new Set(["paid_amount", "total_amount"]);

function formatChangeValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  const enumLabels = ENUM_FIELD_LABELS[field];
  if (enumLabels && typeof value === "string") return labelOf(enumLabels, value, value);
  if (MONEY_FIELDS.has(field) && (typeof value === "number" || typeof value === "string")) return formatMoney(value);
  if (field === "delivery_date" && typeof value === "string") return formatDate(value);
  if (field === "delivery_time" && typeof value === "string") return formatTime(value);
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function ChangesList({ changes }: { changes: Record<string, unknown> }) {
  const entries = Object.entries(changes ?? {});
  if (entries.length === 0) return null;
  return (
    <ul className="mt-1.5 flex flex-col gap-0.5 text-sm text-slate-600">
      {entries.map(([field, diff]) => {
        const label = labelOf(ORDER_FIELD_LABELS, field, field);
        if (Array.isArray(diff) && diff.length === 2) {
          return (
            <li key={field}>
              <span className="text-slate-500">{label}:</span> {formatChangeValue(field, diff[0])} →{" "}
              <span className="font-medium text-slate-800">{formatChangeValue(field, diff[1])}</span>
            </li>
          );
        }
        return (
          <li key={field}>
            <span className="text-slate-500">{label}:</span> {formatChangeValue(field, diff)}
          </li>
        );
      })}
    </ul>
  );
}

function EventRow({ event }: { event: OrderEventOut }) {
  return (
    <li className="relative flex gap-3 pb-6 last:pb-0">
      <span aria-hidden className="absolute top-1.5 bottom-0 left-[7px] w-px bg-slate-200" />
      <span
        aria-hidden
        className="relative z-10 mt-1.5 size-[15px] shrink-0 rounded-full border-2 border-white bg-brand-500 ring-2 ring-slate-100"
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={toneOf(ORDER_EVENT_TYPE_TONES, event.event_type)}>
            {labelOf(ORDER_EVENT_TYPE_LABELS, event.event_type, event.event_type)}
          </Badge>
          <Badge tone={ACTOR_TYPE_TONES[event.actor_type]}>
            {ACTOR_TYPE_LABELS[event.actor_type]}
            {event.actor_user ? ` · ${event.actor_user.username}` : ""}
          </Badge>
          <span className="text-xs whitespace-nowrap text-slate-400">{formatDateTime(event.created_at)}</span>
        </div>
        <ChangesList changes={event.changes} />
        {event.comment ? <p className="mt-1 text-sm text-slate-600 italic">«{event.comment}»</p> : null}
      </div>
    </li>
  );
}

/** Order change journal (`GET /orders/{id}/events`) as a timeline: actor, event type, diff, comment, timestamp. */
export function OrderJournal({ orderId }: OrderJournalProps) {
  const query = useQuery({
    queryKey: queryKeys.orders.events(orderId),
    queryFn: () => ordersApi.events(orderId),
  });

  const events = query.data ?? [];
  const sorted = [...events].sort((a, b) => b.created_at.localeCompare(a.created_at));

  return (
    <SectionCard
      title="Журнал изменений"
      loading={query.isLoading}
      error={query.error}
      onRetry={() => query.refetch()}
      empty={!query.isLoading && !query.error && sorted.length === 0}
      emptyTitle="Изменений пока нет"
    >
      <ul>
        {sorted.map((event) => (
          <EventRow key={event.id} event={event} />
        ))}
      </ul>
    </SectionCard>
  );
}
